"""`check_calculation_readiness` (Step 4B): goal-specific, read-only, nothing persisted.

Only the facts the goal needs are reported (architecture §14, agent-spec §6):
- `list_regular`: the entitlement group. `list_family`: the qualifying-child count only -
  family eligibility does not depend on group ranking; the group budget is a calculation
  requirement of a family alternative, not a listing requirement. `waiver_eligibility`: the
  occupation and its table policy. None of them needs salary.
- `calculate_net`: salary, eligibility and each alternative's components (no baseline unless
  `includeCurrent`). `compare_to_current`: additionally the current arrangement.

Calculation goals use the same planner and BL component checks as `compare_scenarios`, so a
scenario reported ready is one the comparison calculates. Until the shared contract gains a
`kind` (decision Q1, pending joint approval) readiness evaluates regular calculations: the
latest actual profile. Listing goals use the listing facts of 4A.
"""

from collections.abc import Sequence
from typing import Any

from app.business.application.cases.confirmation import fields_requiring_confirmation
from app.business.application.comparisons.facts import FactBasis, effective_profile
from app.business.application.comparisons.mapping import pending_fields, readiness_result
from app.business.application.comparisons.planner import ScenarioPlanner
from app.business.application.comparisons.selection import resolve_selection
from app.business.application.errors import InvalidInput, VersionConflict, translate_errors
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.outcomes import Outcome
from app.business.application.ports.clock import Clock
from app.business.application.ports.reference_data import ReferenceDataRepository
from app.business.application.ports.unit_of_work import BusinessUnitOfWorkFactory
from app.business.application.profiles.profile_service import load_profile
from app.business.application.reference import PinnedReference, pin_reference
from app.business.application.vehicles.facts import eligibility_facts
from app.business.bl.eligibility.evaluator import evaluate_waiver_option
from app.business.bl.facts import require_usable
from app.business.bl.readiness.assessor import assess_readiness
from app.business.domain.arrangements import WAIVER_OPTION_KEY, AlternativeSpec
from app.business.domain.eligibility import EligibilityStatus
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.provenance import FieldValue
from app.business.domain.representatives import RepresentativeRequest, RequestStatus
from app.business.domain.results import MissingFact, MissingReason
from app.contracts.common import EnvelopeStatus, FieldPath, Issue, IssueCode
from app.contracts.readiness import (
    BlockedScenario,
    ReadinessGoal,
    ReadinessRequest,
    ReadinessResult,
)
from app.contracts.tool_context import ToolContext

LIST_FIELDS = {
    ReadinessGoal.LIST_REGULAR: FieldPath.ENTITLEMENT_GROUP,
    ReadinessGoal.LIST_FAMILY: FieldPath.QUALIFYING_CHILDREN_COUNT,
}
CALCULATION_GOALS = frozenset({ReadinessGoal.CALCULATE_NET, ReadinessGoal.COMPARE_TO_CURRENT})


class ReadinessService:
    def __init__(
        self,
        unit_of_work: BusinessUnitOfWorkFactory,
        policy: AuthorizationPolicy,
        reference_data: ReferenceDataRepository,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._reference_data = reference_data
        self._planner = ScenarioPlanner(reference_data)
        self._clock = clock

    async def check_calculation_readiness(
        self, ctx: ToolContext, request: ReadinessRequest
    ) -> Outcome[ReadinessResult]:
        with translate_errors():
            async with self._unit_of_work() as uow:
                actor, case = await self._policy.tool_case(uow, ctx)
                if request.expected_case_version != case.version:
                    raise VersionConflict("the case changed in the meantime; reload first")
                latest = await load_profile(uow, actor.subject_id)
                pinned = await pin_reference(self._reference_data)
                goal = request.goal
                if goal not in CALCULATION_GOALS:
                    basis = FactBasis.LATEST_WITH_OVERRIDES
                    profile = effective_profile(basis, case, latest)
                    open_requests = await uow.representative_requests.list_for_case(
                        case.case_id, [RequestStatus.OPEN]
                    )
                    result = await self._listing_readiness(goal, profile, pinned, open_requests)
                    issues: list[Issue] = []
                else:
                    if request.selection is None:
                        raise InvalidInput("a calculation goal needs a selection")
                    basis = FactBasis.ACTUAL  # regular until Q1 adds `kind` to the contract
                    profile = effective_profile(basis, case, latest)
                    occupation = await self._planner.occupation(pinned, profile)
                    selected = await resolve_selection(
                        request.selection,
                        uow=uow,
                        reference_data=self._reference_data,
                        case=case,
                        pinned=pinned,
                        profile=profile,
                        occupation=occupation,
                        basis=basis,
                        persist=False,
                        now=self._clock.now(),
                    )
                    open_requests = await uow.representative_requests.list_for_case(
                        case.case_id, [RequestStatus.OPEN]
                    )
                    baseline_required = (
                        goal is ReadinessGoal.COMPARE_TO_CURRENT or request.include_current
                    )
                    plan = await self._planner.plan(
                        pinned,
                        profile,
                        selected.option_keys,
                        include_baseline=baseline_required,
                        confirmation_required=fields_requiring_confirmation(
                            profile, case.created_at
                        ),
                        open_requests=open_requests,
                    )
                    specs: list[AlternativeSpec] = [
                        o.spec for o in plan.options if o.spec is not None
                    ]
                    baseline_spec = None if plan.baseline is None else plan.baseline.spec
                    assessment = assess_readiness(
                        profile.salary, baseline_spec, specs, pinned.rules
                    )
                    result, issues = readiness_result(
                        plan, assessment, baseline_required=baseline_required
                    )
                    issues = selected.issues + issues
        status = EnvelopeStatus.OK if result.scope_complete else EnvelopeStatus.NEEDS_INFORMATION
        if not result.scope_complete and result.ready_scenario_ids:
            status = EnvelopeStatus.PARTIAL
        return Outcome(
            result,
            status=status,
            issues=tuple(issues),
            case_version=case.version,
            profile_version=latest.version,
            source_versions=pinned.versions,
        )

    async def _listing_readiness(
        self,
        goal: ReadinessGoal,
        profile: EmployeeProfile,
        pinned: PinnedReference,
        open_requests: Sequence[RepresentativeRequest],
    ) -> ReadinessResult:
        missing: list[MissingFact] = []
        if goal in LIST_FIELDS:
            path = LIST_FIELDS[goal]
            field: FieldValue[Any] = (
                profile.entitlement_group
                if path is FieldPath.ENTITLEMENT_GROUP
                else profile.qualifying_children_count
            )
            require_usable(field, path.value, missing)
            return _from_missing(missing, ready=[], blocked=[], pending=[])
        occupation = await self._planner.occupation(pinned, profile)
        decision = evaluate_waiver_option(eligibility_facts(profile), occupation, pinned.rules)
        requests = [r for r in open_requests if r.option_key in (None, WAIVER_OPTION_KEY)]
        if decision.status is EligibilityStatus.ELIGIBLE:
            return _from_missing([], ready=[WAIVER_OPTION_KEY], blocked=[], pending=requests)
        if decision.status is EligibilityStatus.INELIGIBLE:
            blocked = [BlockedScenario(option_key=WAIVER_OPTION_KEY, reason=IssueCode.INELIGIBLE)]
            return _from_missing([], ready=[], blocked=blocked, pending=requests)
        facts = list(decision.missing) or [
            MissingFact(
                field=FieldPath.OCCUPATION_ID.value, reason=MissingReason.NEEDS_CLARIFICATION
            )
        ]
        code = IssueCode.MISSING_INPUT if decision.missing else IssueCode.AMBIGUOUS_INPUT
        blocked = [
            BlockedScenario(
                option_key=WAIVER_OPTION_KEY, reason=code, fields=[FieldPath.OCCUPATION_ID]
            )
        ]
        return _from_missing(facts, ready=[], blocked=blocked, pending=requests)


def _from_missing(
    missing: list[MissingFact],
    *,
    ready: list[str],
    blocked: list[BlockedScenario],
    pending: Sequence[RepresentativeRequest],
) -> ReadinessResult:
    missing_fields = [FieldPath(m.field) for m in missing if m.reason is MissingReason.MISSING]
    unclear = [FieldPath(m.field) for m in missing if m.reason is MissingReason.NEEDS_CLARIFICATION]
    return ReadinessResult(
        ready_scenario_ids=ready,
        missing_employee_fields=missing_fields,
        ambiguous_fields=unclear,
        pending_representative_fields=pending_fields(pending),
        blocked_scenarios=blocked,
        baseline_ready=None,
        scope_complete=not missing and all(b.reason is IssueCode.INELIGIBLE for b in blocked),
    )
