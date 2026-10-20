"""Deterministic candidate detection: document lines with numbers, tagged with label hints.

Candidates are the only source of values. The AI interpretation can only point at a candidate
and one of its printed numbers; it can never supply a number of its own.
"""

from app.ai.application.payslip.models import Candidate, DocumentText, Party, Period
from app.ai.application.payslip.text import (
    is_suspicious,
    matches_any,
    normalize_line,
    parse_numbers,
    redact,
    tokens,
)
from app.contracts.common import FieldPath


def _sets(*groups: set[str]) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(g) for g in groups)


LABELS: dict[FieldPath, tuple[frozenset[str], ...]] = {
    FieldPath.BASE_GROSS: _sets(
        {"שכר", "יסוד"},
        {"שכר", "בסיס"},
        {"משכורת", "יסוד"},
        {"משכורת", "בסיס"},
        {"משכורת", "חודשית"},
        {"base", "salary"},
    ),
    FieldPath.COMMISSIONS_GROSS: _sets({"עמלות"}, {"עמלה"}, {"commission"}, {"commissions"}),
    FieldPath.BONUS_GROSS: _sets({"בונוס"}, {"פרמיה"}, {"מענק"}, {"bonus"}),
    FieldPath.PENSION_EMPLOYEE_RATE: _sets(
        {"פנסיה"}, {"תגמולי"}, {"תגמולים"}, {"גמל"}, {"pension"}
    ),
    FieldPath.STUDY_FUND_EMPLOYEE_RATE: _sets({"השתלמות"}, {"study", "fund"}),
    FieldPath.CREDIT_POINTS: _sets({"נקודות", "זיכוי"}, {"נק", "זיכוי"}, {"credit", "points"}),
}
EMPLOYER = _sets({"מעסיק"}, {"מעביד"}, {"employer"})
EMPLOYEE = _sets({"עובד"}, {"employee"})
CUMULATIVE = _sets(
    {"מצטבר"}, {"מצטברים"}, {"מצטברת"}, {"שנתי"}, {"מתחילת"}, {"ytd"}, {"cumulative"}
)
EXCLUDED = _sets(
    {"נטו"},
    {"סהכ"},
    {"net"},
    {"total"},
    {"מס", "הכנסה"},
    {"ביטוח", "לאומי"},
    {"ביטוח", "בריאות"},
    {"שווי"},
    {"זקיפה"},
    {"החזר"},
    {"הוצאות"},
)


def build_candidates(document: DocumentText) -> tuple[Candidate, ...]:
    found: list[Candidate] = []
    for page_number, lines in enumerate(document.pages, start=1):
        for line_index, raw in enumerate(lines):
            line = normalize_line(raw)
            if not line:
                continue
            numbers = parse_numbers(line)
            if not numbers.amounts and not numbers.percents:
                continue
            evidence = redact(line)
            if evidence is None:
                continue
            words = tokens(line)
            employer = matches_any(words, EMPLOYER)
            employee = matches_any(words, EMPLOYEE)
            party = (
                Party.UNKNOWN
                if employer == employee
                else Party.EMPLOYER
                if employer
                else Party.EMPLOYEE
            )
            found.append(
                Candidate(
                    candidate_id=f"c{len(found) + 1}",
                    page=page_number,
                    line_index=line_index,
                    evidence=evidence,
                    amounts=numbers.amounts,
                    percents=numbers.percents,
                    keyword_fields=frozenset(
                        path for path, labels in LABELS.items() if matches_any(words, labels)
                    ),
                    party=party,
                    period=Period.CUMULATIVE if matches_any(words, CUMULATIVE) else Period.MONTHLY,
                    excluded=matches_any(words, EXCLUDED),
                    suspicious=is_suspicious(line),
                )
            )
    return tuple(found)
