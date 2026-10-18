"""Contract input changes -> Domain fact changes, and how they apply (pure; no I/O).

- `set` converts the validated contract value to its Domain type (decimal strings -> Decimal,
  never through float) and checks local invariants and the demo support limits; an invalid
  value rejects the whole change list. `set null` is already invalid in the contract.
- `clear` in an actual update makes the fact unknown (`missing`), never zero.
- `clear` in a hypothetical update removes the scenario override, so the scenario inherits
  the actual value again (decision B4); it does not model a hypothetical "unknown".
- Setting an unchanged usable value is a no-op: it is not an employee reconfirmation (B3),
  and `lastConfirmedAt` is never set here.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.business.application.errors import InvalidInput, OutOfSupportedRange
from app.business.domain.arrangements import ArrangementKind
from app.business.domain.cases import (
    FactPath,
    HypotheticalOverride,
    profile_fact,
    validate_fact_value,
    with_profile_fact,
)
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.errors import DomainError
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.business.domain.rules import EntitlementGroup, SupportLimits
from app.business.domain.values import require_at_most, to_decimal
from app.business.domain.vehicles import Propulsion, VehicleRef, VehicleTaxKey
from app.contracts.case_inputs import Change, SetChange
from app.contracts.profile import VehicleRef as ContractVehicleRef

MONEY_PATHS = frozenset(
    {
        FactPath.BASE_GROSS,
        FactPath.COMMISSIONS_GROSS,
        FactPath.BONUS_GROSS,
        FactPath.CURRENT_PARTICIPATION,
        FactPath.CURRENT_FAMILY_DEDUCTION,
        FactPath.CURRENT_ALLOWANCE,
    }
)
RATE_PATHS = frozenset({FactPath.PENSION_EMPLOYEE_RATE, FactPath.STUDY_FUND_EMPLOYEE_RATE})


@dataclass(frozen=True, slots=True)
class FactChange:
    path: FactPath
    value: object | None
    """Domain value; `None` means `clear`."""


def _domain_vehicle(value: ContractVehicleRef) -> VehicleRef:
    if value.catalogue_id is not None:
        return VehicleRef(catalogue_id=value.catalogue_id)
    key = value.tax_key
    assert key is not None
    return VehicleRef(
        tax_key=VehicleTaxKey(
            key.tax_year,
            key.registration_year,
            key.vehicle_type_code,
            key.manufacturer_code,
            key.model_code,
        )
    )


def _domain_value(path: FactPath, raw: object, limits: SupportLimits) -> object:
    where = path.value
    if path in MONEY_PATHS:
        amount = to_decimal(raw, field=where)
        validate_fact_value(path, amount)
        return require_at_most(amount, limits.max_monthly_amount, where)
    if path in RATE_PATHS:
        rate = to_decimal(raw, field=where)
        validate_fact_value(path, rate)
        return rate
    if path is FactPath.CREDIT_POINTS:
        points = to_decimal(raw, field=where)
        validate_fact_value(path, points)
        return require_at_most(points, limits.max_credit_points, where)
    if path is FactPath.QUALIFYING_CHILDREN_COUNT:
        validate_fact_value(path, raw)
        assert isinstance(raw, int)
        require_at_most(Decimal(raw), Decimal(limits.max_children), where)
        return raw
    converted: object
    if path is FactPath.ENTITLEMENT_GROUP:
        converted = EntitlementGroup(str(raw))
    elif path is FactPath.CURRENT_KIND:
        converted = ArrangementKind(str(raw))
    elif path is FactPath.CURRENT_PROPULSION:
        converted = Propulsion(str(raw))
    elif path is FactPath.CURRENT_VEHICLE:
        if not isinstance(raw, ContractVehicleRef):
            raise InvalidInput(f"{where}: expected a vehicle reference")
        converted = _domain_vehicle(raw)
    else:
        converted = raw
    validate_fact_value(path, converted)
    return converted


def parse_changes(changes: Sequence[Change], limits: SupportLimits) -> tuple[FactChange, ...]:
    parsed: list[FactChange] = []
    for change in changes:
        path = FactPath(change.field.value)
        if not isinstance(change, SetChange):
            parsed.append(FactChange(path, None))
            continue
        try:
            parsed.append(FactChange(path, _domain_value(path, change.value, limits)))
        except DomainError as error:
            details = {"field": path.value}
            if error.code == "INPUT_OUT_OF_SUPPORTED_RANGE":
                raise OutOfSupportedRange(str(error), safe_details=details) from error
            raise InvalidInput(str(error), safe_details=details) from error
        except ValueError as error:
            raise InvalidInput(
                f"{path.value}: {error}", safe_details={"field": path.value}
            ) from error
    return tuple(parsed)


def apply_actual_changes(
    profile: EmployeeProfile,
    changes: Sequence[FactChange],
    *,
    now: datetime,
    source_message_id: UUID | None,
) -> EmployeeProfile:
    """Employee-reported actual facts: changed fields get revision + 1 and employee provenance."""
    for change in changes:
        current = profile_fact(profile, change.path)
        if change.value is None:
            if current.status is FieldStatus.MISSING:
                continue
            new: FieldValue[object] = FieldValue(
                source=SourceType.EMPLOYEE,
                source_message_id=source_message_id,
                updated_at=now,
                revision=current.revision + 1,
            )
        else:
            if current.status is FieldStatus.USABLE and current.value == change.value:
                continue  # unchanged value: no-op, not a reconfirmation (B3)
            new = FieldValue(
                value=change.value,
                status=FieldStatus.USABLE,
                source=SourceType.EMPLOYEE,
                source_message_id=source_message_id,
                updated_at=now,
                revision=current.revision + 1,
            )
        profile = with_profile_fact(profile, change.path, new)
    return profile


def apply_hypothetical_changes(
    overrides: tuple[HypotheticalOverride, ...],
    changes: Sequence[FactChange],
    *,
    now: datetime,
    source_message_id: UUID | None,
) -> tuple[HypotheticalOverride, ...]:
    by_path = {o.path: o for o in overrides}
    for change in changes:
        if change.value is None:
            by_path.pop(change.path, None)  # B4: inherit the actual value again
            continue
        existing = by_path.get(change.path)
        if existing is not None and existing.value == change.value:
            continue
        by_path[change.path] = HypotheticalOverride(
            path=change.path,
            value=change.value,
            created_at=now,
            source_message_id=source_message_id,
        )
    return tuple(by_path[p] for p in FactPath if p in by_path)


def effective_value(
    profile: EmployeeProfile, overrides: tuple[HypotheticalOverride, ...], path: FactPath
) -> object | None:
    for override in overrides:
        if override.path is path:
            return override.value
    field = profile_fact(profile, path)
    return field.value if field.status is FieldStatus.USABLE else None
