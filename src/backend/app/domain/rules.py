"""Typed business/payroll configuration (source JSON + explicit derived demo policy).

No file paths or free-text notes. Local invariants are enforced here; which policy values the
engine supports is checked by `app.business.rules.validator`.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.domain.errors import InvalidValueError
from app.domain.values import ZERO, require_non_negative, require_rate


class EntitlementGroup(StrEnum):
    """Source Hebrew labels are preserved as values."""

    ALEF = "א"
    BET = "ב"
    GIMEL = "ג"
    DALET = "ד"


@dataclass(frozen=True, slots=True)
class TaxBracket:
    upper_bound: Decimal | None
    """Cumulative upper boundary of the band; None for the top band."""
    rate: Decimal

    def __post_init__(self) -> None:
        require_rate(self.rate, "bracket.rate")
        if self.upper_bound is not None:
            require_non_negative(self.upper_bound, "bracket.upperBound")


@dataclass(frozen=True, slots=True)
class InsuranceSchedule:
    threshold: Decimal
    ceiling: Decimal
    low_rate: Decimal
    high_rate: Decimal

    def __post_init__(self) -> None:
        require_non_negative(self.threshold, "insurance.threshold")
        require_non_negative(self.ceiling, "insurance.ceiling")
        require_rate(self.low_rate, "insurance.lowRate")
        require_rate(self.high_rate, "insurance.highRate")
        if self.ceiling < self.threshold:
            raise InvalidValueError("insurance ceiling must not be below threshold")


@dataclass(frozen=True, slots=True)
class PensionCreditRule:
    rate: Decimal
    max_contribution_rate: Decimal
    income_ceiling: Decimal

    def __post_init__(self) -> None:
        require_rate(self.rate, "pensionCredit.rate")
        require_rate(self.max_contribution_rate, "pensionCredit.maxContributionRate")
        require_non_negative(self.income_ceiling, "pensionCredit.incomeCeiling")


@dataclass(frozen=True, slots=True)
class ElectricRule:
    net_bonus: Decimal
    method: str

    def __post_init__(self) -> None:
        require_non_negative(self.net_bonus, "electric.netBonus")


@dataclass(frozen=True, slots=True)
class FamilyTrackRule:
    min_children: int
    child_max_age: int
    catalogue_ids: tuple[str, ...]
    price_source: str
    gap_split_employee: Decimal

    def __post_init__(self) -> None:
        if self.min_children < 0 or self.child_max_age < 0:
            raise InvalidValueError("family track counts must be non-negative")
        require_rate(self.gap_split_employee, "familyTrack.gapSplitEmployee")


@dataclass(frozen=True, slots=True)
class WaiverRule:
    allowed_by: str
    maintenance_is_taxable: bool
    maintenance_is_pensionable: bool


@dataclass(frozen=True, slots=True)
class ParticipationRule:
    treatment: str
    reduces_value_of_use: bool


@dataclass(frozen=True, slots=True)
class DerivedPolicy:
    """Explicit demonstration decisions (data/rules/derived-demo-policy.json)."""

    version: str
    family_participation_mode: str
    allowance_insurable: bool
    gross_up_insurable: bool
    contributions_exclude_vehicle_benefits: bool
    child_age_comparison: str


@dataclass(frozen=True, slots=True)
class GrossUpSettings:
    net_tolerance: Decimal
    max_iterations: int
    initial_upper_bound: Decimal
    max_upper_bound: Decimal

    def __post_init__(self) -> None:
        if self.net_tolerance <= ZERO or self.max_iterations < 1:
            raise InvalidValueError("gross-up tolerance and iterations must be positive")
        if not (ZERO < self.initial_upper_bound <= self.max_upper_bound):
            raise InvalidValueError("gross-up bounds must satisfy 0 < initial <= max")


@dataclass(frozen=True, slots=True)
class SupportLimits:
    max_monthly_amount: Decimal
    max_credit_points: Decimal
    max_children: int
    max_alternatives_per_run: int


@dataclass(frozen=True, slots=True)
class SourceReference:
    supplied_version: str
    sha256: str


@dataclass(frozen=True, slots=True)
class RulesConfig:
    source: SourceReference
    derived_source: SourceReference
    budgets: dict[EntitlementGroup, Decimal]
    group_order: tuple[EntitlementGroup, ...]
    choose_from: str
    electric: ElectricRule
    family_track: FamilyTrackRule
    waiver: WaiverRule
    unused_budget: str
    net_basis: str
    tax_year: int
    credit_point_value: Decimal
    brackets: tuple[TaxBracket, ...]
    national_insurance: InsuranceSchedule
    health_insurance: InsuranceSchedule
    pension_credit: PensionCreditRule
    contributions_base: str
    participation: ParticipationRule
    rounding_during: str
    display_rounding: str
    derived: DerivedPolicy
    gross_up: GrossUpSettings
    limits: SupportLimits
    value_of_use_key: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if set(self.group_order) != set(self.budgets) or len(self.group_order) != len(
            set(self.group_order)
        ):
            raise InvalidValueError("groupOrder must list each budget group exactly once")
        for group, budget in self.budgets.items():
            require_non_negative(budget, f"budgets.{group}")
        require_non_negative(self.credit_point_value, "creditPointValue")
        if not self.brackets or self.brackets[-1].upper_bound is not None:
            raise InvalidValueError("tax brackets must end with an unbounded top band")
        previous = ZERO
        for bracket in self.brackets[:-1]:
            if bracket.upper_bound is None:
                raise InvalidValueError("only the top tax band may be unbounded")
            if bracket.upper_bound <= previous:
                raise InvalidValueError("tax bracket boundaries must be strictly increasing")
            previous = bracket.upper_bound
