"""`compare_scenarios` and `get_comparison_details` (Step 4B).

One short Business Unit of Work per comparison:
  authorize (tool context) -> operation claim (replay returns the stored run) -> closed case
  rejected -> version checks -> lock profile, then case (`FOR UPDATE`, the same order as every
  profile + case write; versions re-checked under the lock) -> regular only: synchronize the
  case's actual copy to the latest profile -> pin one release -> resolve the selection ->
  plan -> BL calculation -> immutable run + operation result -> commit.

- `regular`: the latest actual profile only (hypothetical overrides are not applied);
  `expectedProfileVersion` must equal the latest version.
- `hypothetical`: the case's saved actual copy with its overrides (fixed baseline); an
  `expectedProfileVersion`, if given, must equal that copy's version (Q11).
- Independent alternatives are always calculated; a blocked one is reported with its reason
  (AC14). A baseline that is not reliable (Q2) is not calculated and every delta is null.
- Runs are never changed; freshness is evaluated on every read; details never recalculate.
"""

from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID, uuid4

from app.business.application.cases.alternatives import ensure_alternatives
from app.business.application.cases.confirmation import fields_requiring_confirmation
from app.business.application.cases.freshness import run_freshness
from app.business.application.comparisons.facts import basis_for, effective_profile
from app.business.application.comparisons.mapping import (
    baseline_result,
    scenario_result,
)
from app.business.application.comparisons.planner import Plan, ScenarioPlanner
from app.business.application.comparisons.selection import ResolvedSelection, resolve_selection
from app.business.application.comparisons.snapshot import build_input_snapshot
from app.business.application.errors import (
    InvalidStateTransition,
    NotFoundError,
    OutOfSupportedRange,
    VersionConflict,
    translate_errors,
)
from app.business.application.idempotency import canonical_request_hash
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.outcomes import Outcome
from app.business.application.ports.clock import Clock
from app.business.application.ports.reference_data import ReferenceDataRepository
from app.business.application.ports.repositories import OperationKey, Replay, ScopeKind
from app.business.application.ports.unit_of_work import (
    BusinessUnitOfWork,
    BusinessUnitOfWorkFactory,
)
from app.business.application.profiles.profile_service import load_profile
from app.business.application.reference import PinnedReference, pin_reference
from app.business.application.vehicles.catalogue_service import plain_json
from app.business.bl.comparisons.builder import build_comparison
from app.business.bl.readiness.assessor import assess_readiness
from app.business.domain.arrangements import AlternativeSpec
from app.business.domain.cases import (
    Alternative,
    CaseLifecycleStatus,
    ComparisonCase,
    ComparisonRunRecord,
)
from app.business.domain.cases import RunKind as DomainRunKind
from app.business.domain.comparisons import ComparisonOutcome
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.json_values import JsonValue, freeze_object
from app.business.domain.representatives import RequestStatus
from app.business.domain.rules import RulesConfig
from app.business.domain.vehicles import Track
from app.contracts.common import EnvelopeStatus, Freshness, Issue, IssueCode, RunKind
from app.contracts.comparisons import (
    Assumption,
    BaselineResult,
    CompareRequest,
    ComparisonDetails,
    ComparisonDetailsRequest,
    ComparisonResult,
    ScenarioResult,
    ScenarioStatus,
)
from app.contracts.tool_context import ToolContext

COMPARE = "compare_scenarios"
ELIGIBILITY_CHANGED = "eligibility_changed_since_list"


class ComparisonService:
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

    # --- compare_scenarios -------------------------------------------------------------

    async def compare_scenarios(
        self, ctx: ToolContext, request: CompareRequest
    ) -> Outcome[ComparisonResult]:
        request_hash = canonical_request_hash(request.model_dump(mode="json", by_alias=True))
        with translate_errors():
            async with self._unit_of_work() as uow:
                actor, case = await self._policy.tool_case(uow, ctx)
                key = OperationKey(
                    actor.subject_id, ScopeKind.CASE, case.case_id, COMPARE, request.operation_id
                )
                claim = await uow.operations.claim(key, request_hash)
                if isinstance(claim, Replay) and claim.result is not None:
                    return await self._replay(uow, case, str(claim.result["runId"]))
                if case.lifecycle_status is CaseLifecycleStatus.CLOSED:
                    raise InvalidStateTransition("a closed case is read-only")
                latest, case = await self._lock_and_check(uow, case, request)
                pinned = await pin_reference(self._reference_data)
                now = self._clock.now()
                if request.kind is RunKind.REGULAR and case.salary_profile_version_used != (
                    latest.version
                ):
                    case = await uow.cases.update(
                        replace(case, actual_facts=latest, updated_at=now)
                    )  # new regular calculation: synchronize to the latest profile (T70)
                basis = basis_for(request.kind)
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
                    persist=True,
                    now=now,
                )
                limit = pinned.rules.limits.max_alternatives_per_run
                if len(selected.option_keys) > limit:
                    raise OutOfSupportedRange(
                        f"at most {limit} alternatives per comparison",
                        safe_details={"field": "selection"},
                    )
                open_requests = await uow.representative_requests.list_for_case(
                    case.case_id, [RequestStatus.OPEN]
                )
                plan = await self._planner.plan(
                    pinned,
                    profile,
                    selected.option_keys,
                    include_baseline=request.include_current,
                    confirmation_required=fields_requiring_confirmation(profile, case.created_at),
                    open_requests=open_requests,
                )
                alternatives = {
                    a.option_key: a
                    for a in await ensure_alternatives(
                        uow,
                        self._reference_data,
                        pinned.release,
                        case,
                        tuple(selected.option_keys),
                        now=now,
                    )
                }
                outcome = _calculate(plan, pinned.rules)
                run_id = uuid4()
                result = _result(
                    run_id, request, case, profile, pinned, plan, outcome, alternatives, now
                )
                issues = _issues(selected, plan, result)
                snapshot = build_input_snapshot(
                    case=case,
                    basis=basis,
                    plan=plan,
                    pinned=pinned,
                    option_keys=selected.option_keys,
                    result_set_id=selected.result_set_id,
                    alternatives=alternatives,
                    include_current=request.include_current,
                )
                snapshot["issues"] = [i.model_dump(mode="json", by_alias=True) for i in issues]
                await uow.comparison_runs.add(
                    ComparisonRunRecord(
                        run_id=run_id,
                        case_id=case.case_id,
                        kind=DomainRunKind(request.kind.value),
                        calculation_month=case.calculation_month,
                        case_version_used=case.version,
                        profile_version_used=profile.version,
                        reference_release_id=pinned.release.release_id,
                        rules_snapshot_id=pinned.release.rules_snapshot_id,
                        catalogue_version_id=pinned.release.catalogue_version_id,
                        tax_dataset_version_id=pinned.release.tax_dataset_version_id,
                        selection=freeze_object(
                            request.selection.model_dump(mode="json", by_alias=True)
                        ),
                        include_current=request.include_current,
                        input_snapshot=freeze_object(snapshot),
                        result=freeze_object(result.model_dump(mode="json", by_alias=True)),
                        operation_id=request.operation_id,
                        created_by=actor.subject_id,
                        created_at=now,
                        option_result_set_id=selected.result_set_id,
                    )
                )
                await uow.operations.complete(key, freeze_object({"runId": str(run_id)}))
                await uow.commit()
        return Outcome(
            result,
            status=_status(result),
            issues=tuple(issues),
            case_version=case.version,
            profile_version=latest.version,
            source_versions=pinned.versions,
        )

    async def _lock_and_check(
        self, uow: BusinessUnitOfWork, case: ComparisonCase, request: CompareRequest
    ) -> tuple[EmployeeProfile, ComparisonCase]:
        """Lock order: profile, then case - identical to every profile + case write."""
        owner = case.owner_subject_id
        stored = await uow.profiles.lock(owner)
        latest = stored if stored is not None else EmployeeProfile(employee_id=owner, version=0)
        locked = await uow.cases.lock(case.case_id, owner_subject_id=owner)
        if locked is None:
            raise NotFoundError()
        if request.expected_case_version != locked.version:
            raise VersionConflict("the case changed in the meantime; reload first")
        if request.kind is RunKind.REGULAR:
            if request.expected_profile_version != latest.version:
                raise VersionConflict("the employee profile changed in the meantime")
        elif (
            request.expected_profile_version is not None
            and request.expected_profile_version != locked.salary_profile_version_used
        ):
            raise VersionConflict("the scenario's saved profile version differs")
        return latest, locked

    async def _replay(
        self, uow: BusinessUnitOfWork, case: ComparisonCase, run_id: str
    ) -> Outcome[ComparisonResult]:
        run = await uow.comparison_runs.get(case.case_id, UUID(run_id))
        if run is None:
            raise NotFoundError()
        result = await self._with_freshness(uow, case, run)
        issues = run.input_snapshot.get("issues", ())
        return Outcome(
            result,
            status=_status(result),
            issues=tuple(Issue.model_validate(plain_json(i)) for i in _sequence(issues)),
            case_version=case.version,
            profile_version=result.profile_version_used,
        )

    # --- get_comparison_details --------------------------------------------------------

    async def get_comparison_details(
        self, ctx: ToolContext, request: ComparisonDetailsRequest
    ) -> Outcome[ComparisonDetails]:
        with translate_errors():
            async with self._unit_of_work() as uow:
                _actor, case = await self._policy.tool_case(uow, ctx)
                run = await uow.comparison_runs.get(case.case_id, request.run_id)
                if run is None:
                    raise NotFoundError()
                result = await self._with_freshness(uow, case, run)
        snapshot = plain_json(run.input_snapshot)
        assert isinstance(snapshot, dict)
        details = ComparisonDetails(result=result, input_snapshot=snapshot)
        return Outcome(details, case_version=case.version)

    async def _with_freshness(
        self, uow: BusinessUnitOfWork, case: ComparisonCase, run: ComparisonRunRecord
    ) -> ComparisonResult:
        latest = await load_profile(uow, case.owner_subject_id)
        release = await self._reference_data.latest_release()
        freshness = run_freshness(
            run,
            case_version=case.version,
            latest_profile_version=latest.version,
            latest_release_id=None if release is None else release.release_id,
        )
        stored = ComparisonResult.model_validate(plain_json(run.result))
        return stored.model_copy(update={"freshness": freshness})


# --- helpers -----------------------------------------------------------------------------


def _sequence(value: JsonValue | None) -> Sequence[JsonValue]:
    return value if isinstance(value, tuple) else ()


def _calculate(plan: Plan, rules: RulesConfig) -> ComparisonOutcome:
    specs: list[AlternativeSpec] = [o.spec for o in plan.options if o.spec is not None]
    baseline = plan.baseline
    spec = baseline.spec if baseline is not None and baseline.calculable else None
    return build_comparison(plan.profile.salary, spec, specs, rules)


def _result(
    run_id: object,
    request: CompareRequest,
    case: ComparisonCase,
    profile: EmployeeProfile,
    pinned: PinnedReference,
    plan: Plan,
    outcome: ComparisonOutcome,
    alternatives: dict[str, Alternative],
    now: object,
) -> ComparisonResult:
    by_key = {o.option_key: o for o in outcome.alternatives}
    scenarios: list[ScenarioResult] = [
        scenario_result(
            option,
            by_key.get(option.option_key) if option.spec is not None else None,
            alternatives.get(option.option_key),
        )
        for option in plan.options
    ]
    current: BaselineResult | None = None
    if plan.baseline is not None:
        readiness = None
        if not plan.baseline.calculable and plan.baseline.spec is not None:
            readiness = assess_readiness(
                plan.profile.salary, plan.baseline.spec, [], pinned.rules
            ).baseline
        current = baseline_result(
            plan.baseline,
            outcome.baseline if plan.baseline.calculable else None,
            readiness,
        )
    return ComparisonResult.model_validate(
        {
            "run_id": run_id,
            "kind": request.kind,
            "calculation_month": case.calculation_month,
            "case_version_used": case.version,
            "profile_version_used": profile.version,
            "rules_snapshot_id": pinned.release.rules_snapshot_id,
            "catalogue_version_id": pinned.release.catalogue_version_id,
            "tax_dataset_version_id": pinned.release.tax_dataset_version_id,
            "current": current,
            "alternatives": scenarios,
            "assumptions": _assumptions(plan, scenarios, pinned),
            "freshness": Freshness.CURRENT,
            "created_at": now,
        }
    )


def _assumptions(
    plan: Plan, scenarios: list[ScenarioResult], pinned: PinnedReference
) -> list[Assumption]:
    """Disclosures grounded in the pinned sources only (decision Q8)."""
    calculated = {s.option_key for s in scenarios if s.status is ScenarioStatus.CALCULATED}
    assumptions: list[Assumption] = []
    tax_years: set[str] = set()
    for option in plan.options:
        vehicle = option.vehicle
        if vehicle is None or option.option_key not in calculated:
            continue
        tax_years.add(vehicle.tax_key.tax_year)
        assumptions.append(
            Assumption(code="participation.catalogue_default", option_key=option.option_key)
        )
        if (
            vehicle.track is Track.FAMILY
            and pinned.rules.derived.family_participation_mode == "base_plus_gap"
        ):
            assumptions.append(
                Assumption(
                    code="demo_policy.family_participation_base_plus_gap",
                    option_key=option.option_key,
                )
            )
    if plan.baseline is not None and plan.baseline.tax_key is not None:
        tax_years.add(plan.baseline.tax_key.tax_year)
    payroll_year = str(pinned.rules.tax_year)
    for year in sorted(tax_years - {payroll_year}):  # SRC-06: label both source years
        assumptions.insert(
            0, Assumption(code=f"demo_policy.payroll_tables_{payroll_year}_vehicle_data_{year}")
        )
    return assumptions


def _issues(selected: ResolvedSelection, plan: Plan, result: ComparisonResult) -> list[Issue]:
    issues: list[Issue] = list(selected.issues)
    for option in plan.options:
        listed = selected.listed_eligibility.get(option.option_key)
        if listed is not None and listed != option.decision.status.value:
            issues.append(
                Issue(
                    code=IssueCode.STALE_REVISION,
                    option_key=option.option_key,
                    reason=ELIGIBILITY_CHANGED,
                )
            )
    if result.current is not None:
        issues.extend(result.current.missing_information)
    for scenario in result.alternatives:
        issues.extend(scenario.missing_information)
    unique: list[Issue] = []
    for issue in issues:
        if issue not in unique:
            unique.append(issue)
    return unique


def _status(result: ComparisonResult) -> EnvelopeStatus:
    requested = [s.status for s in result.alternatives]
    if result.current is not None:
        requested.append(result.current.status)
    done = sum(1 for s in requested if s is ScenarioStatus.CALCULATED)
    decided = sum(
        1 for s in requested if s in {ScenarioStatus.CALCULATED, ScenarioStatus.INELIGIBLE}
    )
    if decided == len(requested) and done:
        return EnvelopeStatus.OK
    if done:
        return EnvelopeStatus.PARTIAL
    return EnvelopeStatus.NEEDS_INFORMATION
