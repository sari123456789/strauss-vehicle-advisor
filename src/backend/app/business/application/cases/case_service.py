"""Business case use cases (architecture §7-§8, §10; decisions B4, B5, B7).

Every method runs in one short Business Unit of Work: authorize (revalidated actor, owned
case, matching conversation) -> idempotency claim -> version checks -> change -> record the
result -> commit. Nothing is committed on any error. Tools never pass identity: the actor and
case come from the server-built `ToolContext`.

- Actual updates change the shared EmployeeProfile and this case's actual working copy in the
  same transaction, version-checked (profile first, then case). Hypothetical updates change
  only this case's overrides; the profile is never touched.
- Closed cases are read-only (B5); there is no close operation in this stage.
- A conversation already bound to another owner is `NOT_FOUND` (B7).
"""

from dataclasses import replace
from datetime import date
from uuid import uuid4

from app.business.application.cases.changes import (
    FactChange,
    apply_hypothetical_changes,
    effective_value,
    parse_changes,
)
from app.business.application.cases.freshness import run_freshness
from app.business.application.cases.mapping import case_context
from app.business.application.errors import (
    InfrastructureUnavailable,
    InvalidInput,
    InvalidStateTransition,
    NotFoundError,
    VersionConflict,
    translate_errors,
)
from app.business.application.idempotency import canonical_request_hash
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.ports.clock import Clock
from app.business.application.ports.reference_data import PublishedRelease, ReferenceDataRepository
from app.business.application.ports.repositories import (
    DuplicateResourceError,
    OperationKey,
    Replay,
    ScopeKind,
)
from app.business.application.ports.unit_of_work import (
    BusinessUnitOfWork,
    BusinessUnitOfWorkFactory,
)
from app.business.application.profiles.profile_service import EmployeeProfileService, load_profile
from app.business.domain.cases import CaseLifecycleStatus, ComparisonCase, FactPath
from app.business.domain.json_values import freeze_object
from app.business.domain.representatives import RequestStatus
from app.business.domain.rules import RulesConfig
from app.business.domain.vehicles import VehicleRef
from app.contracts.case_inputs import UpdateInputsRequest, UpdateInputsResult
from app.contracts.case_lifecycle import CreateCaseCommand, CreateCaseResult
from app.contracts.cases import CaseContext
from app.contracts.common import Freshness, Issue, IssueCode, UpdateMode
from app.contracts.tool_context import ActorRef, ToolContext

CREATE_CASE = "create_case"
UPDATE_INPUTS = "update_conversation_inputs"
RECENT_RUNS = 20


class CaseService:
    def __init__(
        self,
        unit_of_work: BusinessUnitOfWorkFactory,
        policy: AuthorizationPolicy,
        profiles: EmployeeProfileService,
        reference_data: ReferenceDataRepository,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._profiles = profiles
        self._reference_data = reference_data
        self._clock = clock

    # --- create ------------------------------------------------------------------------

    async def create_case_for_conversation(
        self, actor: ActorRef, command: CreateCaseCommand
    ) -> CreateCaseResult:
        request_hash = canonical_request_hash(command.model_dump(mode="json", by_alias=True))
        with translate_errors():
            async with self._unit_of_work() as uow:
                current = await self._policy.employee(uow, actor)
                key = OperationKey(
                    current.subject_id,
                    ScopeKind.CONVERSATION,
                    command.conversation_id,
                    CREATE_CASE,
                    command.operation_id,
                )
                claim = await uow.operations.claim(key, request_hash)
                if isinstance(claim, Replay) and claim.result is not None:
                    replayed = CreateCaseResult.model_validate(dict(claim.result))
                    return replayed.model_copy(update={"created": False})
                existing = await uow.cases.get_by_conversation(
                    command.conversation_id, owner_subject_id=current.subject_id
                )
                if existing is not None:
                    result = _created(existing, created=False)
                else:
                    now = self._clock.now()
                    year, month = (int(p) for p in command.calculation_month.split("-"))
                    case = ComparisonCase(
                        case_id=uuid4(),
                        conversation_id=command.conversation_id,
                        owner_subject_id=current.subject_id,
                        calculation_month=command.calculation_month,
                        reference_date=date(year, month, 1),
                        version=0,
                        lifecycle_status=CaseLifecycleStatus.ACTIVE,
                        actual_facts=await load_profile(uow, current.subject_id),
                        created_at=now,
                        updated_at=now,
                    )
                    try:
                        await uow.cases.add(case)
                    except DuplicateResourceError:
                        raise NotFoundError() from None  # another owner's conversation (B7)
                    result = _created(case, created=True)
                stored = result.model_copy(update={"created": True})
                await uow.operations.complete(
                    key, freeze_object(stored.model_dump(mode="json", by_alias=True))
                )
                await uow.commit()
                return result

    # --- read --------------------------------------------------------------------------

    async def get_context(self, ctx: ToolContext) -> CaseContext:
        with translate_errors():
            async with self._unit_of_work() as uow:
                actor, case = await self._policy.tool_case(uow, ctx)
                profile = await load_profile(uow, actor.subject_id)
                alternatives = await uow.alternatives.list_for_case(case.case_id)
                requests = await uow.representative_requests.list_for_case(
                    case.case_id, [RequestStatus.OPEN]
                )
                runs = await uow.comparison_runs.list_recent(case.case_id, RECENT_RUNS)
            release = await self._reference_data.latest_release()
            release_id = None if release is None else release.release_id
            freshness = [
                (
                    run,
                    run_freshness(
                        run,
                        case_version=case.version,
                        latest_profile_version=profile.version,
                        latest_release_id=release_id,
                    ),
                )
                for run in runs
            ]
            return case_context(case, profile, alternatives, requests, freshness)

    # --- update ------------------------------------------------------------------------

    async def update_inputs(
        self, ctx: ToolContext, request: UpdateInputsRequest
    ) -> UpdateInputsResult:
        request_hash = canonical_request_hash(request.model_dump(mode="json", by_alias=True))
        with translate_errors():
            async with self._unit_of_work() as uow:
                actor, case = await self._policy.tool_case(uow, ctx)
                key = OperationKey(
                    actor.subject_id,
                    ScopeKind.CASE,
                    case.case_id,
                    UPDATE_INPUTS,
                    request.operation_id,
                )
                claim = await uow.operations.claim(key, request_hash)
                if isinstance(claim, Replay) and claim.result is not None:
                    return UpdateInputsResult.model_validate(dict(claim.result))
                if case.lifecycle_status is CaseLifecycleStatus.CLOSED:
                    raise InvalidStateTransition("a closed case is read-only")
                if request.expected_case_version != case.version:
                    raise VersionConflict("the case changed in the meantime; reload first")
                release, rules = await self._pinned_reference()
                changes = parse_changes(request.changes, rules.limits)
                await self._check_identifiers(changes, release)
                result = await self._apply(uow, case, request, changes, release)
                await uow.operations.complete(
                    key, freeze_object(result.model_dump(mode="json", by_alias=True))
                )
                await uow.commit()
                return result

    async def _apply(
        self,
        uow: BusinessUnitOfWork,
        case: ComparisonCase,
        request: UpdateInputsRequest,
        changes: tuple[FactChange, ...],
        release: PublishedRelease,
    ) -> UpdateInputsResult:
        now = self._clock.now()
        profile = await load_profile(uow, case.owner_subject_id)
        new_profile_version: int | None = None
        missing: list[Issue] = []
        if request.mode is UpdateMode.ACTUAL_UPDATE:
            assert request.expected_profile_version is not None  # enforced by the contract
            stored = await self._profiles.apply_actual(
                uow,
                profile,
                changes,
                expected_version=request.expected_profile_version,
                source_message_id=request.source_message_id,
            )
            new_profile_version = stored.version
            updated = case
            if stored is not profile or case.salary_profile_version_used != stored.version:
                updated = await uow.cases.update(replace(case, actual_facts=stored, updated_at=now))
            profile = stored
            missing = [
                Issue(code=IssueCode.MISSING_INPUT, field=c.path.value, reason="cleared")
                for c in changes
                if c.value is None
            ]
        else:
            overrides = apply_hypothetical_changes(
                case.hypothetical_overrides,
                changes,
                now=now,
                source_message_id=request.source_message_id,
            )
            updated = case
            if overrides != case.hypothetical_overrides:
                updated = await uow.cases.update(
                    replace(case, hypothetical_overrides=overrides, updated_at=now)
                )
        conflicts = await self._occupation_conflicts(changes, updated, release)
        runs = await uow.comparison_runs.list_recent(case.case_id, RECENT_RUNS)
        stale = [
            run.run_id
            for run in runs
            if run_freshness(
                run,
                case_version=updated.version,
                latest_profile_version=profile.version,
                latest_release_id=release.release_id,
            )
            is Freshness.STALE
        ]
        return UpdateInputsResult(
            accepted_fields=[change.field for change in request.changes],
            conflicts=conflicts,
            new_case_version=updated.version,
            new_profile_version=new_profile_version,
            stale_run_ids=stale,
            missing_information=missing,
        )

    # --- reference checks --------------------------------------------------------------

    async def _pinned_reference(self) -> tuple[PublishedRelease, RulesConfig]:
        release = await self._reference_data.latest_release()
        rules = (
            None
            if release is None
            else await self._reference_data.get_rules(release.rules_snapshot_id)
        )
        if release is None or rules is None:
            raise InfrastructureUnavailable("reference data has not been published")
        return release, rules

    async def _check_identifiers(
        self, changes: tuple[FactChange, ...], release: PublishedRelease
    ) -> None:
        """Identifiers must exist in the pinned release; any unknown one rejects the list."""
        for change in changes:
            if change.path is FactPath.OCCUPATION_ID and change.value is not None:
                occupation = await self._reference_data.get_occupation(
                    release.catalogue_version_id, str(change.value)
                )
                if occupation is None:
                    raise InvalidInput("unknown occupation", safe_details={"field": change.path})
            if change.path is FactPath.CURRENT_VEHICLE and isinstance(change.value, VehicleRef):
                ref = change.value
                if ref.catalogue_id is not None:
                    exists = (
                        await self._reference_data.get_vehicle(
                            release.catalogue_version_id, ref.catalogue_id
                        )
                        is not None
                    )
                else:
                    assert ref.tax_key is not None
                    exists = (
                        await self._reference_data.get_value_of_use(
                            release.tax_dataset_version_id, ref.tax_key
                        )
                        is not None
                    )
                if not exists:
                    raise InvalidInput("unknown vehicle", safe_details={"field": change.path})

    async def _occupation_conflicts(
        self,
        changes: tuple[FactChange, ...],
        case: ComparisonCase,
        release: PublishedRelease,
    ) -> list[Issue]:
        """Occupation vs group mismatch is accepted but reported; the group is never
        overwritten automatically (architecture §7)."""
        touched = {c.path for c in changes}
        if not touched & {FactPath.OCCUPATION_ID, FactPath.ENTITLEMENT_GROUP}:
            return []
        overrides = case.hypothetical_overrides
        occupation_id = effective_value(case.actual_facts, overrides, FactPath.OCCUPATION_ID)
        group = effective_value(case.actual_facts, overrides, FactPath.ENTITLEMENT_GROUP)
        if occupation_id is None or group is None:
            return []
        occupation = await self._reference_data.get_occupation(
            release.catalogue_version_id, str(occupation_id)
        )
        if occupation is None or occupation.group == group:
            return []
        return [
            Issue(
                code=IssueCode.SOURCE_CONFLICT,
                field=FactPath.ENTITLEMENT_GROUP.value,
                reason="occupation_group_conflict",
            )
        ]


def _created(case: ComparisonCase, *, created: bool) -> CreateCaseResult:
    return CreateCaseResult(
        case_id=case.case_id,
        case_version=case.version,
        salary_profile_version_used=case.salary_profile_version_used,
        created=created,
    )
