"""Shared actual EmployeeProfile: last employee-reported facts, not employer-verified data.

Six salary fields plus occupation, entitlement group, qualifying-children count and the
last-reported current arrangement. Each field may be missing, ambiguous or usable.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from app.domain.arrangements import ArrangementKind
from app.domain.errors import InvalidValueError
from app.domain.provenance import FieldValue
from app.domain.rules import EntitlementGroup
from app.domain.vehicles import Propulsion, VehicleRef


@dataclass(frozen=True, slots=True)
class SalaryFacts:
    base_gross: FieldValue[Decimal] = field(default_factory=FieldValue)
    commissions_gross: FieldValue[Decimal] = field(default_factory=FieldValue)
    bonus_gross: FieldValue[Decimal] = field(default_factory=FieldValue)
    pension_employee_rate: FieldValue[Decimal] = field(default_factory=FieldValue)
    study_fund_employee_rate: FieldValue[Decimal] = field(default_factory=FieldValue)
    credit_points: FieldValue[Decimal] = field(default_factory=FieldValue)


@dataclass(frozen=True, slots=True)
class CurrentArrangementFacts:
    kind: FieldValue[ArrangementKind] = field(default_factory=FieldValue)
    vehicle: FieldValue[VehicleRef] = field(default_factory=FieldValue)
    propulsion: FieldValue[Propulsion] = field(default_factory=FieldValue)
    participation: FieldValue[Decimal] = field(default_factory=FieldValue)
    family_deduction: FieldValue[Decimal] = field(default_factory=FieldValue)
    family_deduction_included_in_participation: FieldValue[bool] = field(default_factory=FieldValue)
    allowance: FieldValue[Decimal] = field(default_factory=FieldValue)


@dataclass(frozen=True, slots=True)
class EmployeeProfile:
    employee_id: UUID
    version: int
    salary: SalaryFacts = field(default_factory=SalaryFacts)
    occupation_id: FieldValue[str] = field(default_factory=FieldValue)
    entitlement_group: FieldValue[EntitlementGroup] = field(default_factory=FieldValue)
    qualifying_children_count: FieldValue[int] = field(default_factory=FieldValue)
    current_arrangement: CurrentArrangementFacts = field(default_factory=CurrentArrangementFacts)

    def __post_init__(self) -> None:
        if self.version < 0:
            raise InvalidValueError("profile version must be non-negative")
        children = self.qualifying_children_count.value
        if children is not None and children < 0:
            raise InvalidValueError("qualifyingChildrenCount must be non-negative")
