"""Employee pension / study-fund contributions and the demonstration pension credit."""

from decimal import Decimal

from app.domain.rules import PensionCreditRule


def employee_contributions(
    contribution_base: Decimal, pension_rate: Decimal, study_fund_rate: Decimal
) -> tuple[Decimal, Decimal]:
    """P = base x pension rate; H = base x study-fund rate (base = G in the demo)."""
    return contribution_base * pension_rate, contribution_base * study_fund_rate


def pension_credit(pension_employee: Decimal, rule: PensionCreditRule) -> Decimal:
    """0.35 x min(P, 0.07 x 9500), exactly as stated in the source rules (max 232.75)."""
    return rule.rate * min(pension_employee, rule.max_contribution_rate * rule.income_ceiling)
