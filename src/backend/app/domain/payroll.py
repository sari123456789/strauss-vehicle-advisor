"""Complete payroll calculation input and detailed result (all amounts are exact Decimals)."""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.values import ZERO, require_non_negative, require_rate


@dataclass(frozen=True, slots=True)
class PayrollFacts:
    """Complete monthly salary facts; every value is known (explicit zero is valid)."""

    base_gross: Decimal
    commissions_gross: Decimal
    bonus_gross: Decimal
    pension_employee_rate: Decimal
    study_fund_employee_rate: Decimal
    credit_points: Decimal

    def __post_init__(self) -> None:
        require_non_negative(self.base_gross, "payroll.baseGross")
        require_non_negative(self.commissions_gross, "payroll.commissionsGross")
        require_non_negative(self.bonus_gross, "payroll.bonusGross")
        require_rate(self.pension_employee_rate, "payroll.pensionEmployeeRate")
        require_rate(self.study_fund_employee_rate, "payroll.studyFundEmployeeRate")
        require_non_negative(self.credit_points, "payroll.creditPoints")

    @property
    def earned_gross(self) -> Decimal:
        """G = base + commissions + bonus."""
        return self.base_gross + self.commissions_gross + self.bonus_gross


@dataclass(frozen=True, slots=True)
class ArrangementComponents:
    """Vehicle-arrangement amounts, computed once by the benefits calculator."""

    allowance: Decimal = ZERO
    """A - taxable maintenance allowance (not pensionable)."""
    value_of_use: Decimal = ZERO
    """U - vehicle benefit-in-kind; taxable, not paid cash."""
    ordinary_participation: Decimal = ZERO
    """C - net deduction; does not reduce U."""
    family_gap_contribution: Decimal = ZERO
    """F - employee family-track gap share; net deduction."""
    electric: bool = False
    """Whether the electric reimbursement (net target via gross-up) applies."""

    def __post_init__(self) -> None:
        require_non_negative(self.allowance, "allowance")
        require_non_negative(self.value_of_use, "valueOfUse")
        require_non_negative(self.ordinary_participation, "ordinaryParticipation")
        require_non_negative(self.family_gap_contribution, "familyGapContribution")


@dataclass(frozen=True, slots=True)
class PayrollCalculationInput:
    facts: PayrollFacts
    components: ArrangementComponents
    electric_gross_up: Decimal = ZERO
    """E - grossed-up electric reimbursement (0 for the gross-up base calculation)."""

    def __post_init__(self) -> None:
        require_non_negative(self.electric_gross_up, "electricGrossUp")


@dataclass(frozen=True, slots=True)
class PayrollResult:
    earned_gross: Decimal
    allowance: Decimal
    electric_gross_up: Decimal
    value_of_use: Decimal
    taxable_base: Decimal
    insurance_base: Decimal
    contribution_base: Decimal
    pension_employee: Decimal
    study_fund_employee: Decimal
    credit_points_credit: Decimal
    pension_credit: Decimal
    income_tax_before_credits: Decimal
    income_tax: Decimal
    national_insurance: Decimal
    health_insurance: Decimal
    ordinary_participation: Decimal
    family_gap_contribution: Decimal
    net: Decimal

    @property
    def combined_insurance(self) -> Decimal:
        return self.national_insurance + self.health_insurance
