"""Current arrangements and alternative scenario specifications consumed by Business Logic.

Values that may be unknown are `FieldValue`s; the scenario builder turns them into complete
calculation components or explicit missing facts - never zeros.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.business.domain.provenance import FieldValue
from app.business.domain.vehicles import Propulsion, Track

WAIVER_OPTION_KEY = "waiver"
CURRENT_OPTION_KEY = "current"


class ArrangementKind(StrEnum):
    COMPANY_CAR = "company_car"
    ALLOWANCE = "allowance"
    NO_CAR_NO_ALLOWANCE = "no_car_no_allowance"


@dataclass(frozen=True, slots=True)
class AlternativeVehicleSpec:
    """A selectable catalogue vehicle (eligibility already established by the caller)."""

    option_key: str
    catalogue_id: str
    track: Track
    propulsion: Propulsion
    value_of_use: FieldValue[Decimal]
    """From the complete official tax key; missing if the lookup is unavailable/ambiguous."""
    participation: FieldValue[Decimal]
    """Catalogue default or an approved representative override."""
    monthly_company_cost: Decimal
    group_budget: FieldValue[Decimal] = field(default_factory=FieldValue)
    """Employee group budget; required only for the family track gap."""
    participation_source: str | None = None


@dataclass(frozen=True, slots=True)
class WaiverSpec:
    allowance: FieldValue[Decimal]
    """Occupation-table maintenance allowance (eligibility already established)."""
    option_key: str = WAIVER_OPTION_KEY


@dataclass(frozen=True, slots=True)
class IneligibleSpec:
    """Pass-through for an option already found ineligible; never calculated."""

    option_key: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CurrentCompanyCarSpec:
    track: Track
    propulsion: FieldValue[Propulsion]
    value_of_use: FieldValue[Decimal]
    participation: FieldValue[Decimal]
    """Actual reported participation (may include the family deduction, see flag)."""
    family_deduction: FieldValue[Decimal] = field(default_factory=FieldValue)
    family_deduction_included_in_participation: FieldValue[bool] = field(default_factory=FieldValue)
    catalogue_id: str | None = None


@dataclass(frozen=True, slots=True)
class CurrentAllowanceSpec:
    allowance: FieldValue[Decimal]


@dataclass(frozen=True, slots=True)
class CurrentNoCarSpec:
    pass


AlternativeSpec = AlternativeVehicleSpec | WaiverSpec | IneligibleSpec
BaselineSpec = CurrentCompanyCarSpec | CurrentAllowanceSpec | CurrentNoCarSpec
