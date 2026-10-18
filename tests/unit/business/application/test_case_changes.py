"""Input changes: contract -> Domain conversion, limits, actual vs hypothetical semantics."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.business.application.cases.changes import (
    FactChange,
    apply_actual_changes,
    apply_hypothetical_changes,
    parse_changes,
)
from app.business.application.errors import OutOfSupportedRange
from app.business.domain.arrangements import ArrangementKind
from app.business.domain.cases import FactPath, HypotheticalOverride
from app.business.domain.employee_profiles import EmployeeProfile, SalaryFacts
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.business.domain.rules import EntitlementGroup, SupportLimits
from app.business.domain.vehicles import VehicleRef, VehicleTaxKey
from app.contracts.case_inputs import ClearChange, SetChange
from app.contracts.common import FieldPath

LIMITS = SupportLimits(
    max_monthly_amount=Decimal(1_000_000),
    max_credit_points=Decimal(100),
    max_children=100,
    max_alternatives_per_run=30,
)
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
MESSAGE = uuid4()


def _set(field: FieldPath, value: object) -> SetChange:
    return SetChange.model_validate({"op": "set", "field": field, "value": value})


def test_values_are_converted_exactly_to_domain_types() -> None:
    parsed = parse_changes(
        [
            _set(FieldPath.BASE_GROSS, "18500.123456789"),
            _set(FieldPath.STUDY_FUND_EMPLOYEE_RATE, "0"),
            _set(FieldPath.PENSION_EMPLOYEE_RATE, "0.065"),
            _set(FieldPath.QUALIFYING_CHILDREN_COUNT, 4),
            _set(FieldPath.ENTITLEMENT_GROUP, "ג"),
            _set(FieldPath.CURRENT_KIND, "allowance"),
            _set(
                FieldPath.CURRENT_VEHICLE,
                {
                    "taxKey": {
                        "taxYear": "2026",
                        "registrationYear": "2025",
                        "vehicleTypeCode": "1",
                        "manufacturerCode": "01014",
                        "modelCode": "00003",
                    }
                },
            ),
            ClearChange(op="clear", field=FieldPath.BONUS_GROSS),
        ],
        LIMITS,
    )
    assert [c.value for c in parsed] == [
        Decimal("18500.123456789"),
        Decimal("0"),
        Decimal("0.065"),
        4,
        EntitlementGroup.GIMEL,
        ArrangementKind.ALLOWANCE,
        VehicleRef(tax_key=VehicleTaxKey("2026", "2025", "1", "01014", "00003")),
        None,
    ]


def test_limits_and_invalid_values_reject_the_list() -> None:
    with pytest.raises(OutOfSupportedRange):
        parse_changes([_set(FieldPath.BASE_GROSS, "1000000.01")], LIMITS)
    with pytest.raises(OutOfSupportedRange):
        parse_changes([_set(FieldPath.CREDIT_POINTS, "100.5")], LIMITS)


def test_contract_rejects_null_set_and_unsupported_values() -> None:
    for value in (None, 18500.5, "-1", "abc"):
        with pytest.raises(ValidationError):
            _set(FieldPath.BASE_GROSS, value)
    with pytest.raises(ValidationError):
        _set(FieldPath.OCCUPATION_ID, "OCC-x")
    with pytest.raises(ValidationError):
        _set(FieldPath.QUALIFYING_CHILDREN_COUNT, True)


def _profile() -> EmployeeProfile:
    salary = SalaryFacts(
        base_gross=FieldValue(
            value=Decimal("18500"),
            status=FieldStatus.USABLE,
            source=SourceType.EMPLOYEE,
            revision=3,
            last_confirmed_at=NOW,
        ),
        bonus_gross=FieldValue(
            value=Decimal("1000"), status=FieldStatus.USABLE, source=SourceType.EMPLOYEE, revision=1
        ),
    )
    return EmployeeProfile(employee_id=uuid4(), version=4, salary=salary)


def test_actual_changes_bump_revisions_and_record_employee_provenance() -> None:
    profile = _profile()
    updated = apply_actual_changes(
        profile,
        [FactChange(FactPath.BASE_GROSS, Decimal("19000")), FactChange(FactPath.BONUS_GROSS, None)],
        now=NOW,
        source_message_id=MESSAGE,
    )
    base = updated.salary.base_gross
    assert (base.value, base.revision, base.source, base.source_message_id) == (
        Decimal("19000"),
        4,
        SourceType.EMPLOYEE,
        MESSAGE,
    )
    assert base.last_confirmed_at is None and base.updated_at == NOW
    bonus = updated.salary.bonus_gross
    assert bonus.status is FieldStatus.MISSING and bonus.value is None and bonus.revision == 2
    assert updated.version == profile.version  # the repository bumps the profile version


def test_unchanged_value_is_a_no_op_not_a_reconfirmation() -> None:
    profile = _profile()
    same = apply_actual_changes(
        profile,
        [
            FactChange(FactPath.BASE_GROSS, Decimal("18500.00")),
            FactChange(FactPath.COMMISSIONS_GROSS, None),
        ],
        now=NOW,
        source_message_id=MESSAGE,
    )
    assert same == profile
    assert same.salary.base_gross.last_confirmed_at == NOW  # untouched (B3)


def test_hypothetical_set_and_clear_restores_inheritance() -> None:
    overrides = apply_hypothetical_changes(
        (),
        [
            FactChange(FactPath.BONUS_GROSS, Decimal("2000")),
            FactChange(FactPath.BASE_GROSS, Decimal("20000")),
        ],
        now=NOW,
        source_message_id=None,
    )
    assert [o.path for o in overrides] == [FactPath.BASE_GROSS, FactPath.BONUS_GROSS]
    unchanged = apply_hypothetical_changes(
        overrides,
        [FactChange(FactPath.BASE_GROSS, Decimal("20000"))],
        now=NOW,
        source_message_id=None,
    )
    assert unchanged == overrides
    cleared = apply_hypothetical_changes(
        overrides, [FactChange(FactPath.BASE_GROSS, None)], now=NOW, source_message_id=None
    )
    assert cleared == (HypotheticalOverride(FactPath.BONUS_GROSS, Decimal("2000"), NOW),)
