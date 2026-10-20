"""Plans and BL outcomes -> shared contract DTOs (comparison results and readiness).

Amounts: `raw` is the exact Decimal as a plain decimal string (no quantization); `display` is
whole-shekel half-away-from-zero rounding of that raw value. A delta's display value rounds
the precise difference, never a difference of rounded nets. Unknown amounts stay null.
"""

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from app.business.application.comparisons.planner import BaselinePlan, OptionPlan, Plan
from app.business.application.vehicles.facts import missing_issues
from app.business.application.vehicles.mapping import contract_key
from app.business.bl.display_rounding import display_round
from app.business.domain.cases import Alternative
from app.business.domain.comparisons import ScenarioOutcome
from app.business.domain.payroll import PayrollResult
from app.business.domain.readiness import ReadinessAssessment, ReadinessStatus, ScenarioReadiness
from app.business.domain.representatives import RepresentativeRequest
from app.business.domain.results import MissingFact, MissingReason
from app.business.domain.vehicles import Track
from app.contracts.catalogue import EligibilityReason
from app.contracts.common import FieldPath, Issue, IssueCode
from app.contracts.common import Track as ContractTrack
from app.contracts.comparisons import (
    BaselineResult,
    CalculationBreakdown,
    MoneyAmount,
    ScenarioResult,
    ScenarioStatus,
    VehicleSource,
)
from app.contracts.readiness import BlockedScenario, PendingRepresentativeField, ReadinessResult
from app.contracts.representatives import RepresentativeFieldCode

CONFIRMATION_REQUIRED = "confirmation_required"
_FIELDS = frozenset(p.value for p in FieldPath)


def amount(value: Decimal) -> MoneyAmount:
    return MoneyAmount(raw=format(value, "f"), display=format(display_round(value), "f"))


def breakdown(
    result: PayrollResult, *, monthly_cost: Decimal | None = None, budget: Decimal | None = None
) -> CalculationBreakdown:
    return CalculationBreakdown(
        earned_gross=amount(result.earned_gross),
        allowance=amount(result.allowance),
        electric_gross_up=amount(result.electric_gross_up),
        value_of_use=amount(result.value_of_use),
        taxable_base=amount(result.taxable_base),
        pension_employee=amount(result.pension_employee),
        study_fund_employee=amount(result.study_fund_employee),
        credit_points_credit=amount(result.credit_points_credit),
        pension_credit=amount(result.pension_credit),
        income_tax_before_credits=amount(result.income_tax_before_credits),
        income_tax=amount(result.income_tax),
        national_insurance=amount(result.national_insurance),
        health_insurance=amount(result.health_insurance),
        ordinary_participation=amount(result.ordinary_participation),
        family_gap_contribution=amount(result.family_gap_contribution),
        monthly_company_cost=None if monthly_cost is None else amount(monthly_cost),
        group_budget=None if budget is None else amount(budget),
    )


def _status_issues(outcome: ScenarioOutcome) -> list[Issue]:
    issues = missing_issues(outcome.missing, per_option=True)
    if outcome.error_code is not None:
        issues.append(
            Issue(
                code=IssueCode.UNSUPPORTED_INPUT,
                option_key=outcome.option_key if outcome.option_key != "current" else None,
                reason=outcome.error_code,
            )
        )
    return issues


def scenario_result(
    plan: OptionPlan, outcome: ScenarioOutcome | None, alternative: Alternative | None
) -> ScenarioResult:
    vehicle = plan.vehicle
    reasons = [EligibilityReason(r.value) for r in plan.decision.reasons]
    common = {
        "option_key": plan.option_key,
        "alternative_id": None if alternative is None else alternative.alternative_id,
        "alternative_version": None if alternative is None else alternative.version,
        "track": None if vehicle is None else ContractTrack(vehicle.track.value),
        "eligibility_reasons": reasons,
        "vehicle": None
        if vehicle is None
        else VehicleSource(
            catalogue_id=vehicle.catalogue_id,
            tax_key=contract_key(vehicle.tax_key),
            participation_source=getattr(plan.spec, "participation_source", None)
            or "catalogue_default",
        ),
    }
    if outcome is None:  # blocked before calculation
        missing = missing_issues(plan.decision.missing, per_option=True)
        missing = [m.model_copy(update={"option_key": plan.option_key}) for m in missing]
        return ScenarioResult(
            status=ScenarioStatus.NEEDS_INFORMATION,
            missing_information=missing + list(plan.blockers),
            **common,
        )
    result = outcome.result
    budget = None
    cost = None
    if vehicle is not None and vehicle.track is Track.FAMILY and result is not None:
        cost = vehicle.monthly_company_cost
        budget = _family_budget(plan)
    return ScenarioResult(
        status=ScenarioStatus(outcome.status.value),
        net=None if result is None else amount(result.net),
        delta=None if outcome.delta is None else amount(outcome.delta),
        breakdown=None if result is None else breakdown(result, monthly_cost=cost, budget=budget),
        missing_information=_status_issues(outcome),
        **common,
    )


def _family_budget(plan: OptionPlan) -> Decimal | None:
    spec = plan.spec
    budget = getattr(spec, "group_budget", None)
    return None if budget is None or not budget.is_usable else budget.value


def baseline_result(
    plan: BaselinePlan, outcome: ScenarioOutcome | None, readiness: ScenarioReadiness | None
) -> BaselineResult:
    vehicle = VehicleSource(
        catalogue_id=plan.catalogue_id,
        tax_key=None if plan.tax_key is None else contract_key(plan.tax_key),
        participation_source="employee_reported",
    )
    has_vehicle = plan.catalogue_id is not None or plan.tax_key is not None
    if outcome is None:
        missing = [] if readiness is None else missing_issues(readiness.missing)
        return BaselineResult(
            status=ScenarioStatus.NEEDS_INFORMATION,
            vehicle=vehicle if has_vehicle else None,
            missing_information=missing + list(plan.blockers),
        )
    result = outcome.result
    return BaselineResult(
        status=ScenarioStatus(outcome.status.value),
        net=None if result is None else amount(result.net),
        breakdown=None if result is None else breakdown(result),
        vehicle=vehicle if has_vehicle else None,
        missing_information=_status_issues(outcome),
    )


# --- readiness ---------------------------------------------------------------------------


def _paths(facts: Iterable[MissingFact], reason: MissingReason) -> list[FieldPath]:
    return [FieldPath(f.field) for f in facts if f.reason is reason and f.field in _FIELDS]


def _ordered(paths: Iterable[FieldPath]) -> list[FieldPath]:
    wanted = set(paths)
    return [p for p in FieldPath if p in wanted]


def readiness_result(
    plan: Plan,
    assessment: ReadinessAssessment,
    *,
    baseline_required: bool,
) -> tuple[ReadinessResult, list[Issue]]:
    """(result, envelope issues). Confirmation-required facts are reported as issues only:
    they are neither missing nor ambiguous (decision Q2)."""
    by_key = {r.option_key: r for r in assessment.alternatives}
    missing: list[MissingFact] = list(assessment.payroll_missing)
    issues: list[Issue] = []
    ambiguous: list[FieldPath] = []
    ready: list[str] = []
    blocked: list[BlockedScenario] = []
    for option in plan.options:
        if option.spec is None:
            missing.extend(option.decision.missing)
            for blocker in option.blockers:
                if blocker.code is IssueCode.AMBIGUOUS_INPUT and blocker.field is not None:
                    ambiguous.append(blocker.field)
            reason = option.blockers[0].code if option.blockers else _code(option.decision.missing)
            fields = [b.field for b in option.blockers if b.field is not None]
            fields += _paths(option.decision.missing, MissingReason.MISSING)
            fields += _paths(option.decision.missing, MissingReason.NEEDS_CLARIFICATION)
            blocked.append(
                BlockedScenario(
                    option_key=option.option_key, reason=reason, fields=_ordered(fields)
                )
            )
            issues.extend(option.blockers)
            continue
        state = by_key[option.option_key]
        missing.extend(state.missing)
        if state.status is ReadinessStatus.READY:
            ready.append(option.option_key)
            continue
        code = {
            ReadinessStatus.INELIGIBLE: IssueCode.INELIGIBLE,
            ReadinessStatus.UNSUPPORTED: IssueCode.UNSUPPORTED_INPUT,
        }.get(state.status, _code(state.missing))
        own = [m for m in state.missing if m not in assessment.payroll_missing]
        fields = _paths(own, MissingReason.MISSING) + _paths(own, MissingReason.NEEDS_CLARIFICATION)
        if state.status is ReadinessStatus.NEEDS_INFORMATION and not own:
            fields = _paths(state.missing, MissingReason.MISSING) + _paths(
                state.missing, MissingReason.NEEDS_CLARIFICATION
            )
        blocked.append(
            BlockedScenario(option_key=option.option_key, reason=code, fields=_ordered(fields))
        )

    baseline_ready: bool | None = None
    if baseline_required:
        baseline = plan.baseline
        baseline_state = assessment.baseline
        if baseline_state is not None:
            missing.extend(baseline_state.missing)
        if baseline is not None:
            issues.extend(baseline.blockers)
            ambiguous.extend(
                b.field
                for b in baseline.blockers
                if b.field is not None
                and b.code in {IssueCode.AMBIGUOUS_INPUT, IssueCode.SOURCE_CONFLICT}
                and b.reason != CONFIRMATION_REQUIRED
            )
            missing_kind = [
                b.field
                for b in baseline.blockers
                if b.code is IssueCode.MISSING_INPUT and b.field is not None
            ]
            missing.extend(
                MissingFact(field=f.value, reason=MissingReason.MISSING) for f in missing_kind
            )
        baseline_ready = (
            baseline is not None
            and baseline.calculable
            and baseline_state is not None
            and baseline_state.status is ReadinessStatus.READY
        )

    missing_paths = _paths(missing, MissingReason.MISSING)
    ambiguous += _paths(missing, MissingReason.NEEDS_CLARIFICATION)
    issues.extend(missing_issues(missing))
    complete = (
        all(b.reason is IssueCode.INELIGIBLE for b in blocked) and baseline_ready is not False
    )
    result = ReadinessResult(
        ready_scenario_ids=ready,
        missing_employee_fields=_ordered(missing_paths),
        ambiguous_fields=_ordered(ambiguous),
        pending_representative_fields=pending_fields(plan.pending),
        blocked_scenarios=blocked,
        baseline_ready=baseline_ready,
        scope_complete=complete,
    )
    return result, _unique(issues)


def _code(missing: Sequence[MissingFact]) -> IssueCode:
    if any(m.reason is MissingReason.NEEDS_CLARIFICATION for m in missing):
        return IssueCode.AMBIGUOUS_INPUT
    return IssueCode.MISSING_INPUT


def pending_fields(requests: Iterable[RepresentativeRequest]) -> list[PendingRepresentativeField]:
    return [
        PendingRepresentativeField(
            request_id=r.request_id,
            field_code=RepresentativeFieldCode(r.field_code.value),
            option_key=r.option_key,
        )
        for r in requests
    ]


def _unique(issues: Iterable[Issue]) -> list[Issue]:
    seen: list[Issue] = []
    for issue in issues:
        if issue not in seen:
            seen.append(issue)
    return seen


def alternatives_by_key(alternatives: Iterable[Alternative]) -> Mapping[str, Alternative]:
    return {a.option_key: a for a in alternatives}
