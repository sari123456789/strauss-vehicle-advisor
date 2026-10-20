"""Representative answers and case facts (B3a): effective periods, the choice of the applicable
fact, and answer invariants. Pure Domain; no I/O.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.business.domain.cases import FactPath
from app.business.domain.errors import InvalidValueError
from app.business.domain.representatives import (
    AnswerApplication,
    AnswerOutcome,
    NotApplicableReason,
    RepresentativeAnswer,
    RepresentativeFact,
    applicable_facts,
    covers,
)
from app.business.domain.rules import EntitlementGroup
from app.business.domain.vehicles import Propulsion

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
DAY = date(2026, 10, 1)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (None, None, True),
        (DAY, None, True),
        (None, DAY, True),
        (DAY + timedelta(days=1), None, False),
        (None, DAY - timedelta(days=1), False),
        (date(2026, 9, 1), date(2026, 12, 31), True),
    ],
)
def test_periods_are_inclusive_and_optional(
    start: date | None, end: date | None, expected: bool
) -> None:
    assert covers(start, end, DAY) is expected


def fact(
    value: EntitlementGroup | Propulsion,
    *,
    at: datetime = NOW,
    start: date | None = None,
    end: date | None = None,
    path: FactPath = FactPath.ENTITLEMENT_GROUP,
) -> RepresentativeFact:
    return RepresentativeFact(
        fact_id=uuid4(),
        case_id=uuid4(),
        request_id=uuid4(),
        path=path,
        value=value,
        answered_at=at,
        effective_from=start,
        effective_to=end,
    )


def test_latest_applicable_fact_wins_and_an_expired_one_never_hides_it() -> None:
    older = fact(EntitlementGroup.BET, at=NOW - timedelta(days=2))
    newer_expired = fact(EntitlementGroup.DALET, at=NOW, end=DAY - timedelta(days=1))
    newest = fact(EntitlementGroup.GIMEL, at=NOW - timedelta(days=1))
    chosen = applicable_facts([newer_expired, older, newest], DAY)
    assert chosen == {FactPath.ENTITLEMENT_GROUP: newest}
    assert applicable_facts([newer_expired], DAY) == {}


def test_a_case_fact_is_only_a_group_or_propulsion_of_the_right_type() -> None:
    assert fact(Propulsion.HYBRID, path=FactPath.CURRENT_PROPULSION).applies_on(DAY)
    with pytest.raises(InvalidValueError):
        fact(Propulsion.HYBRID, path=FactPath.ENTITLEMENT_GROUP)
    with pytest.raises(InvalidValueError):
        fact(EntitlementGroup.BET, path=FactPath.OCCUPATION_ID)
    with pytest.raises(InvalidValueError):
        fact(EntitlementGroup.BET, start=DAY, end=DAY - timedelta(days=1))


def answer(**overrides: object) -> RepresentativeAnswer:
    values: dict[str, object] = {
        "answer_id": uuid4(),
        "request_id": uuid4(),
        "case_id": uuid4(),
        "outcome": AnswerOutcome.ANSWERED,
        "answered_by": uuid4(),
        "answered_at": NOW,
        "operation_id": uuid4(),
        "value": "350.00",
        "unit": "ILS",
        "application": AnswerApplication.APPLIED,
    }
    values.update(overrides)
    return RepresentativeAnswer(**values)  # type: ignore[arg-type]


def test_answer_invariants() -> None:
    answer()
    answer(outcome=AnswerOutcome.CANNOT_DETERMINE, value=None, unit=None, application=None)
    answer(
        application=AnswerApplication.NOT_APPLICABLE,
        application_reason=NotApplicableReason.OUTSIDE_EFFECTIVE_PERIOD,
    )
    for invalid in (
        {"value": None},  # answered without a value
        {"outcome": AnswerOutcome.CANNOT_DETERMINE, "application": None},  # a value
        {"application": None},  # answered without an application
        {"application": AnswerApplication.NOT_APPLICABLE},  # no reason
        {"application_reason": NotApplicableReason.ALTERNATIVE_CHANGED},  # applied + reason
        {"unit": "USD"},
        {"note": "x" * 1001},
        {"answered_at": datetime(2026, 10, 20)},  # naive
        {"effective_from": DAY, "effective_to": DAY - timedelta(days=1)},
    ):
        with pytest.raises(InvalidValueError):
            answer(**invalid)
