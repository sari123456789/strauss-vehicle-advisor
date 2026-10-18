"""AC02 / AC14 / T08-T10 / T62: partial results, independence, one snapshot and determinism."""

from dataclasses import replace
from decimal import Decimal, getcontext, localcontext

import pytest

from app.business.bl.comparisons.builder import build_comparison
from app.business.bl.display_rounding import display_round
from app.business.domain.arrangements import IneligibleSpec
from app.business.domain.comparisons import ScenarioStatus
from app.business.domain.errors import (
    InvalidValueError,
    OutOfSupportedRangeError,
    UnsupportedPolicyError,
)
from app.business.domain.provenance import FieldValue
from app.business.domain.results import MissingFact, MissingReason
from app.business.domain.rules import ElectricRule
from app.business.domain.values import calculation_context
from app.business.domain.vehicles import Propulsion
from tests.unit.business.bl.helpers import company_car, d, rules, salary, usable, vehicle

ATTO = vehicle("CAR-10", "3130", "300", "4100", Propulsion.ELECTRIC)
MAZDA = vehicle("CAR-13", "4570", "350", "4200")
EV3_PENDING = replace(
    vehicle("CAR-11", "2930", "300", "4350", Propulsion.ELECTRIC), participation=FieldValue()
)
MODEL3 = IneligibleSpec("vehicle:CAR-14", ("group_higher_than_entitlement",))


def test_alternative_net_without_current_arrangement() -> None:
    """T08: no baseline requested -> nets without deltas."""
    outcome = build_comparison(salary(), None, [ATTO, MAZDA], rules())
    assert outcome.baseline is None
    assert all(
        o.status is ScenarioStatus.CALCULATED and o.delta is None for o in outcome.alternatives
    )


def test_blocked_alternative_does_not_block_ready_ones() -> None:
    """AC14 / T31: ready, pending and ineligible alternatives side by side."""
    outcome = build_comparison(
        salary(), company_car("4070", "300"), [ATTO, EV3_PENDING, MODEL3], rules()
    )
    by_key = {o.option_key: o for o in outcome.alternatives}
    assert by_key["vehicle:CAR-10"].status is ScenarioStatus.CALCULATED
    assert by_key["vehicle:CAR-10"].delta is not None
    assert by_key["vehicle:CAR-11"].status is ScenarioStatus.NEEDS_INFORMATION
    assert by_key["vehicle:CAR-11"].result is None
    assert by_key["vehicle:CAR-14"].status is ScenarioStatus.INELIGIBLE
    assert by_key["vehicle:CAR-14"].reasons == ("group_higher_than_entitlement",)


def test_missing_baseline_fact_removes_deltas_but_keeps_nets() -> None:
    """T10: current participation missing -> no invented delta."""
    baseline = replace(company_car("4070", "300"), participation=FieldValue())
    outcome = build_comparison(salary(), baseline, [ATTO], rules())
    assert outcome.baseline is not None
    assert outcome.baseline.status is ScenarioStatus.NEEDS_INFORMATION
    assert outcome.baseline.missing == (
        MissingFact("currentArrangement.participation", MissingReason.MISSING, "current"),
    )
    assert outcome.alternatives[0].result is not None and outcome.alternatives[0].delta is None


def test_missing_salary_blocks_calculation_but_not_ineligibility() -> None:
    """T11: no average-salary fallback."""
    outcome = build_comparison(
        replace(salary(), base_gross=FieldValue()), None, [ATTO, MODEL3], rules()
    )
    expected = MissingFact("payroll.baseGross", MissingReason.MISSING)
    assert outcome.payroll_missing == (expected,)
    assert outcome.alternatives[0].status is ScenarioStatus.NEEDS_INFORMATION
    assert expected in outcome.alternatives[0].missing
    assert outcome.alternatives[1].status is ScenarioStatus.INELIGIBLE


def test_one_consistent_baseline_for_every_row() -> None:
    """T09: every delta uses the same baseline net."""
    outcome = build_comparison(salary(), company_car("4070", "300"), [ATTO, MAZDA], rules())
    assert outcome.baseline is not None and outcome.baseline.result is not None
    for alternative in outcome.alternatives:
        assert alternative.result is not None
        with calculation_context():
            expected = alternative.result.net - outcome.baseline.result.net
        assert alternative.delta == expected


def test_out_of_range_local_value_marks_only_that_alternative_unsupported() -> None:
    huge = replace(MAZDA, participation=usable(d("2000000")))
    outcome = build_comparison(salary(), None, [ATTO, huge], rules())
    statuses = [o.status for o in outcome.alternatives]
    assert statuses == [ScenarioStatus.CALCULATED, ScenarioStatus.UNSUPPORTED]


def test_identical_inputs_produce_identical_outputs() -> None:
    first = build_comparison(salary(), company_car("4070", "300"), [ATTO, MAZDA], rules())
    second = build_comparison(salary(), company_car("4070", "300"), [ATTO, MAZDA], rules())
    assert first == second


def test_results_do_not_depend_on_the_callers_decimal_context() -> None:
    reference = build_comparison(salary(), company_car("4070", "300"), [ATTO], rules())
    with localcontext() as ctx:
        ctx.prec = 6
        assert getcontext().prec == 6
        low_precision = build_comparison(salary(), company_car("4070", "300"), [ATTO], rules())
    assert low_precision == reference
    assert reference.alternatives[0].delta is not None
    assert display_round(reference.alternatives[0].delta) == Decimal(661)


def test_duplicate_option_keys_are_rejected() -> None:
    with pytest.raises(InvalidValueError):
        build_comparison(salary(), None, [ATTO, ATTO], rules())


def test_alternative_limit_is_enforced() -> None:
    many = [vehicle(f"CAR-{i:02d}", "3000", "300", "4000") for i in range(1, 32)]
    with pytest.raises(OutOfSupportedRangeError):
        build_comparison(salary(), None, many, rules())


def test_unsupported_rules_policy_is_rejected() -> None:
    gross_mode = replace(rules(), electric=ElectricRule(d("250"), "gross"))
    with pytest.raises(UnsupportedPolicyError):
        build_comparison(salary(), None, [ATTO], gross_mode)
