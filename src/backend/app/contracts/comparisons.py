"""`compare_scenarios` and `get_comparison_details`. STATUS: PROVISIONAL.

All amounts come from the deterministic engine. `raw` keeps full Decimal precision (no
quantization); `display` is whole-shekel half-away-from-zero rounding of that raw value.
`delta.raw = alternative.net.raw - current.net.raw`; `delta.display` rounds that difference
(never a difference of rounded nets). Unknown or failed amounts are null, never zero.
"""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from app.contracts.catalogue import EligibilityReason
from app.contracts.common import (
    CalculationMonthStr,
    CatalogueIdStr,
    ContractModel,
    Freshness,
    Issue,
    MoneyStr,
    OptionKeyStr,
    RunKind,
    Track,
    WholeShekelStr,
)
from app.contracts.profile import VehicleTaxKey
from app.contracts.readiness import Selection


class MoneyAmount(ContractModel):
    raw: MoneyStr
    display: WholeShekelStr


class ScenarioStatus(StrEnum):
    CALCULATED = "calculated"
    INELIGIBLE = "ineligible"
    NEEDS_INFORMATION = "needs_information"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class CalculationBreakdown(ContractModel):
    earned_gross: MoneyAmount
    """G = base + commissions + bonus."""
    allowance: MoneyAmount
    """A - taxable vehicle maintenance allowance."""
    electric_gross_up: MoneyAmount
    """E - grossed-up electric reimbursement."""
    value_of_use: MoneyAmount
    """U - vehicle benefit-in-kind (not paid cash)."""
    taxable_base: MoneyAmount
    """T = G + A + E + U."""
    pension_employee: MoneyAmount
    study_fund_employee: MoneyAmount
    credit_points_credit: MoneyAmount
    pension_credit: MoneyAmount
    income_tax_before_credits: MoneyAmount
    income_tax: MoneyAmount
    national_insurance: MoneyAmount
    health_insurance: MoneyAmount
    ordinary_participation: MoneyAmount
    """C - net deduction; does not reduce U."""
    family_gap_contribution: MoneyAmount
    """F = 0.5 x max(0, catalogue monthly cost - group budget)."""
    monthly_company_cost: MoneyAmount | None = None
    group_budget: MoneyAmount | None = None


class VehicleSource(ContractModel):
    catalogue_id: CatalogueIdStr | None = None
    tax_key: VehicleTaxKey | None = None
    participation_source: str | None = None
    """e.g. "catalogue_default", "employee_reported", "representative"."""


class Assumption(ContractModel):
    code: str = Field(max_length=80)
    """Stable token, e.g. "demo_policy.family_participation_base_plus_gap"."""
    option_key: OptionKeyStr | None = None


class BaselineResult(ContractModel):
    status: ScenarioStatus
    net: MoneyAmount | None = None
    breakdown: CalculationBreakdown | None = None
    vehicle: VehicleSource | None = None
    missing_information: list[Issue] = Field(default_factory=list)


class ScenarioResult(ContractModel):
    option_key: OptionKeyStr
    alternative_id: UUID | None = None
    alternative_version: int | None = Field(default=None, ge=0)
    track: Track | None = None
    status: ScenarioStatus
    eligibility_reasons: list[EligibilityReason] = Field(default_factory=list)
    net: MoneyAmount | None = None
    delta: MoneyAmount | None = None
    """null when the baseline is not requested or not calculable."""
    breakdown: CalculationBreakdown | None = None
    vehicle: VehicleSource | None = None
    missing_information: list[Issue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _amounts_only_when_calculated(self) -> Self:
        calculated = self.status is ScenarioStatus.CALCULATED
        if calculated and (self.net is None or self.breakdown is None):
            raise ValueError("calculated scenarios require net and breakdown")
        if not calculated and (self.net is not None or self.delta is not None):
            raise ValueError("amounts are only allowed for calculated scenarios")
        return self


class CompareRequest(ContractModel):
    selection: Selection
    include_current: bool
    kind: RunKind
    expected_case_version: int = Field(ge=0)
    expected_profile_version: int | None = Field(default=None, ge=0)
    operation_id: UUID

    @model_validator(mode="after")
    def _regular_requires_profile_version(self) -> Self:
        if self.kind is RunKind.REGULAR and self.expected_profile_version is None:
            raise ValueError("regular comparisons require expectedProfileVersion")
        return self


class ComparisonResult(ContractModel):
    run_id: UUID
    kind: RunKind
    calculation_month: CalculationMonthStr
    case_version_used: int = Field(ge=0)
    profile_version_used: int = Field(ge=0)
    rules_snapshot_id: UUID
    catalogue_version_id: UUID
    tax_dataset_version_id: UUID
    current: BaselineResult | None = None
    """null when includeCurrent=false."""
    alternatives: list[ScenarioResult]
    assumptions: list[Assumption] = Field(default_factory=list)
    freshness: Freshness
    created_at: datetime


class ComparisonDetailsRequest(ContractModel):
    run_id: UUID


class ComparisonDetails(ContractModel):
    result: ComparisonResult
    input_snapshot: dict[str, JsonValue]
    """Immutable input snapshot (shape finalized with persistence, Phase 3)."""
