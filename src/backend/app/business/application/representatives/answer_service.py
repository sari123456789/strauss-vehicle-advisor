"""Representative side of representative requests (B3a): list, read and answer.

Authorization: every call revalidates the actor and requires the `representative` role
(`FORBIDDEN` otherwise). DEMO-ONLY POLICY (decision Q4): every active representative sees and
may answer every request. A request view carries only the minimal question context - the
vehicle, and the entitlement group for a participation question - never the employee's
identity, profile, salary, case ID or conversation ID.

`submit_answer` runs as one short Business transaction, all or nothing:
  revalidate the representative -> read the request without a lock (to find its case) ->
  operation claim (same ID + same payload replays the stored answer; another payload is
  `OPERATION_PAYLOAD_MISMATCH`) -> lock the case -> lock the request and re-check its status
  (only `open` may be answered, else `INVALID_STATE_TRANSITION`) and `expectedRequestVersion`
  (`VERSION_CONFLICT`) -> validate the value -> apply to the case if applicable -> request
  status + 1 version, case + 1 version -> answer, audit entry and outbox event -> operation
  completion -> commit.
This is the global lock order (claim, profile, case, requests, alternatives); no earlier lock
is taken after a later one. Nothing is called outside the database while locks are held.

Applying an `answered` answer (decision Q1; case-local, the shared profile never changes):
- `alternative.participation` (money, ILS): stored in the request's alternative `inputs`
  (alternative version + 1) - only if that alternative is still active and unchanged since
  the question, else `not_applicable` / `alternative_changed`.
- `employee.entitlementGroup`, `currentArrangement.propulsion`: a case representative fact.
- Either applies only when its effective period covers the case's reference date, else it is
  saved as `not_applicable` / `outside_effective_period` and not used (AC30).
- `occupation.waiverPolicy` and `currentArrangement.vehicle` (decisions Q2, Q3): a value is
  rejected (`INVALID_BUSINESS_INPUT`, reason `field_code_not_answerable_yet`) until their
  business rule and canonical format are approved; nothing is stored, the request stays open.
`cannot_determine` is accepted for every field code; it applies nothing to the case and keeps
the request's own option uncalculated (AC33).

Every committed answer - applied, not applicable or cannot determine - changes the case
version (pending blockers changed), so earlier runs become stale; their snapshots are kept.
A PostgreSQL deadlock or serialization abort rolls back everything and the whole idempotent
operation is retried a bounded number of times; version conflicts are never retried.
"""

import asyncio
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.business.application.errors import (
    ForbiddenError,
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
from app.business.application.ports.reference_data import ReferenceDataRepository
from app.business.application.ports.repositories import (
    AuditEntry,
    OperationKey,
    OutboxEvent,
    QueueQuery,
    QueueScope,
    Replay,
    ScopeKind,
)
from app.business.application.ports.unit_of_work import (
    BusinessUnitOfWork,
    BusinessUnitOfWorkFactory,
    TransientConflictError,
)
from app.business.application.profiles.profile_service import load_profile
from app.business.application.representatives.case_answers import (
    PARTICIPATION_INPUT,
    participation_inputs,
    read_participation,
    with_representative_facts,
)
from app.business.application.representatives.request_service import answer_view
from app.business.domain.cases import CaseLifecycleStatus, ComparisonCase
from app.business.domain.json_values import freeze_object
from app.business.domain.representatives import (
    CASE_FACT_PATHS,
    AnswerApplication,
    AnswerOutcome,
    AssignmentStatus,
    ExpectedValueType,
    NotApplicableReason,
    RepresentativeAnswer,
    RepresentativeFact,
    RepresentativeFieldCode,
    RepresentativeParticipation,
    RepresentativeRequest,
    RequestAssignment,
    RequestStatus,
    applicable_facts,
    covers,
)
from app.business.domain.rules import EntitlementGroup
from app.business.domain.vehicles import CatalogueVehicle, Propulsion
from app.contracts.common import EntitlementGroup as ContractEntitlementGroup
from app.contracts.representatives import AnswerOutcome as ContractOutcome
from app.contracts.representatives import (
    AnswerSubmit,
    AnswerView,
    AssignmentFilter,
    HandlingStatus,
    RepresentativeRequestView,
    VehicleContext,
)
from app.contracts.representatives import ExpectedValueType as ContractValueType
from app.contracts.representatives import RepresentativeFieldCode as ContractFieldCode
from app.contracts.representatives import RequestStatus as ContractRequestStatus
from app.contracts.tool_context import ActorRef, Role

SUBMIT_ANSWER = "submit_representative_answer"
ANSWER_ACTION = "representative_answer.submitted"
EVENT_TYPE = "representative.answered"
NOT_ANSWERABLE_YET = "field_code_not_answerable_yet"
DEFERRED_FIELDS = frozenset(
    {RepresentativeFieldCode.OCCUPATION_WAIVER_POLICY, RepresentativeFieldCode.VEHICLE_TAX_KEY}
)
"""No approved business rule / canonical value format yet (decisions Q2, Q3)."""
MONEY = re.compile(r"^\d{1,9}(\.\d{1,2})?$")
"""Whole shekels or agorot, e.g. "350" or "350.00"; never a float."""
MAX_LIST = 200
MAX_SEARCH = 100
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class _Applied:
    """What an answer does to its case (computed under the locks)."""

    application: AnswerApplication | None
    reason: NotApplicableReason | None = None
    previous: str | None = None


class RepresentativeAnswerService:
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
        self._clock = clock

    # --- read ----------------------------------------------------------------------------

    async def list_requests(
        self,
        actor: ActorRef,
        *,
        statuses: Collection[RequestStatus | ContractRequestStatus] | None = (RequestStatus.OPEN,),
        assignment_filter: AssignmentFilter | None = None,
        field_code: ContractFieldCode | None = None,
        search: str | None = None,
        after_id: UUID | None = None,
        limit: int = MAX_LIST,
    ) -> list[RepresentativeRequestView]:
        """The representative queue, oldest first, filtered and paginated in the database.

        `statuses`: business status filter; None = all.
        `assignment_filter`: all (default), unclaimed, mine or others.
        `field_code`, `search` (case-insensitive substring of the question or request ID).
        `after_id`: exclusive cursor, the last request ID of the previous page.
        """
        if isinstance(limit, bool) or not 1 <= limit <= MAX_LIST:
            raise InvalidInput(f"limit must be between 1 and {MAX_LIST}")
        if search is not None and len(search) > MAX_SEARCH:
            raise InvalidInput(f"search must be at most {MAX_SEARCH} characters")
        wanted = None if statuses is None else frozenset(RequestStatus(s.value) for s in statuses)
        scope = QueueScope.ALL if assignment_filter is None else QueueScope(assignment_filter.value)
        with translate_errors():
            async with self._unit_of_work() as uow:
                representative = await self._policy.representative(uow, actor)
                after_created_at = None
                if after_id is not None:
                    cursor = await uow.representative_requests.get_by_id(after_id)
                    if cursor is None:
                        raise InvalidInput(
                            "unknown pagination cursor", safe_details={"field": "after"}
                        )
                    after_created_at = cursor.created_at
                entries = await uow.representative_requests.list_queue(
                    QueueQuery(
                        viewer_id=representative.subject_id,
                        limit=limit,
                        statuses=wanted,
                        scope=scope,
                        field_code=None if field_code is None else field_code.value,
                        search=search or None,
                        after_created_at=after_created_at,
                        after_id=after_id,
                    )
                )
                catalogue = await self._catalogue()
                return [
                    await self._view(
                        uow, entry.request, catalogue, actor=actor, assignment=entry.assignment
                    )
                    for entry in entries
                ]

    async def get_request(self, actor: ActorRef, request_id: UUID) -> RepresentativeRequestView:
        with translate_errors():
            async with self._unit_of_work() as uow:
                await self._policy.representative(uow, actor)
                found = await uow.representative_requests.get_by_id(request_id)
                if found is None:
                    raise NotFoundError()
                assignment = await uow.assignments.get_active(request_id)
                return await self._view(
                    uow, found, await self._catalogue(), actor=actor, assignment=assignment
                )

    async def _catalogue(self) -> dict[str, CatalogueVehicle]:
        release = await self._reference_data.latest_release()
        if release is None:
            return {}
        vehicles = await self._reference_data.list_vehicles(release.catalogue_version_id)
        return {v.catalogue_id: v for v in vehicles}

    async def _view(
        self,
        uow: BusinessUnitOfWork,
        request: RepresentativeRequest,
        catalogue: dict[str, CatalogueVehicle],
        *,
        actor: ActorRef,
        assignment: RequestAssignment | None,
    ) -> RepresentativeRequestView:
        group = None
        if request.field_code is RepresentativeFieldCode.ALTERNATIVE_PARTICIPATION:
            group = await self._group(uow, request.case_id)

        # Determine handling status and permissions.
        is_terminal = request.status is not RequestStatus.OPEN
        if is_terminal:
            handling_status = HandlingStatus.COMPLETED
            can_claim = False
            can_answer = False
            can_release = False
            handler_display_name = None
            claimed_at = None
            assignment_version = None
        elif assignment is None:
            handling_status = HandlingStatus.UNCLAIMED
            can_claim = True
            can_answer = False
            can_release = False
            handler_display_name = None
            claimed_at = None
            assignment_version = None
        elif assignment.claimed_by == actor.subject_id:
            handling_status = HandlingStatus.ASSIGNED_TO_ME
            can_claim = False
            can_answer = True
            can_release = True
            handler_account = await uow.users.get(assignment.claimed_by)
            handler_display_name = handler_account.display_name if handler_account else None
            claimed_at = assignment.claimed_at
            assignment_version = assignment.assignment_version
        else:
            handling_status = HandlingStatus.ASSIGNED_TO_OTHER
            can_claim = False
            can_answer = False
            can_release = False
            handler_account = await uow.users.get(assignment.claimed_by)
            handler_display_name = handler_account.display_name if handler_account else None
            claimed_at = assignment.claimed_at
            assignment_version = assignment.assignment_version

        return RepresentativeRequestView(
            request_id=request.request_id,
            request_version=request.request_version,
            status=ContractRequestStatus(request.status.value),
            field_code=ContractFieldCode(request.field_code.value),
            expected_value_type=ContractValueType(request.expected_value_type.value),
            question_text=request.question_text,
            vehicle=_vehicle_context(request, catalogue),
            entitlement_group=None if group is None else ContractEntitlementGroup(group.value),
            created_at=request.created_at,
            updated_at=request.updated_at,
            handling_status=handling_status,
            handler_display_name=handler_display_name,
            claimed_at=claimed_at,
            assignment_version=assignment_version,
            can_claim=can_claim,
            can_answer=can_answer,
            can_release=can_release,
        )

    async def _group(self, uow: BusinessUnitOfWork, case_id: UUID) -> EntitlementGroup | None:
        """The group the case's regular calculation uses (latest report + representative
        fact); the only profile value a participation question discloses."""
        case = await uow.cases.get_by_id(case_id)
        if case is None:
            return None
        facts = applicable_facts(await uow.case_facts.list_for_case(case_id), case.reference_date)
        profile = with_representative_facts(await load_profile(uow, case.owner_subject_id), facts)
        field = profile.entitlement_group
        return field.value if field.is_usable else None

    # --- answer ----------------------------------------------------------------------------

    async def submit_answer(
        self, actor: ActorRef, request_id: UUID, answer: AnswerSubmit
    ) -> AnswerView:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await self._submit_once(actor, request_id, answer)
            except TransientConflictError:
                if attempt == MAX_ATTEMPTS:
                    raise InfrastructureUnavailable(
                        "the answer could not be saved right now; try again",
                        safe_details={"reason": "transient_conflict"},
                    ) from None
                await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
        raise AssertionError("unreachable")

    async def _submit_once(
        self, actor: ActorRef, request_id: UUID, submit: AnswerSubmit
    ) -> AnswerView:
        payload = submit.model_dump(mode="json", by_alias=True)
        request_hash = canonical_request_hash({"requestId": str(request_id), "answer": payload})
        with translate_errors():
            limit = await self._max_monthly_amount()  # immutable reference data, before locks
            async with self._unit_of_work() as uow:
                representative = await self._policy.representative(uow, actor)
                found = await uow.representative_requests.get_by_id(request_id)
                if found is None:
                    raise NotFoundError()
                key = OperationKey(
                    representative.subject_id,
                    ScopeKind.REPRESENTATIVE_REQUEST,
                    request_id,
                    SUBMIT_ANSWER,
                    submit.operation_id,
                )
                claim = await uow.operations.claim(key, request_hash)
                if isinstance(claim, Replay) and claim.result is not None:
                    return AnswerView.model_validate(dict(claim.result))
                # Lock order (global, see ports/repositories.py): case -> request -> assignment.
                case = await uow.cases.lock_by_id(found.case_id)
                request = await uow.representative_requests.lock(request_id)
                if case is None or request is None or request.case_id != case.case_id:
                    raise NotFoundError()
                if request.status is not RequestStatus.OPEN:
                    raise InvalidStateTransition("this request is no longer open")
                # Only the current handler may answer; the handler is checked under the locks.
                active_assignment = await uow.assignments.lock_active(request_id)
                if active_assignment is None:
                    raise InvalidStateTransition(
                        "claim this request before answering it",
                        safe_details={"reason": "must_claim_before_answering"},
                    )
                if active_assignment.claimed_by != representative.subject_id:
                    raise ForbiddenError(
                        "this request is claimed by another representative",
                        safe_details={"reason": "request_claimed_by_another"},
                    )
                if submit.expected_request_version != request.request_version:
                    raise VersionConflict("the request changed in the meantime; reload first")
                if case.lifecycle_status is CaseLifecycleStatus.CLOSED:
                    raise InvalidStateTransition("the request's case is closed")
                value = _parse_value(request, submit, limit)
                now = self._clock.now()
                applied = await self._apply(uow, case, request, submit, value, now)
                answer = RepresentativeAnswer(
                    answer_id=uuid4(),
                    request_id=request.request_id,
                    case_id=case.case_id,
                    outcome=AnswerOutcome(submit.outcome.value),
                    answered_by=representative.subject_id,
                    answered_at=now,
                    operation_id=submit.operation_id,
                    value=None if value is None else _canonical(value),
                    unit="ILS"
                    if request.expected_value_type is ExpectedValueType.MONEY and value is not None
                    else None,
                    effective_from=submit.effective_from,
                    effective_to=submit.effective_to,
                    note=submit.note,
                    application=applied.application,
                    application_reason=applied.reason,
                )
                status = (
                    RequestStatus.ANSWERED
                    if answer.outcome is AnswerOutcome.ANSWERED
                    else RequestStatus.CANNOT_DETERMINE
                )
                stored_request = await uow.representative_requests.update(
                    request.with_status(status, at=now)
                )
                stored_case = await uow.cases.update(replace(case, updated_at=now))
                await uow.representative_answers.add(answer)
                await uow.audit.add(_audit(representative, answer, request, applied))
                await uow.outbox.add(_event(answer, stored_request, stored_case))
                # Complete the assignment in the same transaction as the answer.
                await uow.assignments.update_status(
                    active_assignment,
                    new_status=AssignmentStatus.COMPLETED,
                    completed_at=now,
                )
                view = answer_view(
                    answer,
                    text_answer=request.field_code is RepresentativeFieldCode.GENERAL_QUESTION,
                )
                await uow.operations.complete(
                    key, freeze_object(view.model_dump(mode="json", by_alias=True))
                )
                await uow.commit()
        return view

    async def _max_monthly_amount(self) -> Decimal:
        release = await self._reference_data.latest_release()
        rules = (
            None
            if release is None
            else await self._reference_data.get_rules(release.rules_snapshot_id)
        )
        if rules is None:
            raise InfrastructureUnavailable("reference data has not been published")
        return rules.limits.max_monthly_amount

    async def _apply(
        self,
        uow: BusinessUnitOfWork,
        case: ComparisonCase,
        request: RepresentativeRequest,
        submit: AnswerSubmit,
        value: Decimal | EntitlementGroup | Propulsion | str | None,
        now: datetime,
    ) -> _Applied:
        if value is None:  # cannot_determine: nothing is applied to the case
            return _Applied(None)
        if request.field_code is RepresentativeFieldCode.GENERAL_QUESTION:
            # A text answer delivered to the employee as is; it never becomes a case fact.
            return _Applied(AnswerApplication.APPLIED)
        if request.field_code is RepresentativeFieldCode.ALTERNATIVE_PARTICIPATION:
            assert isinstance(value, Decimal)
            return await self._apply_participation(uow, case, request, submit, value, now)
        path = CASE_FACT_PATHS[request.field_code]
        stored = await uow.case_facts.list_for_case(case.case_id)
        previous = applicable_facts(stored, case.reference_date).get(path)
        if not covers(submit.effective_from, submit.effective_to, case.reference_date):
            return _Applied(
                AnswerApplication.NOT_APPLICABLE, NotApplicableReason.OUTSIDE_EFFECTIVE_PERIOD
            )
        assert isinstance(value, EntitlementGroup | Propulsion)
        await uow.case_facts.add(
            RepresentativeFact(
                fact_id=uuid4(),
                case_id=case.case_id,
                request_id=request.request_id,
                path=path,
                value=value,
                answered_at=now,
                effective_from=submit.effective_from,
                effective_to=submit.effective_to,
            )
        )
        return _Applied(
            AnswerApplication.APPLIED, previous=None if previous is None else previous.value.value
        )

    async def _apply_participation(
        self,
        uow: BusinessUnitOfWork,
        case: ComparisonCase,
        request: RepresentativeRequest,
        submit: AnswerSubmit,
        amount: Decimal,
        now: datetime,
    ) -> _Applied:
        alternative = (
            None
            if request.alternative_id is None
            else await uow.alternatives.get(case.case_id, request.alternative_id)
        )
        if (
            alternative is None
            or not alternative.active
            or alternative.version != request.alternative_version
        ):
            return _Applied(
                AnswerApplication.NOT_APPLICABLE, NotApplicableReason.ALTERNATIVE_CHANGED
            )
        if not covers(submit.effective_from, submit.effective_to, case.reference_date):
            return _Applied(
                AnswerApplication.NOT_APPLICABLE, NotApplicableReason.OUTSIDE_EFFECTIVE_PERIOD
            )
        previous = read_participation(alternative.inputs)
        entry = participation_inputs(
            RepresentativeParticipation(
                amount=amount,
                request_id=request.request_id,
                answered_at=now,
                effective_from=submit.effective_from,
                effective_to=submit.effective_to,
            )
        )
        await uow.alternatives.update(
            replace(
                alternative,
                inputs=freeze_object({**alternative.inputs, PARTICIPATION_INPUT: entry}),
                updated_at=now,
            )
        )
        return _Applied(
            AnswerApplication.APPLIED,
            previous=None if previous is None else format(previous.amount, "f"),
        )


# --- helpers -----------------------------------------------------------------------------


def _parse_value(
    request: RepresentativeRequest, submit: AnswerSubmit, max_amount: Decimal
) -> Decimal | EntitlementGroup | Propulsion | str | None:
    """The validated Domain value of an `answered` submission; None for cannot_determine."""
    if submit.outcome is ContractOutcome.CANNOT_DETERMINE:
        if submit.unit is not None:
            raise InvalidInput("cannot_determine carries no unit", safe_details={"field": "unit"})
        return None
    field = request.field_code
    if field in DEFERRED_FIELDS:
        raise InvalidInput(
            "answers to this question cannot be recorded yet",
            safe_details={
                "field": "value",
                "fieldCode": field.value,
                "reason": NOT_ANSWERABLE_YET,
            },
        )
    raw = submit.value
    assert raw is not None  # contract: answered requires value
    kind = request.expected_value_type
    if kind is ExpectedValueType.TEXT:
        # `value` is the short answer (<=200); the full text, when longer, is in `note`.
        if submit.unit is not None or submit.effective_from or submit.effective_to:
            raise InvalidInput(
                "a text answer carries no unit or period", safe_details={"field": "value"}
            )
        return raw.strip() or raw
    if kind is ExpectedValueType.MONEY:
        if not MONEY.fullmatch(raw):
            raise InvalidInput(
                "the amount must be a decimal number in ILS", safe_details={"field": "value"}
            )
        amount = Decimal(raw)
        if amount > max_amount:
            raise InvalidInput(
                "the amount exceeds the supported monthly limit",
                safe_details={"field": "value", "reason": "above_supported_limit"},
            )
        return amount
    if submit.unit is not None:
        raise InvalidInput("only money answers carry a unit", safe_details={"field": "unit"})
    try:
        if kind is ExpectedValueType.ENTITLEMENT_GROUP:
            return EntitlementGroup(raw)
        if kind is ExpectedValueType.PROPULSION:
            return Propulsion(raw)
    except ValueError:
        raise InvalidInput(
            "the value is not one of the allowed values", safe_details={"field": "value"}
        ) from None
    raise InvalidInput("unsupported value type", safe_details={"field": "value"})


def _canonical(value: Decimal | EntitlementGroup | Propulsion | str) -> str:
    if isinstance(value, str) and not isinstance(value, EntitlementGroup | Propulsion):
        return value
    return format(value, "f") if isinstance(value, Decimal) else value.value


def _vehicle_context(
    request: RepresentativeRequest, catalogue: dict[str, CatalogueVehicle]
) -> VehicleContext | None:
    """Participation: the asked catalogue vehicle. Current-arrangement questions: the
    employee's reported current vehicle (catalogue ID, or only the registration year of an
    official tax key). Other questions carry no vehicle."""
    subject = request.subject_snapshot
    current = subject.get("currentVehicle")
    holder = current if isinstance(current, Mapping) else subject
    catalogue_id = holder.get("catalogueId")
    if isinstance(catalogue_id, str):
        vehicle = catalogue.get(catalogue_id)
        if vehicle is None:
            return VehicleContext(catalogue_id=catalogue_id)
        return VehicleContext(
            catalogue_id=vehicle.catalogue_id,
            commercial_name=vehicle.commercial_name,
            registration_year=vehicle.tax_key.registration_year,
        )
    key = holder.get("taxKey")
    if isinstance(key, Mapping) and isinstance(key.get("registrationYear"), str):
        return VehicleContext(registration_year=str(key["registrationYear"]))
    return None


def _audit(
    representative: ActorRef,
    answer: RepresentativeAnswer,
    request: RepresentativeRequest,
    applied: _Applied,
) -> AuditEntry:
    details: dict[str, object] = {
        "outcome": answer.outcome.value,
        "application": None if answer.application is None else answer.application.value,
        "applicationReason": None if applied.reason is None else applied.reason.value,
        "newValue": answer.value,
        "previousRepresentativeValue": applied.previous,
        "effectiveFrom": None if answer.effective_from is None else str(answer.effective_from),
        "effectiveTo": None if answer.effective_to is None else str(answer.effective_to),
        "alternativeId": None if request.alternative_id is None else str(request.alternative_id),
    }
    return AuditEntry(
        entry_id=uuid4(),
        actor_subject_id=representative.subject_id,
        actor_role=Role.REPRESENTATIVE.value,
        action=ANSWER_ACTION,
        case_id=answer.case_id,
        occurred_at=answer.answered_at,
        request_id=answer.request_id,
        field_code=request.field_code.value,
        details=freeze_object(details),
    )


def _event(
    answer: RepresentativeAnswer, request: RepresentativeRequest, case: ComparisonCase
) -> OutboxEvent:
    """`RepresentativeAnsweredEvent` v1: identifiers and versions only, never values."""
    event_id = uuid4()
    return OutboxEvent(
        event_id=event_id,
        event_type=EVENT_TYPE,
        schema_version=1,
        occurred_at=answer.answered_at,
        payload=freeze_object(
            {
                "eventId": str(event_id),
                "schemaVersion": 1,
                "occurredAt": answer.answered_at.isoformat(),
                "requestId": str(request.request_id),
                "caseId": str(case.case_id),
                "alternativeId": None
                if request.alternative_id is None
                else str(request.alternative_id),
                "requestVersion": request.request_version,
                "caseVersionAfter": case.version,
            }
        ),
    )
