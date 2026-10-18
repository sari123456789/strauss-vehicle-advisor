"""AC09 / T51 / T52 / T53 / T77: electric reimbursement gross-up to exactly ILS 250 net."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.business.bl.comparisons.builder import build_comparison
from app.business.bl.payroll.calculator import calculate_payroll
from app.business.bl.payroll.gross_up import solve_electric_gross_up
from app.business.domain.comparisons import ScenarioStatus
from app.business.domain.errors import GrossUpNotConvergedError
from app.business.domain.payroll import ArrangementComponents
from app.business.domain.values import calculation_context
from app.business.domain.vehicles import Propulsion
from tests.unit.business.bl.helpers import calc_input, company_car, d, facts, rules, salary, vehicle

TOLERANCE = Decimal("0.000001")


def _net_gain(payroll_facts, components) -> tuple[Decimal, Decimal]:  # type: ignore[no-untyped-def]
    config = rules()
    gross_up = solve_electric_gross_up(payroll_facts, components, config)
    with_e = calculate_payroll(calc_input(payroll_facts, components, str(gross_up)), config).net
    without = calculate_payroll(calc_input(payroll_facts, components), config).net
    with calculation_context():
        return gross_up, with_e - without


@pytest.mark.parametrize(
    ("base", "value_of_use", "participation"),
    [
        ("18500", "3130", "300"),  # E-001 ATTO 3
        ("21000", "2430", "250"),  # E-003 MG4
        ("19000", "2930", "300"),  # E-004 EV3
        ("34000", "4150", "500"),  # E-006 MODEL 3
        ("19000", "3130", "300"),  # T crosses the 22,440 band during the gross-up
        ("45000", "5600", "500"),  # T crosses 46,690 and the insurance ceiling
        ("0", "0", "0"),
    ],
)
def test_gross_up_adds_exactly_250_net(base: str, value_of_use: str, participation: str) -> None:
    components = ArrangementComponents(
        value_of_use=d(value_of_use), ordinary_participation=d(participation), electric=True
    )
    gross_up, gain = _net_gain(facts(base_gross=base), components)
    assert gross_up > 0
    assert abs(gain - d("250")) <= TOLERANCE


def test_gross_up_is_not_in_contribution_base_but_is_taxed_and_insured() -> None:
    """T77: P/H from G only; tax and insurance bases include E."""
    components = ArrangementComponents(value_of_use=d("3130"), electric=True)
    gross_up = solve_electric_gross_up(facts(), components, rules())
    plain = calculate_payroll(calc_input(facts(), components), rules())
    grossed = calculate_payroll(calc_input(facts(), components, str(gross_up)), rules())
    assert grossed.pension_employee == plain.pension_employee
    assert grossed.study_fund_employee == plain.study_fund_employee
    with calculation_context():
        assert grossed.taxable_base == plain.taxable_base + gross_up
        assert grossed.insurance_base == plain.insurance_base + gross_up


def test_non_convergence_is_an_explicit_failure() -> None:
    strict = replace(rules(), gross_up=replace(rules().gross_up, max_iterations=1))
    components = ArrangementComponents(value_of_use=d("3130"), electric=True)
    with pytest.raises(GrossUpNotConvergedError):
        solve_electric_gross_up(facts(), components, strict)


def test_unbracketed_target_is_an_explicit_failure() -> None:
    tiny = replace(
        rules(),
        gross_up=replace(rules().gross_up, initial_upper_bound=d("1"), max_upper_bound=d("1")),
    )
    with pytest.raises(GrossUpNotConvergedError):
        solve_electric_gross_up(facts(), ArrangementComponents(electric=True), tiny)


def test_failed_gross_up_fails_only_that_alternative() -> None:
    strict = replace(rules(), gross_up=replace(rules().gross_up, max_iterations=1))
    outcome = build_comparison(
        salary(),
        None,
        [
            vehicle("CAR-10", "3130", "300", "4100", Propulsion.ELECTRIC),
            vehicle("CAR-13", "4570", "350", "4200", Propulsion.PETROL),
        ],
        strict,
    )
    statuses = {o.option_key: (o.status, o.error_code) for o in outcome.alternatives}
    assert statuses["vehicle:CAR-10"] == (ScenarioStatus.FAILED, "GROSS_UP_NOT_CONVERGED")
    assert statuses["vehicle:CAR-13"] == (ScenarioStatus.CALCULATED, None)


def test_plug_in_hybrid_gets_no_electric_reimbursement() -> None:
    """T53: SPORTAGE HEV is plug-in in the supplied data."""
    outcome = build_comparison(
        salary(),
        None,
        [vehicle("CAR-16", "4550", "500", "5150", Propulsion.PLUG_IN_HYBRID)],
        rules(),
    )
    result = outcome.alternatives[0].result
    assert result is not None and result.electric_gross_up == 0


def test_same_electric_car_and_terms_gives_zero_delta() -> None:
    """T52: the electric policy applies to the current baseline too."""
    outcome = build_comparison(
        salary(),
        company_car("3130", "300", Propulsion.ELECTRIC),
        [vehicle("CAR-10", "3130", "300", "4100", Propulsion.ELECTRIC)],
        rules(),
    )
    assert outcome.alternatives[0].delta == 0
