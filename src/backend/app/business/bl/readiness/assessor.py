"""Readiness of a comparison without calculating it (pure; no I/O).

Uses exactly the scenario builder and component functions `build_comparison` uses, so a
scenario reported `ready` is one the comparison calculates (barring a technical failure such
as a non-converging gross-up). No rule is defined here.
"""

from collections.abc import Sequence

from app.business.bl.benefits.calculator import alternative_components, baseline_components
from app.business.bl.payroll.scenario_builder import build_payroll_facts
from app.business.domain.arrangements import (
    CURRENT_OPTION_KEY,
    AlternativeSpec,
    BaselineSpec,
    IneligibleSpec,
)
from app.business.domain.employee_profiles import SalaryFacts
from app.business.domain.errors import OutOfSupportedRangeError
from app.business.domain.payroll import PayrollFacts
from app.business.domain.readiness import ReadinessAssessment, ReadinessStatus, ScenarioReadiness
from app.business.domain.results import MissingFact
from app.business.domain.rules import RulesConfig


def _components_readiness(
    option_key: str, components: object, payroll_missing: tuple[MissingFact, ...]
) -> ScenarioReadiness:
    own = components if isinstance(components, tuple) else ()
    missing = payroll_missing + own
    if missing:
        return ScenarioReadiness(option_key, ReadinessStatus.NEEDS_INFORMATION, missing)
    return ScenarioReadiness(option_key, ReadinessStatus.READY)


def assess_readiness(
    salary: SalaryFacts,
    baseline: BaselineSpec | None,
    alternatives: Sequence[AlternativeSpec],
    rules: RulesConfig,
) -> ReadinessAssessment:
    built = build_payroll_facts(salary, rules.limits)
    payroll_missing = built if not isinstance(built, PayrollFacts) else ()

    baseline_readiness = None
    if baseline is not None:
        try:
            components: object = baseline_components(baseline, rules)
        except OutOfSupportedRangeError:
            baseline_readiness = ScenarioReadiness(CURRENT_OPTION_KEY, ReadinessStatus.UNSUPPORTED)
        else:
            baseline_readiness = _components_readiness(
                CURRENT_OPTION_KEY, components, payroll_missing
            )

    results: list[ScenarioReadiness] = []
    for spec in alternatives:
        if isinstance(spec, IneligibleSpec):
            results.append(
                ScenarioReadiness(spec.option_key, ReadinessStatus.INELIGIBLE, reasons=spec.reasons)
            )
            continue
        try:
            parts: object = alternative_components(spec, rules)
        except OutOfSupportedRangeError:
            results.append(ScenarioReadiness(spec.option_key, ReadinessStatus.UNSUPPORTED))
            continue
        results.append(_components_readiness(spec.option_key, parts, payroll_missing))
    return ReadinessAssessment(payroll_missing, baseline_readiness, tuple(results))
