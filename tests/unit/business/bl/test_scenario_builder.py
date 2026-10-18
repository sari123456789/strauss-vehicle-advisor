"""AC07 / T11-T13 / T39 / T65: missing, ambiguous and invalid values; hypothetical isolation."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.business.bl.payroll.scenario_builder import (
    build_payroll_facts,
    with_hypothetical_overrides,
)
from app.business.domain.errors import InvalidValueError, OutOfSupportedRangeError
from app.business.domain.payroll import PayrollFacts
from app.business.domain.provenance import FieldStatus, FieldValue
from app.business.domain.results import MissingFact, MissingReason
from app.business.domain.values import to_decimal
from tests.unit.business.bl.helpers import d, rules, salary, usable


def test_complete_salary_builds_payroll_facts() -> None:
    built = build_payroll_facts(salary(), rules().limits)
    assert isinstance(built, PayrollFacts) and built.earned_gross == d("18500")


def test_missing_bonus_is_reported_never_zero() -> None:
    """T12: bonus not mentioned -> missing."""
    built = build_payroll_facts(replace(salary(), bonus_gross=FieldValue()), rules().limits)
    assert built == (MissingFact("payroll.bonusGross", MissingReason.MISSING),)


def test_ambiguous_value_needs_clarification() -> None:
    """T15: an unclear salary statement is not used."""
    unclear = FieldValue[Decimal](value=d("18000"), status=FieldStatus.NEEDS_CLARIFICATION)
    built = build_payroll_facts(replace(salary(), base_gross=unclear), rules().limits)
    assert built == (MissingFact("payroll.baseGross", MissingReason.NEEDS_CLARIFICATION),)


def test_all_missing_fields_are_listed_together() -> None:
    from app.business.domain.employee_profiles import SalaryFacts

    built = build_payroll_facts(SalaryFacts(), rules().limits)
    assert isinstance(built, tuple) and len(built) == 6


def test_explicit_zero_commissions_and_bonus_are_usable() -> None:
    """T13."""
    built = build_payroll_facts(salary(commissions="0", bonus="0"), rules().limits)
    assert isinstance(built, PayrollFacts)


@pytest.mark.parametrize(
    "overrides",
    [
        {"base": "-1"},
        {"pension": "1.5"},
        {"study": "-0.01"},
        {"credits": "-1"},
    ],
)
def test_invalid_values_are_rejected(overrides: dict[str, str]) -> None:
    with pytest.raises(InvalidValueError):
        build_payroll_facts(salary(**overrides), rules().limits)


@pytest.mark.parametrize("overrides", [{"base": "1000000.01"}, {"credits": "100.5"}])
def test_values_above_support_limits_are_rejected_not_clamped(overrides: dict[str, str]) -> None:
    with pytest.raises(OutOfSupportedRangeError):
        build_payroll_facts(salary(**overrides), rules().limits)


@pytest.mark.parametrize("bad", [0.1, 18500.0, True, float("nan"), "NaN", "Infinity", "abc", None])
def test_decimal_parsing_rejects_floats_and_non_finite_values(bad: object) -> None:
    with pytest.raises(InvalidValueError):
        to_decimal(bad)


def test_decimal_parsing_accepts_exact_strings_and_ints() -> None:
    assert to_decimal("0.0104") == Decimal("0.0104")
    assert to_decimal(3500) == Decimal(3500)


def test_hypothetical_override_never_changes_the_actual_facts() -> None:
    """T39 / T67: a 'what if' salary is scenario-local."""
    actual = salary(base="18500")
    scenario = with_hypothetical_overrides(actual, {"base_gross": usable(d("20000"))})
    assert actual.base_gross.value == d("18500")
    assert scenario.base_gross.value == d("20000")
    assert scenario.bonus_gross == actual.bonus_gross


def test_unknown_override_field_is_rejected() -> None:
    with pytest.raises(InvalidValueError):
        with_hypothetical_overrides(salary(), {"net_salary": usable(d("12000"))})
