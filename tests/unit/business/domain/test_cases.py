"""Step 3A Domain invariants: cases, overrides, alternatives, records, requests, JSON values."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.business.domain.arrangements import ArrangementKind
from app.business.domain.cases import (
    Alternative,
    AlternativeKind,
    CaseLifecycleStatus,
    ComparisonCase,
    FactPath,
    HypotheticalOverride,
    profile_fact,
    validate_fact_value,
    with_profile_fact,
)
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.errors import InvalidValueError
from app.business.domain.json_values import freeze_json, freeze_object
from app.business.domain.provenance import FieldValue, SourceType
from app.business.domain.representatives import (
    ExpectedValueType,
    InvalidStateTransitionError,
    RepresentativeFieldCode,
    RepresentativeRequest,
    RequestStatus,
)
from app.business.domain.rules import EntitlementGroup
from app.business.domain.vehicles import Track, VehicleRef

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
OWNER = uuid4()


def _case(**overrides: object) -> ComparisonCase:
    values: dict[str, object] = {
        "case_id": uuid4(),
        "conversation_id": uuid4(),
        "owner_subject_id": OWNER,
        "calculation_month": "2026-10",
        "reference_date": date(2026, 10, 1),
        "version": 0,
        "lifecycle_status": CaseLifecycleStatus.ACTIVE,
        "actual_facts": EmployeeProfile(employee_id=OWNER, version=3),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return ComparisonCase(**values)  # type: ignore[arg-type]


def _alternative(**overrides: object) -> Alternative:
    values: dict[str, object] = {
        "alternative_id": uuid4(),
        "case_id": uuid4(),
        "version": 0,
        "kind": AlternativeKind.VEHICLE,
        "option_key": "vehicle:CAR-10",
        "catalogue_id": "CAR-10",
        "track": Track.REGULAR,
        "active": True,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Alternative(**values)  # type: ignore[arg-type]


def _request(**overrides: object) -> RepresentativeRequest:
    values: dict[str, object] = {
        "request_id": uuid4(),
        "case_id": uuid4(),
        "field_code": RepresentativeFieldCode.ALTERNATIVE_PARTICIPATION,
        "expected_value_type": ExpectedValueType.MONEY,
        "question_text": "מה ההשתתפות החודשית ברכב?",
        "subject_snapshot": {"catalogueId": "CAR-10"},
        "subject_fingerprint": "a" * 64,
        "case_version_at_creation": 2,
        "request_version": 0,
        "status": RequestStatus.OPEN,
        "idempotency_key": uuid4(),
        "created_by": OWNER,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return RepresentativeRequest(**values)  # type: ignore[arg-type]


def test_case_exposes_the_profile_version_used_and_keeps_actual_facts_separate() -> None:
    override = HypotheticalOverride(FactPath.BASE_GROSS, Decimal("20000"), NOW)
    case = _case(hypothetical_overrides=(override,))
    assert case.salary_profile_version_used == 3
    assert profile_fact(case.actual_facts, FactPath.BASE_GROSS).value is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"calculation_month": "2026-13"},
        {"version": -1},
        {"actual_facts": EmployeeProfile(employee_id=uuid4(), version=0)},
        {"created_at": datetime(2026, 10, 18)},
        {
            "hypothetical_overrides": (
                HypotheticalOverride(FactPath.BONUS_GROSS, Decimal("1"), NOW),
                HypotheticalOverride(FactPath.BONUS_GROSS, Decimal("2"), NOW),
            )
        },
    ],
)
def test_case_invariants(overrides: dict[str, object]) -> None:
    with pytest.raises(InvalidValueError):
        _case(**overrides)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (FactPath.PENSION_EMPLOYEE_RATE, Decimal("1.5")),
        (FactPath.BASE_GROSS, Decimal("-1")),
        (FactPath.BASE_GROSS, 18500),
        (FactPath.QUALIFYING_CHILDREN_COUNT, True),
        (FactPath.QUALIFYING_CHILDREN_COUNT, 101),
        (FactPath.ENTITLEMENT_GROUP, "ג"),
        (FactPath.OCCUPATION_ID, "software dev"),
        (FactPath.CURRENT_VEHICLE, "CAR-09"),
    ],
)
def test_fact_values_are_type_checked(path: FactPath, value: object) -> None:
    with pytest.raises(InvalidValueError):
        validate_fact_value(path, value)


def test_override_requires_a_value() -> None:
    with pytest.raises(InvalidValueError):
        HypotheticalOverride(FactPath.BASE_GROSS, None, NOW)


def test_profile_fact_helpers_round_trip_every_path() -> None:
    profile = EmployeeProfile(employee_id=OWNER, version=1)
    value = FieldValue.usable(VehicleRef(catalogue_id="CAR-09"), SourceType.EMPLOYEE)
    updated = with_profile_fact(profile, FactPath.CURRENT_VEHICLE, value)
    assert profile_fact(updated, FactPath.CURRENT_VEHICLE) == value
    group = FieldValue.usable(EntitlementGroup.GIMEL, SourceType.EMPLOYEE)
    updated = with_profile_fact(updated, FactPath.ENTITLEMENT_GROUP, group)
    assert updated.entitlement_group == group
    assert profile.current_arrangement.vehicle.value is None
    assert len(list(FactPath)) == 16


@pytest.mark.parametrize(
    "overrides",
    [
        {"catalogue_id": None},
        {"option_key": "vehicle:CAR-11"},
        {"kind": AlternativeKind.WAIVER, "option_key": "waiver"},
        {"active": False},
        {"option_key": "car:CAR-10"},
        {"inputs": {"amount": 1.5}},
    ],
)
def test_alternative_invariants(overrides: dict[str, object]) -> None:
    with pytest.raises(InvalidValueError):
        _alternative(**overrides)


def test_waiver_alternative_and_frozen_inputs() -> None:
    waiver = _alternative(
        kind=AlternativeKind.WAIVER, option_key="waiver", catalogue_id=None, track=None
    )
    assert waiver.inputs == {}
    vehicle = _alternative(inputs={"participation": "350.00"})
    with pytest.raises(TypeError):
        vehicle.inputs["participation"] = "0"  # type: ignore[index]


def test_request_transitions_only_from_open() -> None:
    request = _request()
    cancelled = request.with_status(RequestStatus.CANCELLED, at=NOW)
    assert cancelled.status is RequestStatus.CANCELLED
    assert cancelled.request_version == request.request_version
    with pytest.raises(InvalidStateTransitionError):
        cancelled.with_status(RequestStatus.ANSWERED, at=NOW)
    with pytest.raises(InvalidStateTransitionError):
        request.with_status(RequestStatus.OPEN, at=NOW)


@pytest.mark.parametrize(
    "overrides",
    [
        {"question_text": ""},
        {"question_text": "x" * 1001},
        {"subject_fingerprint": "ABC"},
        {"alternative_id": uuid4()},
        {"option_key": "vehicle"},
        {"subject_snapshot": {"salary": 18500.0}},
    ],
)
def test_request_invariants(overrides: dict[str, object]) -> None:
    with pytest.raises(InvalidValueError):
        _request(**overrides)


def test_freeze_json_rejects_floats_and_non_string_keys_and_is_immutable() -> None:
    frozen = freeze_object({"a": [1, "2", {"b": None, "c": True}]})
    assert frozen["a"] == (1, "2", {"b": None, "c": True})
    with pytest.raises(TypeError):
        frozen["a"] = 1  # type: ignore[index]
    for bad in ({"x": 0.1}, {1: "x"}, [Decimal("1")]):
        with pytest.raises(InvalidValueError):
            freeze_json(bad)


def test_arrangement_kind_is_a_valid_fact_value() -> None:
    validate_fact_value(FactPath.CURRENT_KIND, ArrangementKind.ALLOWANCE)
