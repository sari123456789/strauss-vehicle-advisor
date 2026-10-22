"""SQL store for AI conversation persistence (schema `agent`).

`AgentPersistence.transaction()` opens one short transaction and yields an `AgentStore`
bound to it; every method runs inside the caller's transaction. Callers must not await an LLM
or business tool while a transaction is open (the runtime engine's
`idle_in_transaction_session_timeout` aborts such transactions).

Idempotency: every write is keyed by a caller-supplied stable ID. Replaying the same write
returns the stored record (`created=False`); reusing an ID with different content raises a
`*ConflictError`/`*MismatchError` and changes nothing. Appends lock the conversation row
(`SELECT ... FOR UPDATE`), so concurrent duplicates serialize and sequences have no gaps.
"""

import hashlib
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.application.ports.conversation_store import (
    COMPLETED,
    DEAD,
    MAX_ATTEMPTS,
    NEEDS_RECONCILIATION,
    PROCESSING,
    REPRESENTATIVE_ANSWERED,
    RETRY,
    STAGED_PAYLOAD_KEYS,
    CaseBindingConflictError,
    ConversationConflictError,
    ConversationNotFoundError,
    ConversationRecord,
    EventMismatchError,
    EventRecord,
    ExpiredClaim,
    FailureDecision,
    InputClaim,
    InputPayloadMismatchError,
    InputRecord,
    LineageObservation,
    LineageResult,
    MessageMismatchError,
    MessageRecord,
    NewConversation,
    NewEvent,
    NewInput,
    NewMessage,
    NewSideEffectEvent,
    PersistenceConflictError,
    SideEffectConflictError,
    SideEffectEvent,
    SideEffectPhase,
    StagingConflictError,
    StaleClaimError,
    Written,
    retry_delay_seconds,
)
from app.ai.application.ports.response_staging import (
    MessageRef,
    StagedMessage,
    content_sha256,
    manifest_sha256,
)
from app.ai.infrastructure.persistence.engine import canonical_json
from app.ai.infrastructure.persistence.models import (
    ConversationRow,
    EventRow,
    InputRow,
    MessageRow,
    ResponseStagingMessageRow,
    ResponseStagingRow,
    SideEffectRow,
)


def payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _conversation(row: ConversationRow) -> ConversationRecord:
    return ConversationRecord(
        conversation_id=row.id,
        owner_subject_id=row.owner_subject_id,
        thread_id=row.thread_id,
        status=row.status,
        calculation_month=row.calculation_month,
        case_operation_id=row.case_operation_id,
        case_id=row.case_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _input(row: InputRow) -> InputRecord:
    return InputRecord(
        input_id=row.id,
        conversation_id=row.conversation_id,
        sequence=row.sequence,
        source=row.source,
        external_id=row.external_id,
        payload=row.payload,
        status=row.status,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        last_error_code=row.last_error_code,
        reconciliation_reason=row.reconciliation_reason,
        execution_ref=row.execution_ref,
    )


def _message(row: MessageRow) -> MessageRecord:
    return MessageRecord(
        message_id=row.id,
        conversation_id=row.conversation_id,
        sequence=row.sequence,
        role=row.role,
        kind=row.kind,
        text=row.text,
        payload=row.payload,
        input_id=row.input_id,
        created_at=row.created_at,
    )


def _event(row: EventRow) -> EventRecord:
    return EventRecord(
        event_id=row.event_id,
        conversation_id=row.conversation_id,
        sequence=row.sequence,
        event_type=row.event_type,
        payload=row.payload,
    )


_CLAIM_SQL = """
WITH candidate AS (
    SELECT i.id
    FROM agent.inputs i
    JOIN agent.conversations c ON c.id = i.conversation_id AND c.status = 'active'
    WHERE (i.status = 'pending' OR (i.status = 'retry' AND i.next_attempt_at <= now()))
      AND NOT EXISTS (
          SELECT 1 FROM agent.inputs j
          WHERE j.conversation_id = i.conversation_id
            AND j.sequence < i.sequence
            AND j.status NOT IN ('completed', 'dead'))
    ORDER BY COALESCE(i.next_attempt_at, i.created_at), i.sequence
    LIMIT 1
    FOR UPDATE OF i SKIP LOCKED
)
UPDATE agent.inputs AS t
SET status = 'processing', claim_token = :token, claimed_by = :worker, claimed_at = now(),
    lease_expires_at = now() + make_interval(secs => :lease), attempts = t.attempts + 1,
    next_attempt_at = NULL, base_checkpoint_id = NULL, lineage_checked_at = NULL,
    updated_at = now()
FROM candidate
WHERE t.id = candidate.id
RETURNING t.id
"""

_TAKE_OVER_SQL = """
UPDATE agent.inputs
SET claim_token = :token, claimed_by = :worker, claimed_at = now(),
    lease_expires_at = now() + make_interval(secs => :lease), attempts = attempts + 1,
    base_checkpoint_id = NULL, lineage_checked_at = NULL, updated_at = now()
WHERE id = :id AND status = 'processing' AND claim_token = :old AND lease_expires_at < now()
RETURNING id
"""

_RENEW_SQL = """
UPDATE agent.inputs
SET lease_expires_at = now() + make_interval(secs => :lease), updated_at = now()
WHERE id = :id AND status = 'processing' AND claim_token = :token AND lease_expires_at >= now()
RETURNING lease_expires_at
"""


def _side_effect(row: SideEffectRow) -> SideEffectEvent:
    return SideEffectEvent(
        event_id=row.event_id,
        input_id=row.input_id,
        operation_key=row.operation_key,
        tool=row.tool,
        phase=row.phase,
        claim_token=row.claim_token,
        attempt=row.attempt,
        request_sha256=row.request_sha256,
        outcome_code=row.outcome_code,
    )


def _check_staged_payload(payload: dict[str, Any]) -> None:
    """Identifiers, codes and field names only - never structured calculation results."""
    unknown = set(payload) - STAGED_PAYLOAD_KEYS
    if unknown:
        raise ValueError(f"payload keys not allowed in staged messages: {sorted(unknown)}")
    for key, value in payload.items():
        allowed = (
            value is None or isinstance(value, str | bool | int) and not isinstance(value, float)
        )
        if isinstance(value, list):
            allowed = all(isinstance(item, str) for item in value)
        if not allowed:
            raise ValueError(f"payload value of {key!r} must be a string, flag, count or list")


def _to_reconciliation(row: InputRow, reason: str) -> None:
    row.status = NEEDS_RECONCILIATION
    row.reconciliation_reason = reason
    row.updated_at = func.now()


def _check_lease(lease_seconds: int) -> None:
    if not 1 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 1 and 3600")


def _reason(error: Exception) -> str:
    original = getattr(error, "orig", None)
    return str(original or error).splitlines()[0]


class AgentStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- conversations -----------------------------------------------------------------

    async def create_conversation(self, new: NewConversation) -> Written[ConversationRecord]:
        statement = (
            insert(ConversationRow)
            .values(
                id=new.conversation_id,
                owner_subject_id=new.owner_subject_id,
                thread_id=new.thread_id,
                status="initializing",
                calculation_month=new.calculation_month,
                case_operation_id=new.case_operation_id,
            )
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(ConversationRow.id)
        )
        try:
            async with self._session.begin_nested():
                inserted = (await self._session.execute(statement)).scalar_one_or_none()
        except IntegrityError:
            raise ConversationConflictError(
                "thread ID or case operation ID already belongs to another conversation"
            ) from None
        row = await self._session.get(ConversationRow, new.conversation_id, populate_existing=True)
        if row is None:  # pragma: no cover - the row was just inserted or already existed
            raise ConversationNotFoundError(str(new.conversation_id))
        record = _conversation(row)
        if inserted is None and (
            record.owner_subject_id != new.owner_subject_id
            or record.thread_id != new.thread_id
            or record.calculation_month != new.calculation_month
            or record.case_operation_id != new.case_operation_id
        ):
            raise ConversationConflictError("conversation ID reused with different content")
        return Written(record, created=inserted is not None)

    async def get_conversation(self, conversation_id: UUID) -> ConversationRecord | None:
        row = await self._session.get(ConversationRow, conversation_id, populate_existing=True)
        return None if row is None else _conversation(row)

    async def get_conversation_by_case(self, case_id: UUID) -> ConversationRecord | None:
        row = await self._session.scalar(
            select(ConversationRow)
            .where(ConversationRow.case_id == case_id)
            .execution_options(populate_existing=True)
        )
        return None if row is None else _conversation(row)

    async def get_owned_conversation(
        self, conversation_id: UUID, owner_subject_id: UUID
    ) -> ConversationRecord | None:
        """The conversation only if it belongs to the owner (otherwise indistinguishable)."""
        record = await self.get_conversation(conversation_id)
        if record is None or record.owner_subject_id != owner_subject_id:
            return None
        return record

    async def list_owned_conversations(
        self, owner_subject_id: UUID, *, limit: int = 50
    ) -> list[ConversationRecord]:
        rows = await self._session.scalars(
            select(ConversationRow)
            .where(
                ConversationRow.owner_subject_id == owner_subject_id,
                ConversationRow.hidden_at.is_(None),
            )
            .order_by(ConversationRow.updated_at.desc(), ConversationRow.id)
            .limit(limit)
        )
        return [_conversation(row) for row in rows]

    async def hide_conversation(self, conversation_id: UUID) -> None:
        """Leave the conversation out of its owner's list; repeating it changes nothing."""
        row = await self._lock(conversation_id)
        if row.hidden_at is None:
            row.hidden_at = func.now()
            await self._session.flush()

    async def bind_case(self, conversation_id: UUID, case_id: UUID) -> Written[ConversationRecord]:
        """`initializing` -> `active` with its case; replaying the same binding is a no-op."""
        row = await self._lock(conversation_id)
        if row.case_id is not None:
            if row.case_id != case_id:
                raise CaseBindingConflictError("conversation is already bound to another case")
            return Written(_conversation(row), created=False)
        try:
            async with self._session.begin_nested():
                row.case_id = case_id
                row.status = "active"
                row.updated_at = func.now()
                await self._session.flush()
        except IntegrityError:
            raise CaseBindingConflictError(
                "case is already bound to another conversation"
            ) from None
        await self._session.refresh(row)
        return Written(_conversation(row), created=True)

    # --- inputs ------------------------------------------------------------------------

    async def enqueue_input(self, new: NewInput) -> Written[InputRecord]:
        row = await self._lock(new.conversation_id)
        digest = payload_sha256(new.payload)
        key = select(InputRow).where(InputRow.source == new.source)
        if new.source == REPRESENTATIVE_ANSWERED:
            key = key.where(InputRow.external_id == new.external_id)
        else:
            key = key.where(
                InputRow.conversation_id == new.conversation_id,
                InputRow.external_id == new.external_id,
            )
        existing = await self._session.scalar(key)
        if existing is None:
            existing = await self._session.get(InputRow, new.input_id)
            if existing is not None:
                raise InputPayloadMismatchError("input ID reused for a different input")
        else:
            if (
                existing.conversation_id != new.conversation_id
                or existing.payload_sha256 != digest
                or existing.id != new.input_id
            ):
                raise InputPayloadMismatchError("input reused with a different payload")
            return Written(_input(existing), created=False)
        sequence = row.next_input_seq
        created = InputRow(
            id=new.input_id,
            conversation_id=new.conversation_id,
            sequence=sequence,
            source=new.source,
            external_id=new.external_id,
            payload=new.payload,
            payload_sha256=digest,
        )
        self._session.add(created)
        row.next_input_seq = sequence + 1
        row.updated_at = func.now()
        await self._session.flush()
        await self._session.refresh(created)
        return Written(_input(created), created=True)

    async def get_input(self, input_id: UUID) -> InputRecord | None:
        row = await self._session.get(InputRow, input_id, populate_existing=True)
        return None if row is None else _input(row)

    # --- messages ----------------------------------------------------------------------

    async def append_message(self, new: NewMessage) -> Written[MessageRecord]:
        row = await self._lock(new.conversation_id)
        existing = await self._session.get(MessageRow, new.message_id, populate_existing=True)
        if existing is not None:
            same = (
                existing.conversation_id == new.conversation_id
                and existing.role == new.role
                and existing.kind == new.kind
                and existing.text == new.text
                and existing.input_id == new.input_id
                and payload_sha256(existing.payload) == payload_sha256(new.payload)
            )
            if not same:
                raise MessageMismatchError("message ID reused with different content")
            return Written(_message(existing), created=False)
        sequence = row.next_message_seq
        created = MessageRow(
            id=new.message_id,
            conversation_id=new.conversation_id,
            sequence=sequence,
            role=new.role,
            kind=new.kind,
            text=new.text,
            payload=new.payload,
            input_id=new.input_id,
        )
        self._session.add(created)
        row.next_message_seq = sequence + 1
        row.updated_at = func.now()
        await self._session.flush()
        await self._session.refresh(created)
        return Written(_message(created), created=True)

    async def list_messages(
        self, conversation_id: UUID, *, after_sequence: int = 0, limit: int = 200
    ) -> list[MessageRecord]:
        rows = await self._session.scalars(
            select(MessageRow)
            .where(
                MessageRow.conversation_id == conversation_id,
                MessageRow.sequence > after_sequence,
            )
            .order_by(MessageRow.sequence)
            .limit(limit)
        )
        return [_message(row) for row in rows]

    # --- events ------------------------------------------------------------------------

    async def append_event(self, new: NewEvent) -> Written[EventRecord]:
        row = await self._lock(new.conversation_id)
        existing = await self._session.scalar(
            select(EventRow).where(EventRow.event_id == new.event_id)
        )
        if existing is not None:
            if (
                existing.conversation_id != new.conversation_id
                or existing.event_type != new.event_type
                or payload_sha256(existing.payload) != payload_sha256(new.payload)
            ):
                raise EventMismatchError("event ID reused with different content")
            return Written(_event(existing), created=False)
        sequence = row.next_event_seq
        created = EventRow(
            conversation_id=new.conversation_id,
            sequence=sequence,
            event_id=new.event_id,
            event_type=new.event_type,
            payload=new.payload,
        )
        self._session.add(created)
        row.next_event_seq = sequence + 1
        row.updated_at = func.now()
        await self._session.flush()
        return Written(_event(created), created=True)

    async def list_events(
        self, conversation_id: UUID, *, after_sequence: int = 0, limit: int = 200
    ) -> list[EventRecord]:
        rows = await self._session.scalars(
            select(EventRow)
            .where(EventRow.conversation_id == conversation_id, EventRow.sequence > after_sequence)
            .order_by(EventRow.sequence)
            .limit(limit)
        )
        return [_event(row) for row in rows]

    # --- input claims ------------------------------------------------------------------

    async def claim_next_input(self, worker_id: str, *, lease_seconds: int) -> InputClaim | None:
        """Claim the oldest due input that heads its conversation's queue.

        Only the earliest non-final input of an active conversation is eligible, so a
        `processing` or `needs_reconciliation` input blocks the rest of its conversation (and
        only that conversation). `SKIP LOCKED` lets concurrent claimers pick other inputs.
        """
        _check_lease(lease_seconds)
        token = uuid4()
        row = (
            await self._session.execute(
                text(_CLAIM_SQL),
                {"token": token, "worker": worker_id, "lease": lease_seconds},
            )
        ).first()
        return None if row is None else await self._claim(row[0], token)

    async def list_expired_claims(self, *, limit: int = 20) -> list[ExpiredClaim]:
        """`processing` inputs whose lease expired. An expired lease alone does not prove the
        holder is gone: the caller must also hold the thread's advisory lock to take over."""
        rows = await self._session.execute(
            select(
                InputRow.id,
                InputRow.claim_token,
                ConversationRow.thread_id,
                InputRow.lease_expires_at,
            )
            .join(ConversationRow, ConversationRow.id == InputRow.conversation_id)
            .where(InputRow.status == PROCESSING, InputRow.lease_expires_at < func.now())
            .order_by(InputRow.lease_expires_at)
            .limit(limit)
        )
        return [
            ExpiredClaim(input_id=r[0], claim_token=r[1], thread_id=r[2], lease_expires_at=r[3])
            for r in rows
            if r[1] is not None and r[3] is not None
        ]

    async def take_over_expired_claim(
        self, expired: ExpiredClaim, worker_id: str, *, lease_seconds: int
    ) -> InputClaim | None:
        """Compare-and-swap of the fencing token; the previous holder's writes fail afterwards."""
        _check_lease(lease_seconds)
        token = uuid4()
        row = (
            await self._session.execute(
                text(_TAKE_OVER_SQL),
                {
                    "token": token,
                    "worker": worker_id,
                    "lease": lease_seconds,
                    "id": expired.input_id,
                    "old": expired.claim_token,
                },
            )
        ).first()
        return None if row is None else await self._claim(row[0], token)

    async def renew_lease(self, claim: InputClaim, *, lease_seconds: int) -> datetime:
        """Heartbeat. Fails once the lease has expired: the holder must stop, not resurrect it."""
        _check_lease(lease_seconds)
        row = (
            await self._session.execute(
                text(_RENEW_SQL),
                {"lease": lease_seconds, "id": claim.input.input_id, "token": claim.claim_token},
            )
        ).first()
        if row is None:
            raise StaleClaimError(str(claim.input.input_id))
        renewed: datetime = row[0]
        return renewed

    async def is_claim_live(self, claim: InputClaim) -> bool:
        """Read-only fence check: still `processing`, same token, lease not expired."""
        found = await self._session.scalar(
            select(InputRow.id).where(
                InputRow.id == claim.input.input_id,
                InputRow.status == PROCESSING,
                InputRow.claim_token == claim.claim_token,
                InputRow.lease_expires_at >= func.now(),
            )
        )
        return found is not None

    async def bind_lineage(
        self, claim: InputClaim, observation: LineageObservation
    ) -> LineageResult:
        """Verify the thread's checkpoint lineage before running (or finishing) the input.

        Accepted: the latest checkpoint is the one recorded by the last completed input
        (FRESH), or a direct child of it that already contains this input - a previous attempt
        finished the graph but crashed before completing (ALREADY_EXECUTED). Anything else is
        unverifiable and moves the input to `needs_reconciliation`; it never continues silently.
        """
        row = await self._fenced(claim)
        expected = await self._last_completed_checkpoint(row.conversation_id)
        if observation.latest_checkpoint_id == expected:
            result = LineageResult.FRESH
        elif (
            observation.latest_contains_input
            and observation.latest_checkpoint_id is not None
            and observation.latest_parent_checkpoint_id == expected
        ):
            result = LineageResult.ALREADY_EXECUTED
        else:
            _to_reconciliation(row, "checkpoint_lineage_mismatch")
            await self._session.flush()
            return LineageResult.MISMATCH
        row.base_checkpoint_id = expected
        row.lineage_checked_at = func.now()
        row.updated_at = func.now()
        await self._session.flush()
        return result

    async def record_side_effect(
        self, claim: InputClaim, event: NewSideEffectEvent
    ) -> Written[SideEffectEvent]:
        """Append one journal event. `started` requires a live lease (no new side effects
        after expiry); replaying the same event ID with identical content is a no-op."""
        existing = await self._session.get(SideEffectRow, event.event_id)
        if existing is not None:
            same = (
                existing.input_id == claim.input.input_id
                and existing.operation_key == event.operation_key
                and existing.tool == event.tool
                and existing.phase == event.phase.value
                and existing.claim_token == claim.claim_token
                and existing.request_sha256 == event.request_sha256
                and existing.outcome_code == event.outcome_code
            )
            if not same:
                raise SideEffectConflictError("journal event ID reused with different content")
            return Written(_side_effect(existing), created=False)
        await self._fenced(claim, live_lease=event.phase is SideEffectPhase.STARTED)
        created = SideEffectRow(
            event_id=event.event_id,
            input_id=claim.input.input_id,
            operation_key=event.operation_key,
            tool=event.tool,
            phase=event.phase.value,
            claim_token=claim.claim_token,
            attempt=claim.attempt,
            request_sha256=event.request_sha256,
            outcome_code=event.outcome_code,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(created)
                await self._session.flush()
        except DBAPIError as error:
            raise SideEffectConflictError(_reason(error)) from None
        return Written(_side_effect(created), created=True)

    async def list_side_effects(self, input_id: UUID) -> list[SideEffectEvent]:
        rows = await self._session.scalars(
            select(SideEffectRow)
            .where(SideEffectRow.input_id == input_id)
            .order_by(SideEffectRow.journal_seq)
        )
        return [_side_effect(r) for r in rows]

    async def stage_response(self, claim: InputClaim, messages: Sequence[StagedMessage]) -> bool:
        """Stage the complete response for this claim attempt (idempotent, all-or-nothing).

        Requires a live lease. Header and messages are written in this one transaction and a
        deferred database trigger rejects an incomplete set at commit. Re-staging the same
        response for the same claim is a no-op (False); different content raises.
        """
        row = await self._fenced(claim, live_lease=True)
        refs = [m.ref for m in messages]
        manifest = manifest_sha256(refs)
        for message in messages:
            _check_staged_payload(message.payload)
        existing = await self._session.get(
            ResponseStagingRow, (row.id, claim.claim_token), populate_existing=True
        )
        if existing is not None:
            if existing.manifest_sha256 != manifest or existing.message_count != len(refs):
                raise StagingConflictError("response already staged with different content")
            return False
        self._session.add(
            ResponseStagingRow(
                input_id=row.id,
                claim_token=claim.claim_token,
                attempt=claim.attempt,
                message_count=len(refs),
                manifest_sha256=manifest,
            )
        )
        await self._session.flush()
        for position, (message, ref) in enumerate(zip(messages, refs, strict=True)):
            self._session.add(
                ResponseStagingMessageRow(
                    input_id=row.id,
                    claim_token=claim.claim_token,
                    position=position,
                    message_id=UUID(message.message_id),
                    role="assistant",
                    kind=message.kind,
                    text=message.text,
                    payload=dict(message.payload),
                    content_sha256=ref.content_sha256,
                )
            )
        await self._session.flush()
        return True

    async def complete_input(
        self,
        claim: InputClaim,
        *,
        checkpoint_id: str,
        parent_checkpoint_id: str | None,
        message_refs: Sequence[MessageRef],
        events: Sequence[NewEvent] = (),
    ) -> str:
        """Fenced finish: publish the staged response, events and `completed` atomically.

        Requires (1) a lineage bound by `bind_lineage` and a checkpoint whose parent is that base,
        and (2) a complete staged response of this input whose manifest equals the checkpoint's
        `message_refs` and whose every message re-hashes to its reference. Otherwise the input
        goes to `needs_reconciliation` and nothing is published.
        """
        row = await self._fenced(claim)
        if row.lineage_checked_at is None:
            _to_reconciliation(row, "checkpoint_lineage_unverified")
        elif (
            parent_checkpoint_id != row.base_checkpoint_id or checkpoint_id == parent_checkpoint_id
        ):
            _to_reconciliation(row, "checkpoint_parent_mismatch")
        else:
            staged = await self._matching_staging(row.id, list(message_refs))
            if staged is None:
                _to_reconciliation(row, "staged_response_missing_or_mismatched")
            else:
                for message in staged:
                    await self.append_message(
                        NewMessage(
                            message_id=message.message_id,
                            conversation_id=row.conversation_id,
                            role="assistant",
                            kind=message.kind,
                            text=message.text,
                            payload=dict(message.payload),
                            input_id=row.id,
                        )
                    )
                for event in events:
                    await self.append_event(event)
                row.status = COMPLETED
                row.execution_ref = checkpoint_id
                row.completed_at = func.now()
                row.updated_at = func.now()
        await self._session.flush()
        return row.status

    async def _matching_staging(
        self, input_id: UUID, refs: list[MessageRef]
    ) -> list[ResponseStagingMessageRow] | None:
        """The newest complete staging of the input that matches `refs` exactly, re-verified."""
        manifest = manifest_sha256(refs)
        headers = await self._session.scalars(
            select(ResponseStagingRow)
            .where(
                ResponseStagingRow.input_id == input_id,
                ResponseStagingRow.manifest_sha256 == manifest,
            )
            .order_by(ResponseStagingRow.created_at.desc(), ResponseStagingRow.attempt.desc())
        )
        for header in headers:
            messages = list(
                await self._session.scalars(
                    select(ResponseStagingMessageRow)
                    .where(
                        ResponseStagingMessageRow.input_id == input_id,
                        ResponseStagingMessageRow.claim_token == header.claim_token,
                    )
                    .order_by(ResponseStagingMessageRow.position)
                )
            )
            if len(messages) != header.message_count or len(messages) != len(refs):
                continue
            if all(
                str(m.message_id) == ref.message_id
                and m.content_sha256 == ref.content_sha256
                and content_sha256(
                    StagedMessage(str(m.message_id), m.kind, m.text, dict(m.payload))
                )
                == ref.content_sha256
                for m, ref in zip(messages, refs, strict=True)
            ):
                return messages
        return None

    async def fail_input(
        self,
        claim: InputClaim,
        *,
        error_code: str,
        replay_safe_tools: frozenset[str],
        max_attempts: int = MAX_ATTEMPTS,
    ) -> FailureDecision:
        """Classify a failed attempt from the journal, never from optimism:

        - a side effect of a tool outside `replay_safe_tools` was started -> reconciliation;
        - attempts exhausted: no side effect at all -> `dead`, otherwise -> reconciliation;
        - otherwise -> `retry` after the backoff for this attempt.
        """
        row = await self._fenced(claim)
        started = {
            e.tool
            for e in await self.list_side_effects(row.id)
            if e.phase == SideEffectPhase.STARTED.value
        }
        row.last_error_code = error_code
        row.updated_at = func.now()
        if started - replay_safe_tools:
            _to_reconciliation(row, "unverified_side_effect")
        elif row.attempts >= max_attempts:
            if started:
                _to_reconciliation(row, "attempts_exhausted_after_side_effects")
            else:
                row.status = DEAD
                row.completed_at = func.now()
        else:
            row.status = RETRY
            row.next_attempt_at = func.now() + timedelta(seconds=retry_delay_seconds(row.attempts))
        await self._session.flush()
        await self._session.refresh(row)
        return FailureDecision(row.status, row.next_attempt_at, row.reconciliation_reason)

    async def mark_needs_reconciliation(self, claim: InputClaim, *, reason: str) -> None:
        row = await self._fenced(claim)
        _to_reconciliation(row, reason)
        await self._session.flush()

    async def _claim(self, input_id: UUID, token: UUID) -> InputClaim:
        row = await self._session.get(InputRow, input_id, populate_existing=True)
        if row is None or row.claim_token != token:  # pragma: no cover - just updated
            raise StaleClaimError(str(input_id))
        conversation = await self._session.get(ConversationRow, row.conversation_id)
        assert conversation is not None
        assert row.claimed_by is not None and row.lease_expires_at is not None
        return InputClaim(
            input=_input(row),
            claim_token=token,
            attempt=row.attempts,
            claimed_by=row.claimed_by,
            lease_expires_at=row.lease_expires_at,
            thread_id=conversation.thread_id,
            owner_subject_id=conversation.owner_subject_id,
            expected_base_checkpoint_id=await self._last_completed_checkpoint(row.conversation_id),
        )

    async def _fenced(self, claim: InputClaim, *, live_lease: bool = False) -> InputRow:
        """Lock the input row iff the claim still owns it (fencing token check)."""
        statement = (
            select(InputRow)
            .where(
                InputRow.id == claim.input.input_id,
                InputRow.status == PROCESSING,
                InputRow.claim_token == claim.claim_token,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if live_lease:
            statement = statement.where(InputRow.lease_expires_at >= func.now())
        row = await self._session.scalar(statement)
        if row is None:
            raise StaleClaimError(str(claim.input.input_id))
        return row

    async def _last_completed_checkpoint(self, conversation_id: UUID) -> str | None:
        return await self._session.scalar(
            select(InputRow.execution_ref)
            .where(InputRow.conversation_id == conversation_id, InputRow.status == COMPLETED)
            .order_by(InputRow.sequence.desc())
            .limit(1)
        )

    # --- helpers -----------------------------------------------------------------------

    async def _lock(self, conversation_id: UUID) -> ConversationRow:
        row = await self._session.scalar(
            select(ConversationRow)
            .where(ConversationRow.id == conversation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise ConversationNotFoundError(str(conversation_id))
        return row


class AgentPersistence:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AgentStore]:
        """One short transaction: committed on success, rolled back on any exception."""
        async with self._sessions() as session, session.begin():
            yield AgentStore(session)


__all__ = [
    "AgentPersistence",
    "AgentStore",
    "CaseBindingConflictError",
    "ConversationConflictError",
    "ConversationNotFoundError",
    "ConversationRecord",
    "EventMismatchError",
    "EventRecord",
    "InputPayloadMismatchError",
    "InputRecord",
    "MessageMismatchError",
    "MessageRecord",
    "NewConversation",
    "NewEvent",
    "NewInput",
    "NewMessage",
    "PersistenceConflictError",
    "Written",
    "payload_sha256",
]
