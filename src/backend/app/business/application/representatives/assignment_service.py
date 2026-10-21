"""Representative request claiming, releasing and the dashboard summary.

Lock order (one global order, see `ports/repositories.py`):
  Claim:   operation claim -> request FOR UPDATE -> assignment INSERT.
  Release: operation claim -> request FOR UPDATE -> assignment FOR UPDATE.
  Cancel:  (caller holds) case FOR UPDATE -> request FOR UPDATE -> assignment FOR UPDATE.
Claim and Release serialise on the request row, so an INSERT never races another INSERT for
the same request. The partial unique index `uq_request_assignments_one_active` is the database
safety net for an active assignment.

Privacy: this service exposes no login, subject ID, role or salary data. Handler names come
from the minimal `display_name` in the user directory, through the answer service's view.
"""

import asyncio
from datetime import datetime
from uuid import UUID, uuid4

from app.business.application.errors import (
    ForbiddenError,
    InfrastructureUnavailable,
    InvalidStateTransition,
    NotFoundError,
    VersionConflict,
    translate_errors,
)
from app.business.application.idempotency import canonical_request_hash
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.ports.clock import Clock
from app.business.application.ports.repositories import (
    AssignmentConflictError,
    AuditEntry,
    OperationKey,
    Replay,
    ScopeKind,
)
from app.business.application.ports.unit_of_work import (
    BusinessUnitOfWork,
    BusinessUnitOfWorkFactory,
    TransientConflictError,
)
from app.business.domain.json_values import freeze_object
from app.business.domain.representatives import (
    AssignmentStatus,
    RequestAssignment,
    RequestStatus,
)
from app.contracts.representatives import (
    ClaimRequest,
    ClaimResult,
    ReleaseRequest,
    ReleaseResult,
    RepresentativeSummary,
)
from app.contracts.tool_context import ActorRef, Role

CLAIM_ACTION = "representative_assignment.claimed"
RELEASE_ACTION = "representative_assignment.released"
CANCEL_ACTION = "representative_assignment.cancelled"
CLAIM_OP = "claim_representative_request"
RELEASE_OP = "release_representative_request"
MAX_ATTEMPTS = 3
RETRY_DELAY = 0.05


class AssignmentService:
    def __init__(
        self,
        unit_of_work: BusinessUnitOfWorkFactory,
        policy: AuthorizationPolicy,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._clock = clock

    # ---------------------------------------------------------------------------------
    # Claim
    # ---------------------------------------------------------------------------------

    async def claim_request(
        self, actor: ActorRef, request_id: UUID, body: ClaimRequest
    ) -> ClaimResult:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await self._claim_once(actor, request_id, body)
            except TransientConflictError:
                if attempt == MAX_ATTEMPTS:
                    raise InfrastructureUnavailable(
                        "claim could not be saved; try again",
                        safe_details={"reason": "transient_conflict"},
                    ) from None
                await asyncio.sleep(RETRY_DELAY * attempt)
        raise AssertionError("unreachable")

    async def _claim_once(
        self, actor: ActorRef, request_id: UUID, body: ClaimRequest
    ) -> ClaimResult:
        request_hash = canonical_request_hash(
            {"requestId": str(request_id), "operationId": str(body.operation_id)}
        )
        with translate_errors():
            async with self._unit_of_work() as uow:
                representative = await self._policy.representative(uow, actor)
                key = OperationKey(
                    representative.subject_id,
                    ScopeKind.REPRESENTATIVE_REQUEST,
                    request_id,
                    CLAIM_OP,
                    body.operation_id,
                )
                op_result = await uow.operations.claim(key, request_hash)
                if isinstance(op_result, Replay) and op_result.result is not None:
                    return ClaimResult.model_validate(dict(op_result.result))

                request = await uow.representative_requests.lock(request_id)
                if request is None:
                    raise NotFoundError()
                if request.status is not RequestStatus.OPEN:
                    raise InvalidStateTransition(f"cannot claim a {request.status.value} request")
                if body.expected_request_version != request.request_version:
                    raise VersionConflict("the request changed; reload first")

                now = self._clock.now()
                assignment = RequestAssignment(
                    assignment_id=uuid4(),
                    request_id=request_id,
                    claimed_by=representative.subject_id,
                    claimed_at=now,
                    assignment_version=0,
                    status=AssignmentStatus.ACTIVE,
                )
                try:
                    await uow.assignments.add(assignment)
                except AssignmentConflictError:
                    raise InvalidStateTransition(
                        "this request has already been claimed by another representative"
                    ) from None

                await uow.audit.add(
                    _assignment_audit(
                        Role.REPRESENTATIVE.value,
                        representative.subject_id,
                        assignment,
                        CLAIM_ACTION,
                        now,
                        request.case_id,
                    )
                )
                result = ClaimResult(
                    assignment_id=assignment.assignment_id,
                    assignment_version=assignment.assignment_version,
                    claimed_at=now,
                )
                await uow.operations.complete(
                    key, freeze_object(result.model_dump(mode="json", by_alias=True))
                )
                await uow.commit()
        return result

    # ---------------------------------------------------------------------------------
    # Release
    # ---------------------------------------------------------------------------------

    async def release_request(
        self, actor: ActorRef, request_id: UUID, body: ReleaseRequest
    ) -> ReleaseResult:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await self._release_once(actor, request_id, body)
            except TransientConflictError:
                if attempt == MAX_ATTEMPTS:
                    raise InfrastructureUnavailable(
                        "release could not be saved; try again",
                        safe_details={"reason": "transient_conflict"},
                    ) from None
                await asyncio.sleep(RETRY_DELAY * attempt)
        raise AssertionError("unreachable")

    async def _release_once(
        self, actor: ActorRef, request_id: UUID, body: ReleaseRequest
    ) -> ReleaseResult:
        request_hash = canonical_request_hash(
            {"requestId": str(request_id), "operationId": str(body.operation_id)}
        )
        with translate_errors():
            async with self._unit_of_work() as uow:
                representative = await self._policy.representative(uow, actor)
                key = OperationKey(
                    representative.subject_id,
                    ScopeKind.REPRESENTATIVE_REQUEST,
                    request_id,
                    RELEASE_OP,
                    body.operation_id,
                )
                op_result = await uow.operations.claim(key, request_hash)
                if isinstance(op_result, Replay) and op_result.result is not None:
                    return ReleaseResult.model_validate(dict(op_result.result))

                request = await uow.representative_requests.lock(request_id)
                if request is None:
                    raise NotFoundError()
                assignment = await uow.assignments.lock_active(request_id)
                if assignment is None:
                    raise InvalidStateTransition("this request is not currently claimed")
                if assignment.claimed_by != representative.subject_id:
                    raise ForbiddenError("only the current handler can release this request")
                if assignment.assignment_version != body.expected_assignment_version:
                    raise VersionConflict("the assignment changed; reload first")

                now = self._clock.now()
                updated = await uow.assignments.update_status(
                    assignment,
                    new_status=AssignmentStatus.RELEASED,
                    released_at=now,
                )
                await uow.audit.add(
                    _assignment_audit(
                        Role.REPRESENTATIVE.value,
                        representative.subject_id,
                        updated,
                        RELEASE_ACTION,
                        now,
                        request.case_id,
                    )
                )
                result = ReleaseResult(assignment_id=updated.assignment_id, released_at=now)
                await uow.operations.complete(
                    key, freeze_object(result.model_dump(mode="json", by_alias=True))
                )
                await uow.commit()
        return result

    # ---------------------------------------------------------------------------------
    # Summary counters
    # ---------------------------------------------------------------------------------

    async def get_summary(self, actor: ActorRef) -> RepresentativeSummary:
        with translate_errors():
            async with self._unit_of_work() as uow:
                representative = await self._policy.representative(uow, actor)
                counts = await uow.assignments.counts(representative.subject_id)
                return RepresentativeSummary(
                    total=counts.total,
                    unclaimed=counts.unclaimed,
                    assigned_to_me=counts.assigned_to_me,
                    assigned_to_others=counts.assigned_to_others,
                    answered=counts.answered,
                    cannot_determine=counts.cannot_determine,
                    cancelled=counts.cancelled,
                )


async def cancel_active_assignment(
    uow: BusinessUnitOfWork,
    actor: ActorRef,
    request_id: UUID,
    case_id: UUID,
    *,
    now: datetime,
) -> None:
    """Terminate the active assignment of a request that is being cancelled.

    The caller must already hold the case lock and the request lock, so this only takes the
    assignment lock (global order: case -> request -> assignment). It is a no-op when the
    request has no active assignment.
    """
    assignment = await uow.assignments.lock_active(request_id)
    if assignment is None:
        return
    cancelled = await uow.assignments.update_status(
        assignment,
        new_status=AssignmentStatus.CANCELLED,
        cancelled_at=now,
    )
    await uow.audit.add(
        _assignment_audit(
            _actor_role(actor), actor.subject_id, cancelled, CANCEL_ACTION, now, case_id
        )
    )


def _actor_role(actor: ActorRef) -> str:
    """The acting role for audit: a deterministic choice when an actor holds several roles."""
    return min((role.value for role in actor.roles), default=Role.REPRESENTATIVE.value)


def _assignment_audit(
    actor_role: str,
    actor_subject_id: UUID,
    assignment: RequestAssignment,
    action: str,
    now: datetime,
    case_id: UUID,
) -> AuditEntry:
    return AuditEntry(
        entry_id=uuid4(),
        actor_subject_id=actor_subject_id,
        actor_role=actor_role,
        action=action,
        case_id=case_id,
        occurred_at=now,
        request_id=assignment.request_id,
        details=freeze_object(
            {
                "assignmentId": str(assignment.assignment_id),
                "assignmentVersion": assignment.assignment_version,
                "status": assignment.status.value,
            }
        ),
    )
