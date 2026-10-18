"""Conversation-specific business case, alternatives and immutable calculation records.

`ComparisonCase.actual_facts` is the case's *working copy* of the employee's actual facts,
exactly as they were in the shared EmployeeProfile at `salary_profile_version_used` (field
values with their provenance). It is not a historical record: historical inputs are the
immutable `ComparisonRunRecord.input_snapshot`. Hypothetical values live only in
`hypothetical_overrides` and never in `actual_facts`.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.business.domain.arrangements import WAIVER_OPTION_KEY, ArrangementKind
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.errors import InvalidValueError
from app.business.domain.json_values import JsonObject, JsonValue, freeze_array, freeze_object
from app.business.domain.provenance import FieldValue
from app.business.domain.rules import EntitlementGroup
from app.business.domain.values import require_non_negative, require_rate
from app.business.domain.vehicles import Propulsion, Track, VehicleRef

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_OPTION_KEY = re.compile(r"^(vehicle:[A-Z]{2,5}-\d{2,4}|waiver)$")
_OCCUPATION_ID = re.compile(r"^OCC-[A-Z0-9-]{2,40}$")
MAX_CHILDREN = 100


class CaseLifecycleStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class RunKind(StrEnum):
    REGULAR = "regular"
    HYPOTHETICAL = "hypothetical"


class FactPath(StrEnum):
    """The 16 actual employee facts (same strings as the shared contract's field paths)."""

    BASE_GROSS = "payroll.baseGross"
    COMMISSIONS_GROSS = "payroll.commissionsGross"
    BONUS_GROSS = "payroll.bonusGross"
    PENSION_EMPLOYEE_RATE = "payroll.pensionEmployeeRate"
    STUDY_FUND_EMPLOYEE_RATE = "payroll.studyFundEmployeeRate"
    CREDIT_POINTS = "payroll.creditPoints"
    OCCUPATION_ID = "employee.occupationId"
    ENTITLEMENT_GROUP = "employee.entitlementGroup"
    QUALIFYING_CHILDREN_COUNT = "employee.qualifyingChildrenCount"
    CURRENT_KIND = "currentArrangement.kind"
    CURRENT_VEHICLE = "currentArrangement.vehicle"
    CURRENT_PROPULSION = "currentArrangement.propulsion"
    CURRENT_PARTICIPATION = "currentArrangement.participation"
    CURRENT_FAMILY_DEDUCTION = "currentArrangement.familyDeduction"
    CURRENT_FAMILY_DEDUCTION_INCLUDED = "currentArrangement.familyDeductionIncludedInParticipation"
    CURRENT_ALLOWANCE = "currentArrangement.allowance"


_FACT_LOCATION: Mapping[FactPath, tuple[str | None, str]] = {
    FactPath.BASE_GROSS: ("salary", "base_gross"),
    FactPath.COMMISSIONS_GROSS: ("salary", "commissions_gross"),
    FactPath.BONUS_GROSS: ("salary", "bonus_gross"),
    FactPath.PENSION_EMPLOYEE_RATE: ("salary", "pension_employee_rate"),
    FactPath.STUDY_FUND_EMPLOYEE_RATE: ("salary", "study_fund_employee_rate"),
    FactPath.CREDIT_POINTS: ("salary", "credit_points"),
    FactPath.OCCUPATION_ID: (None, "occupation_id"),
    FactPath.ENTITLEMENT_GROUP: (None, "entitlement_group"),
    FactPath.QUALIFYING_CHILDREN_COUNT: (None, "qualifying_children_count"),
    FactPath.CURRENT_KIND: ("current_arrangement", "kind"),
    FactPath.CURRENT_VEHICLE: ("current_arrangement", "vehicle"),
    FactPath.CURRENT_PROPULSION: ("current_arrangement", "propulsion"),
    FactPath.CURRENT_PARTICIPATION: ("current_arrangement", "participation"),
    FactPath.CURRENT_FAMILY_DEDUCTION: ("current_arrangement", "family_deduction"),
    FactPath.CURRENT_FAMILY_DEDUCTION_INCLUDED: (
        "current_arrangement",
        "family_deduction_included_in_participation",
    ),
    FactPath.CURRENT_ALLOWANCE: ("current_arrangement", "allowance"),
}
_MONEY = {
    FactPath.BASE_GROSS,
    FactPath.COMMISSIONS_GROSS,
    FactPath.BONUS_GROSS,
    FactPath.CURRENT_PARTICIPATION,
    FactPath.CURRENT_FAMILY_DEDUCTION,
    FactPath.CURRENT_ALLOWANCE,
}
_RATES = {FactPath.PENSION_EMPLOYEE_RATE, FactPath.STUDY_FUND_EMPLOYEE_RATE}


def profile_fact(profile: EmployeeProfile, path: FactPath) -> FieldValue[Any]:
    group, name = _FACT_LOCATION[path]
    holder = profile if group is None else getattr(profile, group)
    value: FieldValue[Any] = getattr(holder, name)
    return value


def with_profile_fact(
    profile: EmployeeProfile, path: FactPath, value: FieldValue[Any]
) -> EmployeeProfile:
    validate_fact_value(path, value.value)
    group, name = _FACT_LOCATION[path]
    changes: dict[str, Any] = {name: value}
    if group is None:
        return replace(profile, **changes)
    return replace(profile, **{group: replace(getattr(profile, group), **changes)})


def validate_fact_value(path: FactPath, value: object) -> None:
    """Type and local-invariant check of one fact value; `None` means missing."""
    if value is None:
        return
    where = path.value
    if path in _MONEY or path is FactPath.CREDIT_POINTS:
        if not isinstance(value, Decimal):
            raise InvalidValueError(f"{where}: expected a Decimal", field=where)
        require_non_negative(value, where)
    elif path in _RATES:
        if not isinstance(value, Decimal):
            raise InvalidValueError(f"{where}: expected a Decimal rate", field=where)
        require_rate(value, where)
    elif path is FactPath.OCCUPATION_ID:
        if not isinstance(value, str) or not _OCCUPATION_ID.fullmatch(value):
            raise InvalidValueError(f"{where}: invalid occupation id {value!r}", field=where)
    elif path is FactPath.QUALIFYING_CHILDREN_COUNT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidValueError(f"{where}: expected an integer", field=where)
        if not 0 <= value <= MAX_CHILDREN:
            raise InvalidValueError(f"{where}: must be between 0 and {MAX_CHILDREN}", field=where)
    else:
        expected: type = {
            FactPath.ENTITLEMENT_GROUP: EntitlementGroup,
            FactPath.CURRENT_KIND: ArrangementKind,
            FactPath.CURRENT_VEHICLE: VehicleRef,
            FactPath.CURRENT_PROPULSION: Propulsion,
            FactPath.CURRENT_FAMILY_DEDUCTION_INCLUDED: bool,
        }[path]
        if not isinstance(value, expected):
            raise InvalidValueError(f"{where}: expected {expected.__name__}", field=where)


def _require_month(month: str) -> None:
    if not isinstance(month, str) or not _MONTH.fullmatch(month):
        raise InvalidValueError(f"calculation month must be YYYY-MM, got {month!r}")


def _require_version(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidValueError(f"{name} must be a non-negative integer")


def _require_aware(value: datetime | None, name: str) -> None:
    if value is not None and value.utcoffset() is None:
        raise InvalidValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class HypotheticalOverride:
    """A case-local "what if" value; never written to the shared EmployeeProfile."""

    path: FactPath
    value: object
    created_at: datetime
    source_message_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            raise InvalidValueError("a hypothetical override requires a value; use no override")
        validate_fact_value(self.path, self.value)
        _require_aware(self.created_at, "override.createdAt")


@dataclass(frozen=True, slots=True)
class ComparisonCase:
    case_id: UUID
    conversation_id: UUID
    owner_subject_id: UUID
    calculation_month: str
    reference_date: date
    version: int
    lifecycle_status: CaseLifecycleStatus
    actual_facts: EmployeeProfile
    """Working copy of the profile at `salary_profile_version_used` (its `version`)."""
    created_at: datetime
    updated_at: datetime
    hypothetical_overrides: tuple[HypotheticalOverride, ...] = ()

    def __post_init__(self) -> None:
        _require_month(self.calculation_month)
        _require_version(self.version, "case version")
        if self.actual_facts.employee_id != self.owner_subject_id:
            raise InvalidValueError("actual facts must belong to the case owner")
        paths = [o.path for o in self.hypothetical_overrides]
        if len(paths) != len(set(paths)):
            raise InvalidValueError("each fact may have at most one hypothetical override")
        _require_aware(self.created_at, "case.createdAt")
        _require_aware(self.updated_at, "case.updatedAt")

    @property
    def salary_profile_version_used(self) -> int:
        return self.actual_facts.version


class AlternativeKind(StrEnum):
    VEHICLE = "vehicle"
    WAIVER = "waiver"


@dataclass(frozen=True, slots=True)
class Alternative:
    alternative_id: UUID
    case_id: UUID
    version: int
    kind: AlternativeKind
    option_key: str
    catalogue_id: str | None
    track: Track | None
    active: bool
    created_at: datetime
    updated_at: datetime
    inputs: JsonObject = field(default_factory=lambda: freeze_object({}))
    deactivated_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_version(self.version, "alternative version")
        if not _OPTION_KEY.fullmatch(self.option_key):
            raise InvalidValueError(f"invalid option key {self.option_key!r}")
        if self.kind is AlternativeKind.VEHICLE:
            if self.catalogue_id is None or self.option_key != f"vehicle:{self.catalogue_id}":
                raise InvalidValueError("a vehicle alternative needs a matching catalogue id")
        elif (
            self.catalogue_id is not None
            or self.track is not None
            or self.option_key != WAIVER_OPTION_KEY
        ):
            raise InvalidValueError("a waiver alternative has no catalogue id or track")
        if self.active != (self.deactivated_at is None):
            raise InvalidValueError(
                "deactivated_at is set exactly when the alternative is inactive"
            )
        object.__setattr__(self, "inputs", freeze_object(self.inputs, path="inputs"))
        for name in ("created_at", "updated_at", "deactivated_at"):
            _require_aware(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class OptionResultSetRecord:
    """An option list exactly as displayed (order preserved). Written once, never changed."""

    result_set_id: UUID
    case_id: UUID
    case_version_used: int
    reference_release_id: UUID
    request: JsonObject
    items: tuple[JsonValue, ...]
    unchecked_categories: tuple[str, ...]
    scope_complete: bool
    created_at: datetime

    def __post_init__(self) -> None:
        _require_version(self.case_version_used, "case_version_used")
        object.__setattr__(self, "request", freeze_object(self.request, path="request"))
        object.__setattr__(self, "items", freeze_array(self.items, path="items"))
        if not all(isinstance(c, str) for c in self.unchecked_categories):
            raise InvalidValueError("unchecked categories must be strings")
        object.__setattr__(self, "unchecked_categories", tuple(self.unchecked_categories))
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class ComparisonRunRecord:
    """One executed calculation: immutable input snapshot and result, pinned to one release."""

    run_id: UUID
    case_id: UUID
    kind: RunKind
    calculation_month: str
    case_version_used: int
    profile_version_used: int
    reference_release_id: UUID
    rules_snapshot_id: UUID
    catalogue_version_id: UUID
    tax_dataset_version_id: UUID
    selection: JsonObject
    include_current: bool
    input_snapshot: JsonObject
    result: JsonObject
    operation_id: UUID
    created_by: UUID
    created_at: datetime
    option_result_set_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_month(self.calculation_month)
        _require_version(self.case_version_used, "case_version_used")
        _require_version(self.profile_version_used, "profile_version_used")
        for name in ("selection", "input_snapshot", "result"):
            object.__setattr__(self, name, freeze_object(getattr(self, name), path=name))
        _require_aware(self.created_at, "created_at")
