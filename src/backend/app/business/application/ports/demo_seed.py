"""Demo-seed source port: fictional reference employees from the workbook plus the explicit
seed mapping (`data/demo/demo-accounts.json`). Only plain values cross this boundary.

The reader never returns the workbook's fictional ID-number column.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.business.application.ports.reference_data import SourceFingerprint
from app.business.domain.arrangements import ArrangementKind
from app.business.domain.rules import EntitlementGroup
from app.contracts.tool_context import Role


@dataclass(frozen=True, slots=True)
class DemoCurrentArrangement:
    kind: ArrangementKind
    catalogue_id: str | None = None
    expected_commercial_name: str | None = None
    """Workbook "current car" text; must equal the mapped catalogue vehicle's name."""
    participation: Decimal | None = None
    allowance: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DemoEmployeeSeed:
    employee_ref: str
    login: str
    display_name: str
    occupation_id: str
    entitlement_group: EntitlementGroup
    base_gross: Decimal
    commissions_gross: Decimal
    bonus_gross: Decimal
    pension_employee_rate: Decimal
    study_fund_employee_rate: Decimal
    credit_points: Decimal
    qualifying_children_count: int
    current: DemoCurrentArrangement


@dataclass(frozen=True, slots=True)
class DemoStaffSeed:
    login: str
    roles: frozenset[Role]
    display_name: str


@dataclass(frozen=True, slots=True)
class DemoSeedSource:
    seed_version: str
    uuid_namespace: UUID
    seed_file: SourceFingerprint
    workbook: SourceFingerprint
    employees: tuple[DemoEmployeeSeed, ...]
    staff: tuple[DemoStaffSeed, ...]


@runtime_checkable
class DemoSeedSourceReader(Protocol):
    def read(self) -> DemoSeedSource:
        """Read and validate the seed mapping and workbook; raise on any inconsistency."""
        ...
