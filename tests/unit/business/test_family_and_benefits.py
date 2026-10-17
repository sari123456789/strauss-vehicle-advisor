"""AC10 / T54-T57: family-track gap share and current family-car deductions."""

from dataclasses import replace

from app.business.benefits.calculator import alternative_components, baseline_components
from app.domain.arrangements import CurrentAllowanceSpec, CurrentNoCarSpec, WaiverSpec
from app.domain.payroll import ArrangementComponents
from app.domain.provenance import FieldStatus, FieldValue
from app.domain.results import MissingFact, MissingReason
from app.domain.vehicles import Propulsion, Track
from tests.unit.business.helpers import company_car, d, rules, usable, vehicle


def _components(result: object) -> ArrangementComponents:
    assert isinstance(result, ArrangementComponents)
    return result


def test_carnival_for_group_bet_is_gap_plus_ordinary_participation() -> None:
    """T54: F = 0.5 x (6000 - 3700) = 1150, in addition to C = 500."""
    spec = vehicle("CAR-19", "8120", "500", "6000", track=Track.FAMILY, budget="3700")
    parts = _components(alternative_components(spec, rules()))
    assert (parts.family_gap_contribution, parts.ordinary_participation) == (d("1150"), d("500"))


def test_santa_fe_uses_its_own_catalogue_cost() -> None:
    """T55: F = 0.5 x (5600 - 3700) = 950."""
    spec = vehicle("CAR-20", "4700", "500", "5600", track=Track.FAMILY, budget="3700")
    assert _components(alternative_components(spec, rules())).family_gap_contribution == d("950")


def test_cost_below_budget_creates_no_negative_gap_and_no_salary_credit() -> None:
    """T56: unused budget is not paid."""
    spec = vehicle("CAR-19", "8120", "500", "3000", track=Track.FAMILY, budget="3500")
    assert _components(alternative_components(spec, rules())).family_gap_contribution == 0


def test_regular_track_has_no_gap_even_above_budget() -> None:
    spec = vehicle("CAR-14", "4150", "500", "5100", Propulsion.ELECTRIC, budget="3500")
    parts = _components(alternative_components(spec, rules()))
    assert parts.family_gap_contribution == 0 and parts.electric


def test_family_gap_needs_the_group_budget() -> None:
    spec = vehicle("CAR-19", "8120", "500", "6000", track=Track.FAMILY)
    assert alternative_components(spec, rules()) == (
        MissingFact("employee.entitlementGroup", MissingReason.MISSING, "vehicle:CAR-19"),
    )


def test_missing_new_participation_is_never_zero() -> None:
    """T31: missing participation blocks only this alternative."""
    spec = replace(vehicle("CAR-11", "2930", "300", "4350"), participation=FieldValue())
    assert alternative_components(spec, rules()) == (
        MissingFact("alternative.participation", MissingReason.MISSING, "vehicle:CAR-11"),
    )


def test_unavailable_value_of_use_is_never_zero() -> None:
    """T61: a failed/ambiguous tax lookup is not a zero benefit."""
    spec = replace(
        vehicle("CAR-10", "3130", "300", "4100"),
        value_of_use=FieldValue(status=FieldStatus.NEEDS_CLARIFICATION),
    )
    result = alternative_components(spec, rules())
    assert result == (
        MissingFact("alternative.valueOfUse", MissingReason.NEEDS_CLARIFICATION, "vehicle:CAR-10"),
    )


def test_current_family_car_inclusive_total_is_deducted_once() -> None:
    """T57: reported 1,650 confirmed to include the family deduction."""
    spec = replace(
        company_car("8120", "1650", track=Track.FAMILY),
        family_deduction_included_in_participation=usable(True),
    )
    parts = _components(baseline_components(spec, rules()))
    assert (parts.ordinary_participation, parts.family_gap_contribution) == (d("1650"), 0)


def test_current_family_car_separate_deduction_is_added() -> None:
    spec = replace(
        company_car("8120", "500", track=Track.FAMILY),
        family_deduction_included_in_participation=usable(False),
        family_deduction=usable(d("1150")),
    )
    parts = _components(baseline_components(spec, rules()))
    assert (parts.ordinary_participation, parts.family_gap_contribution) == (d("500"), d("1150"))


def test_current_family_car_with_unclear_breakdown_needs_clarification() -> None:
    spec = company_car("8120", "1650", track=Track.FAMILY)
    assert baseline_components(spec, rules()) == (
        MissingFact(
            "currentArrangement.familyDeductionIncludedInParticipation",
            MissingReason.MISSING,
            "current",
        ),
    )


def test_current_car_with_unknown_propulsion_is_not_assumed() -> None:
    """D6: no propulsion inference for a non-catalogue current vehicle."""
    spec = replace(company_car("3000", "300"), propulsion=FieldValue())
    assert baseline_components(spec, rules()) == (
        MissingFact("currentArrangement.propulsion", MissingReason.MISSING, "current"),
    )


def test_allowance_and_no_car_baselines() -> None:
    allowance = _components(baseline_components(CurrentAllowanceSpec(usable(d("1500"))), rules()))
    assert allowance == ArrangementComponents(allowance=d("1500"))
    assert baseline_components(CurrentNoCarSpec(), rules()) == ArrangementComponents()


def test_waiver_requires_a_known_allowance() -> None:
    assert alternative_components(WaiverSpec(allowance=FieldValue()), rules()) == (
        MissingFact("waiver.allowance", MissingReason.MISSING, "waiver"),
    )
