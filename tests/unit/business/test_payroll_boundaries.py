"""AC08 / T59 / T60: marginal tax bands, insurance threshold/ceiling and contribution limits.

Expected values are derived by hand from the source JSON tables (not from the engine).
"""

from decimal import Decimal

import pytest

from app.business.payroll.calculator import calculate_payroll
from app.business.payroll.contributions import employee_contributions, pension_credit
from app.business.payroll.income_tax import income_tax_after_credits, progressive_tax
from app.business.payroll.social_insurance import insurance_charge
from app.domain.payroll import ArrangementComponents
from tests.unit.business.helpers import calc_input, d, facts, rules

# Cumulative tax at each band boundary: 7010x0.10; +3050x0.14; +6090x0.20; +6290x0.31;
# +24250x0.35; +13440x0.47.
BOUNDARIES = [
    ("7010", "701", "0.10", "0.14"),
    ("10060", "1128", "0.14", "0.20"),
    ("16150", "2346", "0.20", "0.31"),
    ("22440", "4295.9", "0.31", "0.35"),
    ("46690", "12783.4", "0.35", "0.47"),
    ("60130", "19100.2", "0.47", "0.50"),
]
CENT = Decimal("0.01")


@pytest.mark.parametrize(("boundary", "cumulative", "rate_below", "rate_above"), BOUNDARIES)
def test_tax_below_at_and_above_each_band_boundary(
    boundary: str, cumulative: str, rate_below: str, rate_above: str
) -> None:
    brackets = rules().brackets
    b, cum = d(boundary), d(cumulative)
    assert progressive_tax(b, brackets) == cum
    assert progressive_tax(b - CENT, brackets) == cum - CENT * d(rate_below)
    assert progressive_tax(b + CENT, brackets) == cum + CENT * d(rate_above)


def test_tax_is_marginal_not_a_single_rate() -> None:
    assert progressive_tax(d("70000"), rules().brackets) == d("19100.2") + d("9870") * d("0.50")
    assert progressive_tax(d("0"), rules().brackets) == 0


def test_credits_never_create_negative_tax() -> None:
    assert income_tax_after_credits(d("100"), d("2420"), d("0")) == 0


@pytest.mark.parametrize(
    ("base", "national", "health"),
    [
        ("0", "0", "0"),
        ("7522", "78.2288", "242.9606"),
        ("7523", "78.2988", "243.0123"),
        ("50695", "3100.3388", "2475.0047"),
        ("60000", "3100.3388", "2475.0047"),
    ],
)
def test_insurance_threshold_and_ceiling(base: str, national: str, health: str) -> None:
    config = rules()
    assert insurance_charge(d(base), config.national_insurance) == d(national)
    assert insurance_charge(d(base), config.health_insurance) == d(health)


@pytest.mark.parametrize(
    ("pension", "credit"),
    [("0", "0"), ("300", "105"), ("665", "232.75"), ("665.01", "232.75"), ("1110", "232.75")],
)
def test_pension_credit_cap_at_seven_percent_of_9500(pension: str, credit: str) -> None:
    assert pension_credit(d(pension), rules().pension_credit) == d(credit)


def test_contributions_use_earned_gross_only() -> None:
    assert employee_contributions(d("18500"), d("0.06"), d("0.025")) == (d("1110"), d("462.5"))


def test_low_salary_high_credits_zero_pension_is_non_negative() -> None:
    """T60: tax floors at zero; pension credit is based on the actual (zero) contribution."""
    result = calculate_payroll(
        calc_input(facts(base_gross="5000", pension_employee_rate="0", credit_points="10")), rules()
    )
    assert result.income_tax == 0 and result.pension_credit == 0
    assert result.net == d("5000") - result.national_insurance - result.health_insurance - d("125")


def test_zero_study_fund_rate_and_zero_components_are_valid() -> None:
    """AC06 / T13: explicit zeros are usable values."""
    result = calculate_payroll(
        calc_input(facts(study_fund_employee_rate="0", commissions_gross="0", bonus_gross="0")),
        rules(),
    )
    assert result.study_fund_employee == 0


def test_zero_base_salary_is_computed_not_defaulted() -> None:
    result = calculate_payroll(
        calc_input(facts(base_gross="0", credit_points="0", pension_employee_rate="0")), rules()
    )
    assert result.net == 0 and result.income_tax == 0


def test_participation_does_not_reduce_value_of_use_or_tax_base() -> None:
    without = calculate_payroll(
        calc_input(facts(), ArrangementComponents(value_of_use=d("4000"))), rules()
    )
    with_participation = calculate_payroll(
        calc_input(
            facts(), ArrangementComponents(value_of_use=d("4000"), ordinary_participation=d("500"))
        ),
        rules(),
    )
    assert with_participation.taxable_base == without.taxable_base
    assert with_participation.income_tax == without.income_tax
    assert with_participation.net == without.net - d("500")


def test_allowance_is_taxable_insurable_and_not_pensionable() -> None:
    base = calculate_payroll(calc_input(facts()), rules())
    with_allowance = calculate_payroll(
        calc_input(facts(), ArrangementComponents(allowance=d("1500"))), rules()
    )
    assert with_allowance.taxable_base == base.taxable_base + d("1500")
    assert with_allowance.insurance_base == base.insurance_base + d("1500")
    assert with_allowance.pension_employee == base.pension_employee


def test_decimal_precision_is_exact() -> None:
    result = calculate_payroll(
        calc_input(
            facts(), ArrangementComponents(value_of_use=d("4070"), ordinary_participation=d("300"))
        ),
        rules(),
    )
    assert result.net == d("11031.819")
    assert result.national_insurance == d("1131.5888")
