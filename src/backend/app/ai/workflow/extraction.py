"""Deterministic validation of model-proposed facts.

The model proposes; this module decides what may be written. A value becomes a `SetChange`
only when it is unambiguous and passes the shared contract validation. Everything else becomes
a clarification. Nothing here guesses: missing is never zero, a rate is normalized exactly
once, and net or unclear salary statements are never mapped to a gross field.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import ValidationError

from app.ai.workflow.models import FactField, FactMode, ProposedFact
from app.contracts.case_inputs import SetChange
from app.contracts.common import ArrangementKind, EntitlementGroup, FieldPath, Propulsion
from app.contracts.profile import VehicleRef


class ClarificationCode(StrEnum):
    ACTUAL_OR_HYPOTHETICAL = "actual_or_hypothetical"
    NET_NOT_GROSS = "net_not_gross"
    SALARY_COMPOSITION = "salary_composition"
    RATE_UNIT = "rate_unit"
    INVALID_VALUE = "invalid_value"
    CONFLICTING_VALUES = "conflicting_values"
    OCCUPATION_UNRESOLVED = "occupation_unresolved"
    VEHICLE_UNRESOLVED = "vehicle_unresolved"


@dataclass(frozen=True)
class Clarification:
    code: ClarificationCode
    field: str | None = None


@dataclass
class ExtractionOutcome:
    actual: list[SetChange] = field(default_factory=list)
    hypothetical: list[SetChange] = field(default_factory=list)
    clarifications: list[Clarification] = field(default_factory=list)
    vehicle_queries: dict[FactMode, str] = field(default_factory=dict)
    """Current-car descriptions that need `resolve_vehicle` before they can be written."""
    occupation_queries: dict[FactMode, str] = field(default_factory=dict)
    """Occupation descriptions that need `resolve_occupation` before they can be written."""
    not_saved: list[str] = field(default_factory=list)
    """Fields the model marked as actual inside a conditional sentence ("ואם השכר שלי השתנה ל-…?").
    They are calculated case-locally and never written to the shared profile (safety net)."""


MONEY_FIELDS = frozenset(
    {
        FieldPath.BASE_GROSS,
        FieldPath.COMMISSIONS_GROSS,
        FieldPath.BONUS_GROSS,
        FieldPath.CURRENT_PARTICIPATION,
        FieldPath.CURRENT_FAMILY_DEDUCTION,
        FieldPath.CURRENT_ALLOWANCE,
    }
)
PAYROLL_MONEY_FIELDS = frozenset(
    {FieldPath.BASE_GROSS, FieldPath.COMMISSIONS_GROSS, FieldPath.BONUS_GROSS}
)
RATE_FIELDS = frozenset({FieldPath.PENSION_EMPLOYEE_RATE, FieldPath.STUDY_FUND_EMPLOYEE_RATE})

_NONE_WORDS = frozenset({"none", "אין", "ללא", "לא", "0"})
_TRUE_WORDS = frozenset({"true", "yes", "כן"})
_FALSE_WORDS = frozenset({"false", "no", "לא"})
_THOUSAND = re.compile(r"^(\d+(?:\.\d+)?)\s*(אלף|k)$", re.IGNORECASE)
_PLAIN_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_CATALOGUE_ID = re.compile(r"^[A-Z]{2,5}-\d{2,4}$")
_GROUP_LETTERS = {group.value: group for group in EntitlementGroup}
_SALARY_FACTS = frozenset(
    {
        FactField.BASE_GROSS,
        FactField.COMMISSIONS_GROSS,
        FactField.BONUS_GROSS,
        FactField.PENSION_EMPLOYEE_RATE,
        FactField.STUDY_FUND_EMPLOYEE_RATE,
        FactField.CREDIT_POINTS,
    }
)
"""Salary facts the conditional safety net guards. Other facts are left to the model: the
current car in "אם אחליף מהקורולה ל-ATTO 3" is a real fact inside a conditional sentence."""
_CONDITIONAL_WORDS = frozenset({"אם", "ואם", "שאם", "נניח", "ונניח", "נגיד", "ונגיד"})
_CONDITIONAL_PREFIXES = ("בהנחה", "ובהנחה", "במקרה", "ובמקרה", "במידה", "ובמידה")
_SENTENCE_END = re.compile(r"[.!?\n]")
_WORD_EDGES = ",;:\"'()־-"


def _in_condition(message: str, quote: str) -> bool:
    """True when the sentence leading up to the quoted fact is conditional ("ואם השכר שלי השתנה
    ל-20000?"). Only the sentence up to the quote counts, so "השכר שלי עלה ל-20000, אם זה משנה"
    or a later "אם אקח ATTO 3…" is not conditional. Without the quote, the whole message counts."""
    if not message:
        return False
    position = message.find(quote) if quote else -1
    head = message if position < 0 else message[: position + len(quote)]
    sentence = _SENTENCE_END.split(head)[-1] if position >= 0 else message
    words = [word.strip(_WORD_EDGES) for word in sentence.split()]
    return any(w in _CONDITIONAL_WORDS or w.startswith(_CONDITIONAL_PREFIXES) for w in words)


def _clean_number(raw: str) -> str:
    text = raw.strip().replace(",", "").replace("₪", "").replace('ש"ח', "").replace("שח", "")
    return text.strip()


def parse_money(raw: str) -> str | None:
    """A non-negative decimal string, or None when the text is not an unambiguous amount."""
    text = _clean_number(raw)
    if text.lower() in _NONE_WORDS:
        return "0"
    thousand = _THOUSAND.match(text)
    try:
        if thousand:
            value = Decimal(thousand.group(1)) * 1000
        elif _PLAIN_NUMBER.match(text):
            value = Decimal(text)
        else:
            return None
    except InvalidOperation:
        return None
    return format(value.normalize(), "f") if value != value.to_integral() else str(int(value))


def parse_rate(raw: str) -> str | None:
    """Percent text -> rate in 0..1, normalized exactly once.

    "6%" or "6" -> "0.06"; "אין" -> "0". A bare value <= 1 without a percent sign (e.g. "0.06")
    is ambiguous (0.06% or 6%?) and returns None so the employee is asked.
    """
    text = raw.strip().replace(" ", "")
    if text.lower() in _NONE_WORDS:
        return "0"
    percent = text.endswith("%")
    number = text.rstrip("%")
    if not _PLAIN_NUMBER.match(number):
        return None
    value = Decimal(number)
    if not percent and value <= 1:
        return None
    rate = value / Decimal(100)
    if rate > 1:
        return None
    return format(rate.normalize(), "f") if rate != 0 else "0"


def _parse_value(path: FieldPath, raw: str) -> VehicleRef | bool | int | str | None:
    text = raw.strip()
    if path in MONEY_FIELDS:
        return parse_money(text)
    if path in RATE_FIELDS:
        return parse_rate(text)
    if path is FieldPath.CREDIT_POINTS:
        number = _clean_number(text)
        return number if _PLAIN_NUMBER.match(number) else None
    if path is FieldPath.ENTITLEMENT_GROUP:
        letter = text.replace("קבוצה", "").replace("'", "").replace("׳", "").strip()
        group = _GROUP_LETTERS.get(letter)
        return group.value if group else None
    if path is FieldPath.QUALIFYING_CHILDREN_COUNT:
        return int(text) if text.isdigit() else None
    if path is FieldPath.CURRENT_KIND:
        return text if text in {kind.value for kind in ArrangementKind} else None
    if path is FieldPath.CURRENT_PROPULSION:
        return text if text in {p.value for p in Propulsion} else None
    if path is FieldPath.CURRENT_FAMILY_DEDUCTION_INCLUDED:
        lowered = text.lower()
        if lowered in _TRUE_WORDS:
            return True
        if lowered in _FALSE_WORDS:
            return False
        return None
    if path is FieldPath.CURRENT_VEHICLE:
        upper = text.upper()
        return VehicleRef(catalogue_id=upper) if _CATALOGUE_ID.match(upper) else None
    return None


def _clarify_value(fact: ProposedFact, path: FieldPath, raw: str) -> Clarification | None:
    if path in PAYROLL_MONEY_FIELDS and "נטו" in fact.quote:
        return Clarification(ClarificationCode.NET_NOT_GROSS, path.value)
    if path in RATE_FIELDS and parse_rate(raw) is None:
        return Clarification(ClarificationCode.RATE_UNIT, path.value)
    return None


def normalize_facts(facts: list[ProposedFact], message: str = "") -> ExtractionOutcome:
    """`message` is the employee's text: an "actual" fact inside a conditional sentence is
    never written to the shared profile; it is calculated case-locally and reported as not
    saved (a safety net for a model misclassification)."""
    outcome = ExtractionOutcome()
    staged: dict[tuple[FactMode, FieldPath], SetChange] = {}
    conflicted: set[tuple[FactMode, FieldPath]] = set()
    for proposed in facts:
        fact = proposed
        if (
            proposed.mode is FactMode.ACTUAL
            and proposed.field in _SALARY_FACTS
            and _in_condition(message, proposed.quote)
        ):
            fact = proposed.model_copy(update={"mode": FactMode.HYPOTHETICAL})
            outcome.not_saved.append(proposed.field.value)
        if fact.field is FactField.SALARY_UNSPECIFIED:
            code = (
                ClarificationCode.NET_NOT_GROSS
                if "נטו" in fact.quote
                else ClarificationCode.SALARY_COMPOSITION
            )
            outcome.clarifications.append(Clarification(code, FieldPath.BASE_GROSS.value))
            continue
        path = FieldPath(fact.field.value)
        if fact.mode is FactMode.AMBIGUOUS:
            outcome.clarifications.append(
                Clarification(ClarificationCode.ACTUAL_OR_HYPOTHETICAL, path.value)
            )
            continue
        if path is FieldPath.OCCUPATION_ID:
            outcome.occupation_queries[fact.mode] = fact.value.strip()
            continue
        early = _clarify_value(fact, path, fact.value)
        if early is not None:
            outcome.clarifications.append(early)
            continue
        value = _parse_value(path, fact.value)
        if value is None:
            if path is FieldPath.CURRENT_VEHICLE:
                outcome.vehicle_queries[fact.mode] = fact.value.strip()
            else:
                outcome.clarifications.append(
                    Clarification(ClarificationCode.INVALID_VALUE, path.value)
                )
            continue
        try:
            change = SetChange(op="set", field=path, value=value)
        except ValidationError:
            outcome.clarifications.append(
                Clarification(ClarificationCode.INVALID_VALUE, path.value)
            )
            continue
        key = (fact.mode, path)
        previous = staged.get(key)
        if previous is not None and previous.value != change.value:
            conflicted.add(key)
        staged[key] = change
    for (mode, path), change in staged.items():
        if (mode, path) in conflicted:
            outcome.clarifications.append(
                Clarification(ClarificationCode.CONFLICTING_VALUES, path.value)
            )
        elif mode is FactMode.ACTUAL:
            outcome.actual.append(change)
        else:
            outcome.hypothetical.append(change)
    return outcome
