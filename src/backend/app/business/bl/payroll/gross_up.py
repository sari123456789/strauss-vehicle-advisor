"""Electric reimbursement gross-up: find E with net(E) - net(0) = target for the SAME scenario.

Uses the same payroll calculator (no duplicated tax formulas). Bisection assumes the demo net
function is continuous and monotonic in E (every marginal deduction rate is below 100%).
"""

from decimal import Decimal

from app.business.bl.payroll.calculator import calculate_payroll
from app.business.domain.errors import GrossUpNotConvergedError
from app.business.domain.payroll import ArrangementComponents, PayrollCalculationInput, PayrollFacts
from app.business.domain.rules import RulesConfig
from app.business.domain.values import ZERO, calculation_context


def solve_electric_gross_up(
    facts: PayrollFacts, components: ArrangementComponents, rules: RulesConfig
) -> Decimal:
    settings = rules.gross_up
    target = rules.electric.net_bonus
    with calculation_context():
        base_net = calculate_payroll(PayrollCalculationInput(facts, components, ZERO), rules).net

        def net_gain(gross_up: Decimal) -> Decimal:
            scenario = PayrollCalculationInput(facts, components, gross_up)
            return calculate_payroll(scenario, rules).net - base_net

        lower, upper = ZERO, settings.initial_upper_bound
        while net_gain(upper) < target:
            if upper >= settings.max_upper_bound:
                raise GrossUpNotConvergedError("net target not bracketed within the upper bound")
            upper = min(upper * 2, settings.max_upper_bound)

        for _ in range(settings.max_iterations):
            middle = (lower + upper) / 2
            error = net_gain(middle) - target
            if abs(error) <= settings.net_tolerance:
                return middle
            if error < ZERO:
                lower = middle
            else:
                upper = middle
    raise GrossUpNotConvergedError("net target not reached within the iteration limit")
