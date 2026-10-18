"""Conversation lifecycle, durable input ingestion and visible history (P2b).

Lifecycle (contract C1, decisions D1/D3): the AI records its conversation first
(`initializing`, own transaction), then calls `CaseLifecyclePort` with no AI transaction open,
then binds the returned case and activates the conversation (second transaction). There is no
cross-module transaction; every step is idempotent, so a crash or a Business outage at any
point is recovered by repeating the same request:

- `conversation_id`, `thread_id` and the case `operation_id` are three separate values derived
  from (owner, client request ID) under distinct namespaces. A retry of the same request maps to
  the same conversation; Business replays the same operation and returns the same case.
- Ownership is enforced on every call; another owner's conversation is indistinguishable from
  a missing one. Only server-established actors (`ActorRef`) are accepted, never model output.
"""

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.ai.application import errors
from app.ai.application.ports import conversation_store as ps
from app.ai.application.ports.graph_runner import AssistantMessage
from app.ai.application.ports.input_queue import EnqueueOutcome, InputPayloadMismatchError
from app.ai.application.turn_service import MAX_MESSAGE_CHARS, TurnBinding
from app.contracts.case_lifecycle import CaseLifecyclePort, CreateCaseCommand, CreateCaseResult
from app.contracts.common import Envelope, EnvelopeStatus, ErrorCode
from app.contracts.representatives import RepresentativeAnsweredEvent
from app.contracts.tool_context import ActorRef, Role, ToolContext

_NS_CONVERSATION = uuid5(NAMESPACE_URL, "urn:strauss:ai:conversation")
_NS_THREAD = uuid5(NAMESPACE_URL, "urn:strauss:ai:thread")
_NS_CASE_OPERATION = uuid5(NAMESPACE_URL, "urn:strauss:ai:case-operation")
_NS_INPUT = uuid5(NAMESPACE_URL, "urn:strauss:ai:input")
_NS_MESSAGE = uuid5(NAMESPACE_URL, "urn:strauss:ai:message")
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

DEFAULT_BUSINESS_TIMEOUT_SECONDS = 10.0
_UNAVAILABLE = {ErrorCode.INFRA_UNAVAILABLE, ErrorCode.RATE_LIMITED}
_ACCESS = {ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN, ErrorCode.UNAUTHENTICATED}


@dataclass(frozen=True)
class ConversationView:
    conversation_id: UUID
    thread_id: UUID
    status: str
    case_id: UUID | None
    calculation_month: str

    @property
    def is_active(self) -> bool:
        return self.status == "active" and self.case_id is not None


@dataclass(frozen=True)
class SubmittedMessage:
    input_id: UUID
    message_id: UUID
    input_sequence: int
    message_sequence: int
    duplicate: bool
    """True when the same client message was already recorded (nothing new was written)."""


def _view(record: ps.ConversationRecord) -> ConversationView:
    return ConversationView(
        conversation_id=record.conversation_id,
        thread_id=record.thread_id,
        status=record.status,
        case_id=record.case_id,
        calculation_month=record.calculation_month,
    )


def _new_trace_id() -> str:
    return uuid4().hex


class ConversationService:
    def __init__(
        self,
        transactions: ps.ConversationTransactions,
        lifecycle: CaseLifecyclePort,
        *,
        business_timeout_seconds: float = DEFAULT_BUSINESS_TIMEOUT_SECONDS,
        trace_id: Callable[[], str] = _new_trace_id,
    ) -> None:
        self._tx = transactions
        self._lifecycle = lifecycle
        self._timeout = business_timeout_seconds
        self._trace_id = trace_id

    # --- lifecycle ---------------------------------------------------------------------

    async def start_conversation(
        self, actor: ActorRef, *, client_request_id: UUID, calculation_month: str
    ) -> ConversationView:
        """Create (or resume) the conversation for this client request and activate it."""
        _require_employee(actor)
        if not _MONTH.match(calculation_month):
            raise errors.ConversationRequestError("calculation month must be YYYY-MM")
        key = f"{actor.subject_id}:{client_request_id}"
        new = ps.NewConversation(
            conversation_id=uuid5(_NS_CONVERSATION, key),
            owner_subject_id=actor.subject_id,
            thread_id=uuid5(_NS_THREAD, key),
            calculation_month=calculation_month,
            case_operation_id=uuid5(_NS_CASE_OPERATION, key),
        )
        try:
            async with self._tx.transaction() as store:
                record = (await store.create_conversation(new)).record
        except ps.ConversationConflictError:
            raise errors.ConversationConflictError(
                "client request ID reused with different content"
            ) from None
        return await self._activate(actor, record)

    async def resume_initialization(
        self, actor: ActorRef, conversation_id: UUID
    ) -> ConversationView:
        """Finish an interrupted initialization (no-op for an active conversation)."""
        record = await self._owned(actor, conversation_id)
        return await self._activate(actor, record)

    async def get_conversation(self, actor: ActorRef, conversation_id: UUID) -> ConversationView:
        return _view(await self._owned(actor, conversation_id))

    async def list_conversations(
        self, actor: ActorRef, *, limit: int = 50
    ) -> list[ConversationView]:
        _require_employee(actor)
        async with self._tx.transaction() as store:
            records = await store.list_owned_conversations(actor.subject_id, limit=limit)
        return [_view(r) for r in records]

    async def turn_binding(
        self, actor: ActorRef, conversation_id: UUID, *, trace_id: str | None = None
    ) -> TurnBinding:
        """Thread ID and `ToolContext` for an active, owned conversation."""
        view = await self._ensure_active(actor, conversation_id)
        assert view.case_id is not None
        return TurnBinding(
            thread_id=str(view.thread_id),
            tool_context=ToolContext(
                actor=actor,
                conversation_id=view.conversation_id,
                case_id=view.case_id,
                trace_id=trace_id or self._trace_id(),
            ),
        )

    # --- inputs and history ------------------------------------------------------------

    async def submit_user_message(
        self, actor: ActorRef, conversation_id: UUID, *, client_message_id: UUID, text: str
    ) -> SubmittedMessage:
        """Durably enqueue a user turn and record it in the visible history (one AI
        transaction). Replaying the same client message ID is a no-op."""
        if not text.strip():
            raise errors.ConversationRequestError("a user message requires text")
        if len(text) > MAX_MESSAGE_CHARS:
            raise errors.ConversationRequestError("the message exceeds the supported length")
        view = await self._ensure_active(actor, conversation_id)
        key = f"{view.conversation_id}:user:{client_message_id}"
        entry = ps.NewInput(
            input_id=uuid5(_NS_INPUT, key),
            conversation_id=view.conversation_id,
            source=ps.USER,
            external_id=client_message_id,
            payload={"text": text},
        )
        message = ps.NewMessage(
            message_id=uuid5(_NS_MESSAGE, key),
            conversation_id=view.conversation_id,
            role=ps.USER,
            kind="text",
            text=text,
            input_id=entry.input_id,
        )
        try:
            async with self._tx.transaction() as store:
                queued = await store.enqueue_input(entry)
                stored = await store.append_message(message)
        except ps.PersistenceConflictError:
            raise errors.ConversationConflictError(
                "client message ID reused with different content"
            ) from None
        return SubmittedMessage(
            input_id=queued.record.input_id,
            message_id=stored.record.message_id,
            input_sequence=queued.record.sequence,
            message_sequence=stored.record.sequence,
            duplicate=not queued.created,
        )

    async def enqueue_representative_answered(
        self, event: RepresentativeAnsweredEvent
    ) -> EnqueueOutcome:
        """`DurableInputQueue`: deduplicated by event ID (called by the worker, decision D1)."""
        async with self._tx.transaction() as store:
            record = await store.get_conversation_by_case(event.case_id)
            if record is None:
                raise errors.ConversationAccessError("no conversation is bound to this case")
            entry = ps.NewInput(
                input_id=uuid5(_NS_INPUT, f"representative_answered:{event.event_id}"),
                conversation_id=record.conversation_id,
                source=ps.REPRESENTATIVE_ANSWERED,
                external_id=event.event_id,
                payload=event.model_dump(mode="json", by_alias=True),
            )
            try:
                written = await store.enqueue_input(entry)
            except ps.InputPayloadMismatchError:
                raise InputPayloadMismatchError(str(event.event_id)) from None
        return EnqueueOutcome.ENQUEUED if written.created else EnqueueOutcome.DUPLICATE

    async def record_assistant_messages(
        self, conversation_id: UUID, input_id: UUID, messages: Sequence[AssistantMessage]
    ) -> list[ps.MessageRecord]:
        """Append a turn's assistant messages (deterministic IDs make replays no-ops)."""
        try:
            async with self._tx.transaction() as store:
                owned_input = await store.get_input(input_id)
                if owned_input is None or owned_input.conversation_id != conversation_id:
                    raise errors.ConversationAccessError("unknown input")
                return [
                    (
                        await store.append_message(
                            ps.NewMessage(
                                message_id=m.message_id,
                                conversation_id=conversation_id,
                                role="assistant",
                                kind=m.kind,
                                text=m.text,
                                payload=dict(m.payload),
                                input_id=input_id,
                            )
                        )
                    ).record
                    for m in messages
                ]
        except ps.PersistenceConflictError:
            raise errors.ConversationConflictError("assistant message ID reused") from None

    async def history(
        self, actor: ActorRef, conversation_id: UUID, *, after_sequence: int = 0, limit: int = 200
    ) -> list[ps.MessageRecord]:
        await self._owned(actor, conversation_id)
        async with self._tx.transaction() as store:
            return await store.list_messages(
                conversation_id, after_sequence=after_sequence, limit=limit
            )

    # --- internals ---------------------------------------------------------------------

    async def _owned(self, actor: ActorRef, conversation_id: UUID) -> ps.ConversationRecord:
        _require_employee(actor)
        async with self._tx.transaction() as store:
            record = await store.get_owned_conversation(conversation_id, actor.subject_id)
        if record is None:
            raise errors.ConversationAccessError()
        return record

    async def _ensure_active(self, actor: ActorRef, conversation_id: UUID) -> ConversationView:
        record = await self._owned(actor, conversation_id)
        if record.status == "active":
            return _view(record)
        return await self._activate(actor, record)

    async def _activate(self, actor: ActorRef, record: ps.ConversationRecord) -> ConversationView:
        if record.status == "active":
            return _view(record)
        case = await self._create_case(actor, record)  # no AI transaction is open here
        try:
            async with self._tx.transaction() as store:
                bound = await store.bind_case(record.conversation_id, case.case_id)
        except ps.CaseBindingConflictError:
            raise errors.ConversationInitializationError(
                "the business case is already bound differently"
            ) from None
        return _view(bound.record)

    async def _create_case(
        self, actor: ActorRef, record: ps.ConversationRecord
    ) -> CreateCaseResult:
        command = CreateCaseCommand(
            conversation_id=record.conversation_id,
            calculation_month=record.calculation_month,
            operation_id=record.case_operation_id,
        )
        try:
            envelope: Envelope[CreateCaseResult] = await asyncio.wait_for(
                self._lifecycle.create_case_for_conversation(actor, command), self._timeout
            )
        except Exception as error:  # timeout or transport failure: retryable, nothing lost
            raise errors.BusinessUnavailableError(type(error).__name__) from None
        if envelope.status is EnvelopeStatus.ERROR:
            assert envelope.error is not None
            code, trace = envelope.error.code, envelope.error.trace_id
            if code in _UNAVAILABLE:
                raise errors.BusinessUnavailableError(code.value, trace_id=trace)
            if code in _ACCESS:
                raise errors.ConversationAccessError(code.value, trace_id=trace)
            if code is ErrorCode.OPERATION_PAYLOAD_MISMATCH:
                raise errors.ConversationConflictError(code.value, trace_id=trace)
            raise errors.ConversationInitializationError(code.value, trace_id=trace)
        if envelope.data is None or envelope.data.case_id is None:
            raise errors.ConversationInitializationError(
                "case creation returned no case", trace_id=envelope.trace_id
            )
        return envelope.data


def _require_employee(actor: ActorRef) -> None:
    if Role.EMPLOYEE not in actor.roles:
        raise errors.ConversationAccessError("only employees own conversations")
