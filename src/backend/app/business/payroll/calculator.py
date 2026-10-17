"""Single detailed Decimal net-pay engine for the challenge demonstration (architecture §11).

Not a statement of payroll law. Identical inputs and rules always produce identical results.
"""

from app.business.payroll.contributions import employee_contributions, pension_credit
from app.business.payroll.income_tax import income_tax_after_credits, progressive_tax
from app.business.payroll.social_insurance import insurance_charge
from app.domain.payroll import PayrollCalculationInput, PayrollResult
from app.domain.rules import RulesConfig
from app.domain.values import ZERO, calculation_context


def calculate_payroll(
    calculation_input: PayrollCalculationInput, rules: RulesConfig
) -> PayrollResult:
    """Compute one complete monthly scenario without any intermediate rounding.

    G = base + commissions + bonus; P = G x pension rate; H = G x study-fund rate
    T = G + A + E + U (tax base); insurance base = G + U (+A, +E per derived policy)
    tax = max(0, progressive(T) - creditPoints x 242 - 0.35 x min(P, 0.07 x 9500))
    net = G + A + E - tax - NI - HI - P - H - C - F
    """
    facts = calculation_input.facts
    parts = calculation_input.components
    policy = rules.derived
    with calculation_context():
        earned = facts.earned_gross
        allowance = parts.allowance
        gross_up = calculation_input.electric_gross_up
        value_of_use = parts.value_of_use

        contribution_base = earned  # allowance and vehicle benefits are excluded (validated)
        pension, study_fund = employee_contributions(
            contribution_base, facts.pension_employee_rate, facts.study_fund_employee_rate
        )

        taxable_base = earned + allowance + gross_up + value_of_use
        insurance_base = (
            earned
            + value_of_use
            + (allowance if policy.allowance_insurable else ZERO)
            + (gross_up if policy.gross_up_insurable else ZERO)
        )

        credit_points_credit = facts.credit_points * rules.credit_point_value
        pension_tax_credit = pension_credit(pension, rules.pension_credit)
        tax_before_credits = progressive_tax(taxable_base, rules.brackets)
        income_tax = income_tax_after_credits(
            tax_before_credits, credit_points_credit, pension_tax_credit
        )
        national_insurance = insurance_charge(insurance_base, rules.national_insurance)
        health_insurance = insurance_charge(insurance_base, rules.health_insurance)

        net = (
            earned
            + allowance
            + gross_up
            - income_tax
            - national_insurance
            - health_insurance
            - pension
            - study_fund
            - parts.ordinary_participation
            - parts.family_gap_contribution
        )

    return PayrollResult(
        earned_gross=earned,
        allowance=allowance,
        electric_gross_up=gross_up,
        value_of_use=value_of_use,
        taxable_base=taxable_base,
        insurance_base=insurance_base,
        contribution_base=contribution_base,
        pension_employee=pension,
        study_fund_employee=study_fund,
        credit_points_credit=credit_points_credit,
        pension_credit=pension_tax_credit,
        income_tax_before_credits=tax_before_credits,
        income_tax=income_tax,
        national_insurance=national_insurance,
        health_insurance=health_insurance,
        ordinary_participation=parts.ordinary_participation,
        family_gap_contribution=parts.family_gap_contribution,
        net=net,
    )
