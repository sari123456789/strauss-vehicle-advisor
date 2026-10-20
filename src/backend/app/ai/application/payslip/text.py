"""Deterministic text handling for payslips: Unicode/RTL normalization, Hebrew tokens,
monetary parsing to `Decimal`, and best-effort redaction.

PDF text extraction returns Hebrew in either logical order or visual (reversed) order,
depending on the producing software, and word order inside a line is not reliable. Label
matching therefore works on token *sets* and also accepts letter-reversed tokens.

Redaction here is a damage-reduction layer only. It does NOT guarantee that every identifying
detail is removed and is never a justification for sending real payslips to an external
provider (see docs/proposals/payslip-import.md).
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_BIDI_CONTROLS = dict.fromkeys(
    [0x200E, 0x200F, 0x061C, *range(0x202A, 0x202F), *range(0x2066, 0x206A)], None
)
_HEBREW_POINTS = re.compile(r"[֑-ׇ]")
_SPACES = re.compile(r"[ \t  -​　]+")
_QUOTES = str.maketrans({"׳": "'", "״": '"', "’": "'", "”": '"', "`": "'"})

_DATE = re.compile(r"(?<!\d)\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}(?!\d)|(?<!\d)\d{1,2}/\d{4}(?!\d)")
_NUMBER = re.compile(
    r"(?P<pct_before>%\s?)?"
    r"(?<![\d.,])(?P<lead>-)?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?"
    r"(?P<trail>-)?(?![\d,]|\.\d)"
    r"(?P<pct_after>\s?%)?"
)
# Hebrew and Latin runs are separate words: extraction often glues "שכרBase" together.
_WORD = re.compile(r"[א-ת'\"]+|[A-Za-z'\"]+")
_HEBREW_PREFIXES = ("ה", "ו", "ב", "ל", "מ", "ש")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_DIGITS = re.compile(r"(?<!\d)\d(?:[\s-]?\d){6,}(?!\d)")
_PII_KEYWORD_SETS: tuple[frozenset[str], ...] = tuple(
    frozenset(words)
    for words in (
        {"זהות"},
        {"תז"},
        {"ת", "ז"},
        {"דרכון"},
        {"חשבון"},
        {"בנק"},
        {"סניף"},
        {"כתובת"},
        {"רחוב"},
        {"טלפון"},
        {"נייד"},
        {"שם"},
        {"מספר", "עובד"},
        {"מס", "עובד"},
        {"name"},
        {"address"},
        {"account"},
        {"phone"},
        {"id"},
    )
)
_SUSPICIOUS_PHRASES = (
    "ignore",
    "instruction",
    "prompt",
    "system",
    "assistant",
    "you are",
    "disregard",
    "override",
    "התעלם",
    "הוראות",
    "הנחיות",
    "הוראה",
    "עדכן את",
    "שנה את",
)
MAX_EVIDENCE_CHARS = 120


def normalize_line(text: str) -> str:
    """NFKC, remove bidi controls and Hebrew points, unify quotes and collapse spaces."""
    text = unicodedata.normalize("NFKC", text).translate(_BIDI_CONTROLS)
    text = _HEBREW_POINTS.sub("", text).translate(_QUOTES)
    return _SPACES.sub(" ", text).strip()


def tokens(line: str) -> frozenset[str]:
    """Word tokens with quote marks removed, lower-cased, plus letter-reversed and
    prefix-stripped variants (for visual-order extraction and Hebrew prefixes)."""
    found: set[str] = set()
    for raw in _WORD.findall(line):
        word = raw.replace("'", "").replace('"', "").lower()
        if not word:
            continue
        for variant in (word, word[::-1]):
            found.add(variant)
            if len(variant) > 2 and variant.startswith(_HEBREW_PREFIXES):
                found.add(variant[1:])
    return frozenset(found)


def matches_any(line_tokens: frozenset[str], keyword_sets: Iterable[frozenset[str]]) -> bool:
    return any(words <= line_tokens for words in keyword_sets)


@dataclass(frozen=True, slots=True)
class ParsedNumbers:
    amounts: tuple[Decimal, ...]
    percents: tuple[Decimal, ...]


def parse_numbers(line: str) -> ParsedNumbers:
    """Amounts and percentages printed in the line, as exact `Decimal` values.

    Accepts thousands separators, a leading or trailing minus (common in payslips) and a
    percent sign on either side (RTL extraction may move it). Dates are ignored.
    """
    amounts: list[Decimal] = []
    percents: list[Decimal] = []
    for match in _NUMBER.finditer(_DATE.sub(" ", line)):
        digits = match["int"].replace(",", "")
        if match["frac"]:
            digits = f"{digits}.{match['frac']}"
        try:
            value = Decimal(digits)
        except InvalidOperation:  # pragma: no cover - the pattern only admits digits
            continue
        if match["lead"] or match["trail"]:
            value = -value
        if match["pct_before"] or match["pct_after"]:
            percents.append(value)
        else:
            amounts.append(value)
    return ParsedNumbers(tuple(amounts), tuple(percents))


def is_suspicious(line: str) -> bool:
    lowered = line.lower()
    reversed_words = " ".join(word[::-1] for word in lowered.split())
    return any(p in lowered or p in reversed_words for p in _SUSPICIOUS_PHRASES)


def redact(line: str) -> str | None:
    """Best-effort redaction. Returns None for lines that look like identity, contact or bank
    details (dropped entirely); otherwise masks e-mails and long digit runs and truncates."""
    if matches_any(tokens(line), _PII_KEYWORD_SETS):
        return None
    masked = _LONG_DIGITS.sub("[***]", _EMAIL.sub("[***]", line))
    return masked[:MAX_EVIDENCE_CHARS]
