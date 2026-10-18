"""Deterministic fact validation and the no-invented-numbers grounding rule."""

import pytest

from app.ai.workflow.extraction import ClarificationCode, normalize_facts, parse_money, parse_rate
from app.ai.workflow.models import FactField, FactMode, ProposedFact
from app.ai.workflow.rendering import render_grounded
from app.contracts.common import FieldPath


def fact(
    field: FactField, value: str, mode: FactMode = FactMode.ACTUAL, quote: str = ""
) -> ProposedFact:
    return ProposedFact(field=field, value=value, mode=mode, quote=quote or value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("18500", "18500"),
        ("18,500", "18500"),
        ("12 אלף", "12000"),
        ("₪350", "350"),
        ("אין", "0"),
        ("none", "0"),
        ("1500.50", "1500.5"),
        ("about 3000", None),
        ("-5", None),
    ],
)
def test_money(raw: str, expected: str | None) -> None:
    assert parse_money(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("6%", "0.06"),
        ("6", "0.06"),
        ("2.5%", "0.025"),
        ("אין", "0"),
        ("0", "0"),
        ("0.06", None),
        ("1", None),
        ("150%", None),
        ("six", None),
    ],
)
def test_rate_is_normalized_exactly_once(raw: str, expected: str | None) -> None:
    assert parse_rate(raw) == expected


def test_clear_facts_become_contract_changes_split_by_mode() -> None:
    outcome = normalize_facts(
        [
            fact(FactField.BASE_GROSS, "18500"),
            fact(FactField.STUDY_FUND_EMPLOYEE_RATE, "אין", quote="אין לי קרן השתלמות"),
            fact(FactField.ENTITLEMENT_GROUP, "ב"),
            fact(FactField.BONUS_GROSS, "2000", FactMode.HYPOTHETICAL),
        ]
    )
    assert {c.field: c.value for c in outcome.actual} == {
        FieldPath.BASE_GROSS: "18500",
        FieldPath.STUDY_FUND_EMPLOYEE_RATE: "0",
        FieldPath.ENTITLEMENT_GROUP: "ב",
    }
    assert [(c.field, c.value) for c in outcome.hypothetical] == [(FieldPath.BONUS_GROSS, "2000")]
    assert outcome.clarifications == []


@pytest.mark.parametrize(
    ("proposed", "code"),
    [
        (
            fact(FactField.BASE_GROSS, "12000", quote="אני מקבל 12 אלף נטו"),
            ClarificationCode.NET_NOT_GROSS,
        ),
        (
            fact(FactField.SALARY_UNSPECIFIED, "18000", quote="המשכורת שלי 18000"),
            ClarificationCode.SALARY_COMPOSITION,
        ),
        (
            fact(FactField.BASE_GROSS, "20000", FactMode.AMBIGUOUS),
            ClarificationCode.ACTUAL_OR_HYPOTHETICAL,
        ),
        (fact(FactField.PENSION_EMPLOYEE_RATE, "0.06"), ClarificationCode.RATE_UNIT),
        (fact(FactField.ENTITLEMENT_GROUP, "ה"), ClarificationCode.INVALID_VALUE),
        (fact(FactField.OCCUPATION_ID, "מפתחת תוכנה"), ClarificationCode.OCCUPATION_UNSUPPORTED),
        (
            fact(FactField.QUALIFYING_CHILDREN_COUNT, "שלושה או ארבעה"),
            ClarificationCode.INVALID_VALUE,
        ),
    ],
)
def test_unclear_facts_are_never_written(proposed: ProposedFact, code: ClarificationCode) -> None:
    outcome = normalize_facts([proposed])
    assert outcome.actual == [] and outcome.hypothetical == []
    assert [c.code for c in outcome.clarifications] == [code]


def test_conflicting_values_for_one_field_need_clarification() -> None:
    outcome = normalize_facts(
        [fact(FactField.BASE_GROSS, "18000"), fact(FactField.BASE_GROSS, "19000")]
    )
    assert outcome.actual == []
    assert outcome.clarifications[0].code is ClarificationCode.CONFLICTING_VALUES


def test_vehicle_description_needs_resolution_but_catalogue_id_does_not() -> None:
    described = normalize_facts([fact(FactField.CURRENT_VEHICLE, "קורולה 2023")])
    assert described.actual == [] and described.vehicle_queries == {FactMode.ACTUAL: "קורולה 2023"}
    direct = normalize_facts([fact(FactField.CURRENT_VEHICLE, "car-09")])
    assert direct.actual[0].value.model_dump(by_alias=True) == {
        "catalogueId": "CAR-09",
        "taxKey": None,
    }


VALUES = {"net:vehicle:CAR-10": "11,693 ₪", "delta:vehicle:CAR-10": "+661 ₪"}


def test_grounded_text_gets_amounts_from_code() -> None:
    text = "ב-ATTO יתקבל נטו של {{net:vehicle:CAR-10}}, כלומר {{delta:vehicle:CAR-10}} לעומת היום."
    assert render_grounded(text, VALUES) == "ב-ATTO יתקבל נטו של 11,693 ₪, כלומר +661 ₪ לעומת היום."


@pytest.mark.parametrize(
    "text",
    [
        "הנטו יהיה 12,000 ₪",  # an invented amount
        "תקבל בערך {{net:vehicle:CAR-10}} ועוד 250 החזר",  # a digit outside placeholders
        "הנטו הוא {{net:vehicle:CAR-99}}",  # unknown placeholder
        "שבר {{net:vehicle:CAR-10}",  # broken placeholder
        "",
    ],
)
def test_ungrounded_text_is_rejected(text: str) -> None:
    assert render_grounded(text, VALUES) is None
