"""BL eligibility policies (pure): group own-or-lower, family track, waiver, A3 and A8."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.business.bl.eligibility.evaluator import evaluate_vehicle, evaluate_waiver_option
from app.business.domain.eligibility import EligibilityFacts, EligibilityReason, EligibilityStatus
from app.business.domain.errors import UnsupportedPolicyError
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.provenance import FieldStatus, FieldValue
from app.business.domain.results import MissingReason
from app.business.domain.rules import EntitlementGroup
from app.business.domain.vehicles import CatalogueVehicle, Propulsion, Track, VehicleTaxKey
from tests.unit.business.bl.helpers import rules, usable

A, B, C, D = (
    EntitlementGroup.ALEF,
    EntitlementGroup.BET,
    EntitlementGroup.GIMEL,
    EntitlementGroup.DALET,
)
KEY = VehicleTaxKey("2026", "2025", "1", "01014", "00003")


def vehicle(group: EntitlementGroup | None, catalogue_id: str = "CAR-10") -> CatalogueVehicle:
    return CatalogueVehicle(
        catalogue_id=catalogue_id,
        track=Track.REGULAR if group is not None else Track.FAMILY,
        group=group,
        manufacturer_name="m",
        commercial_name="c",
        model_description="d",
        tax_key=KEY,
        propulsion=Propulsion.ELECTRIC,
        list_price=Decimal("1"),
        catalogue_value_of_use=Decimal("1"),
        monthly_company_cost=Decimal("1"),
        default_participation=Decimal("0"),
    )


def occupation(
    group: EntitlementGroup,
    *,
    mandatory: bool = False,
    waiver: bool = True,
    allowance: str = "1500",
) -> OccupationPolicy:
    return OccupationPolicy(
        "OCC-TEST",
        "בודק/ת",
        group,
        mandatory,
        waiver and not mandatory,
        Decimal(allowance if waiver and not mandatory else "0"),
    )


def facts(
    group: EntitlementGroup | None = None,
    children: int | None = None,
    occupation_id: str | None = None,
) -> EligibilityFacts:
    return EligibilityFacts(
        entitlement_group=FieldValue() if group is None else usable(group),
        qualifying_children_count=FieldValue() if children is None else usable(children),
        occupation_id=FieldValue() if occupation_id is None else usable(occupation_id),
    )


@pytest.mark.parametrize(
    ("vehicle_group", "employee_group", "status", "reason"),
    [
        (C, C, EligibilityStatus.ELIGIBLE, EligibilityReason.GROUP_OWN),
        (A, C, EligibilityStatus.ELIGIBLE, EligibilityReason.GROUP_LOWER),
        (D, C, EligibilityStatus.INELIGIBLE, EligibilityReason.GROUP_HIGHER),  # AC11
        (B, A, EligibilityStatus.INELIGIBLE, EligibilityReason.GROUP_HIGHER),
    ],
)
def test_own_group_or_lower(
    vehicle_group: EntitlementGroup,
    employee_group: EntitlementGroup,
    status: EligibilityStatus,
    reason: EligibilityReason,
) -> None:
    decision = evaluate_vehicle(vehicle(vehicle_group), facts(employee_group), None, rules())
    assert (decision.status, decision.reasons) == (status, (reason,))


def test_unknown_or_unclear_group_needs_information() -> None:
    decision = evaluate_vehicle(vehicle(A), facts(), None, rules())
    assert decision.status is EligibilityStatus.NEEDS_INFORMATION
    assert decision.reasons == (EligibilityReason.GROUP_UNKNOWN,)
    assert [m.field for m in decision.missing] == ["employee.entitlementGroup"]
    unclear = EligibilityFacts(
        entitlement_group=FieldValue(value=B, status=FieldStatus.NEEDS_CLARIFICATION)
    )
    decision = evaluate_vehicle(vehicle(A), unclear, None, rules())
    assert decision.missing[0].reason is MissingReason.NEEDS_CLARIFICATION


def test_occupation_group_conflict_is_eligible_only_under_both_groups() -> None:
    table_bet = occupation(B)  # T20: the table says ב, the employee reports ג
    reported = facts(C, occupation_id="OCC-TEST")
    assert evaluate_vehicle(vehicle(B), reported, table_bet, rules()).status is (
        EligibilityStatus.ELIGIBLE
    )
    assert evaluate_vehicle(vehicle(D), reported, table_bet, rules()).status is (
        EligibilityStatus.INELIGIBLE
    )
    conflict = evaluate_vehicle(vehicle(C), reported, table_bet, rules())
    assert conflict.status is EligibilityStatus.NEEDS_INFORMATION
    assert conflict.reasons == (EligibilityReason.OCCUPATION_GROUP_CONFLICT,)


@pytest.mark.parametrize(
    ("children", "status"),
    [
        (4, EligibilityStatus.ELIGIBLE),
        (5, EligibilityStatus.ELIGIBLE),
        (3, EligibilityStatus.INELIGIBLE),
        (0, EligibilityStatus.INELIGIBLE),
    ],
)
def test_family_track_needs_at_least_four_children(
    children: int, status: EligibilityStatus
) -> None:
    family = vehicle(None, "CAR-19")
    assert evaluate_vehicle(family, facts(A, children), None, rules()).status is status


def test_family_track_ignores_group_and_never_assumes_zero_children() -> None:
    family = vehicle(None, "CAR-19")
    assert evaluate_vehicle(family, facts(None, 4), None, rules()).status is (
        EligibilityStatus.ELIGIBLE
    )  # T04: not rejected by group ordering
    unknown = evaluate_vehicle(family, facts(D), None, rules())
    assert unknown.status is EligibilityStatus.NEEDS_INFORMATION
    assert unknown.reasons == (EligibilityReason.FAMILY_CHILDREN_UNKNOWN,)


def test_waiver_by_occupation() -> None:
    assert evaluate_waiver_option(
        facts(occupation_id="OCC-TEST"), occupation(B, mandatory=True), rules()
    ).reasons == (EligibilityReason.WAIVER_MANDATORY_CAR,)  # T21
    allowed = evaluate_waiver_option(facts(occupation_id="OCC-TEST"), occupation(B), rules())
    assert allowed.status is EligibilityStatus.ELIGIBLE  # T22
    absent = evaluate_waiver_option(facts(occupation_id="OCC-OTHER"), None, rules())
    assert absent.reasons == (EligibilityReason.OCCUPATION_NOT_IN_TABLE,)  # T23
    unknown = evaluate_waiver_option(facts(), None, rules())
    assert unknown.reasons == (EligibilityReason.OCCUPATION_UNKNOWN,)


def test_undefined_policies_fail_explicitly() -> None:
    neither = OccupationPolicy("OCC-TEST", "x", B, False, False, Decimal("0"))
    with pytest.raises(UnsupportedPolicyError):  # A8
        evaluate_waiver_option(facts(occupation_id="OCC-TEST"), neither, rules())
    with pytest.raises(UnsupportedPolicyError):
        evaluate_vehicle(vehicle(A), facts(A), None, replace(rules(), choose_from="any"))
