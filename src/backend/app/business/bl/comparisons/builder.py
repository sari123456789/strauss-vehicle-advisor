"""Structured comparison construction without an LLM.

All scenarios share one rules snapshot and one set of payroll facts. A missing fact or a local
failure in one alternative never blocks the others. `delta` is the exact difference of precise
nets; display rounding is applied to that difference only (see `display_rounding`).
"""

from collections.abc import Sequence

from app.business.bl.benefits.calculator import alternative_components, baseline_components
from app.business.bl.payroll.calculator import calculate_payroll
from app.business.bl.payroll.gross_up import solve_electric_gross_up
from app.business.bl.payroll.scenario_builder import build_payroll_facts
from app.business.bl.rules.validator import validate_supported_rules
from app.business.domain.arrangements import (
    CURRENT_OPTION_KEY,
    AlternativeSpec,
    BaselineSpec,
    IneligibleSpec,
)
from app.business.domain.comparisons import ComparisonOutcome, ScenarioOutcome, ScenarioStatus
from app.business.domain.employee_profiles import SalaryFacts
from app.business.domain.errors import (
    GrossUpNotConvergedError,
    InvalidValueError,
    OutOfSupportedRangeError,
)
from app.business.domain.payroll import ArrangementComponents, PayrollCalculationInput, PayrollFacts
from app.business.domain.results import MissingFact
from app.business.domain.rules import RulesConfig
from app.business.domain.values import ZERO, calculation_context


def calculate_scenario(
    option_key: str,
    facts: PayrollFacts,
    components: ArrangementComponents,
    rules: RulesConfig,
) -> ScenarioOutcome:
    """Calculate one complete scenario, including the electric gross-up for that same scenario."""
    try:
        gross_up = (
            solve_electric_gross_up(facts, components, rules) if components.electric else ZERO
        )
    except GrossUpNotConvergedError as error:
        return ScenarioOutcome(option_key, ScenarioStatus.FAILED, error_code=error.code)
    result = calculate_payroll(PayrollCalculationInput(facts, components, gross_up), rules)
    return ScenarioOutcome(option_key, ScenarioStatus.CALCULATED, result=result)


def _evaluate(
    option_key: str,
    facts: PayrollFacts | None,
    payroll_missing: tuple[MissingFact, ...],
    components: ArrangementComponents | tuple[MissingFact, ...],
    rules: RulesConfig,
) -> ScenarioOutcome:
    if facts is None or isinstance(components, tuple):
        own_missing = components if isinstance(components, tuple) else ()
        return ScenarioOutcome(
            option_key, ScenarioStatus.NEEDS_INFORMATION, missing=payroll_missing + own_missing
        )
    return calculate_scenario(option_key, facts, components, rules)


def _alternative_outcome(
    spec: AlternativeSpec,
    facts: PayrollFacts | None,
    payroll_missing: tuple[MissingFact, ...],
    rules: RulesConfig,
) -> ScenarioOutcome:
    if isinstance(spec, IneligibleSpec):
        return ScenarioOutcome(spec.option_key, ScenarioStatus.INELIGIBLE, reasons=spec.reasons)
    try:
        components = alternative_components(spec, rules)
    except OutOfSupportedRangeError as error:
        return ScenarioOutcome(spec.option_key, ScenarioStatus.UNSUPPORTED, error_code=error.code)
    return _evaluate(spec.option_key, facts, payroll_missing, components, rules)


def build_comparison(
    salary: SalaryFacts,
    baseline: BaselineSpec | None,
    alternatives: Sequence[AlternativeSpec],
    rules: RulesConfig,
) -> ComparisonOutcome:
    validate_supported_rules(rules)
    if len(alternatives) > rules.limits.max_alternatives_per_run:
        raise OutOfSupportedRangeError("too many alternatives in one run", field="alternatives")
    keys = [spec.option_key for spec in alternatives]
    if len(keys) != len(set(keys)) or CURRENT_OPTION_KEY in keys:
        raise InvalidValueError("alternative option keys must be unique", field="alternatives")

    built = build_payroll_facts(salary, rules.limits)
    facts = built if isinstance(built, PayrollFacts) else None
    payroll_missing = built if isinstance(built, tuple) else ()

    baseline_outcome = None
    if baseline is not None:
        try:
            components = baseline_components(baseline, rules)
        except OutOfSupportedRangeError as error:
            baseline_outcome = ScenarioOutcome(
                CURRENT_OPTION_KEY, ScenarioStatus.UNSUPPORTED, error_code=error.code
            )
        else:
            baseline_outcome = _evaluate(
                CURRENT_OPTION_KEY, facts, payroll_missing, components, rules
            )

    outcomes = []
    for spec in alternatives:
        outcome = _alternative_outcome(spec, facts, payroll_missing, rules)
        if (
            baseline_outcome is not None
            and baseline_outcome.result is not None
            and outcome.result is not None
        ):
            with calculation_context():
                delta = outcome.result.net - baseline_outcome.result.net
            outcome = ScenarioOutcome(
                outcome.option_key, outcome.status, result=outcome.result, delta=delta
            )
        outcomes.append(outcome)

    return ComparisonOutcome(
        baseline=baseline_outcome, alternatives=tuple(outcomes), payroll_missing=payroll_missing
    )
