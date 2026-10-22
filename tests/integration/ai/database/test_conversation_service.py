"""ConversationService against real PostgreSQL (`strauss_ai_test`) and the C1 lifecycle fake."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.ai.application import errors
from app.ai.application.conversation_service import ConversationService
from app.ai.application.ports.conversation_store import ConversationStore
from app.ai.application.ports.graph_runner import AssistantMessage
from app.ai.application.ports.input_queue import (
    DurableInputQueue,
    EnqueueOutcome,
    InputPayloadMismatchError,
)
from app.ai.infrastructure.persistence.store import AgentPersistence
from app.contracts.common import ErrorCode
from app.contracts.representatives import RepresentativeAnsweredEvent
from app.contracts.tool_context import ActorRef, Origin, Role
from tests.fakes.ai.fake_case_lifecycle import FakeCaseLifecycle
from tests.integration.ai.database.conftest import sql

MONTH = "2026-01"


def _employee() -> ActorRef:
    return ActorRef(subject_id=uuid4(), roles=frozenset({Role.EMPLOYEE}), origin=Origin.HTTP)


class CrashOnce:
    """Wraps the store so that one named method raises once (simulated crash)."""

    def __init__(self, persistence: AgentPersistence, method: str) -> None:
        self._persistence = persistence
        self._method = method
        self.crashed = False

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[ConversationStore]:
        async with self._persistence.transaction() as store:
            if not self.crashed:
                original = getattr(store, self._method)

                async def crash(*args: Any, **kwargs: Any) -> Any:
                    self.crashed = True
                    await original(*args, **kwargs)
                    raise ConnectionError("simulated crash before commit")

                setattr(store, self._method, crash)
            yield store


@pytest.fixture
def lifecycle() -> FakeCaseLifecycle:
    return FakeCaseLifecycle()


@pytest.fixture
def service(persistence: AgentPersistence, lifecycle: FakeCaseLifecycle) -> ConversationService:
    return ConversationService(persistence, lifecycle, business_timeout_seconds=1.0)


# --- lifecycle -------------------------------------------------------------------------


async def test_start_creates_an_active_conversation_with_separate_ids(
    service: ConversationService, lifecycle: FakeCaseLifecycle
) -> None:
    actor = _employee()
    view = await service.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    assert view.is_active and view.status == "active"
    assert view.case_id == lifecycle.case_for(view.conversation_id)
    assert len({view.conversation_id, view.thread_id, lifecycle.calls[0][1].operation_id}) == 3
    command = lifecycle.calls[0][1]
    assert command.conversation_id == view.conversation_id
    assert command.calculation_month == MONTH


async def test_start_is_idempotent_per_client_request(
    service: ConversationService, lifecycle: FakeCaseLifecycle
) -> None:
    actor, request = _employee(), uuid4()
    first = await service.start_conversation(
        actor, client_request_id=request, calculation_month=MONTH
    )
    second = await service.start_conversation(
        actor, client_request_id=request, calculation_month=MONTH
    )
    assert first == second
    assert len(lifecycle.calls) == 1  # an active conversation does not call Business again
    other = await service.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    assert other.conversation_id != first.conversation_id


async def test_same_request_id_from_another_employee_is_a_separate_conversation(
    service: ConversationService,
) -> None:
    request = uuid4()
    a = await service.start_conversation(
        _employee(), client_request_id=request, calculation_month=MONTH
    )
    b = await service.start_conversation(
        _employee(), client_request_id=request, calculation_month=MONTH
    )
    assert a.conversation_id != b.conversation_id and a.thread_id != b.thread_id


async def test_reused_request_with_different_month_is_a_conflict(
    service: ConversationService,
) -> None:
    actor, request = _employee(), uuid4()
    await service.start_conversation(actor, client_request_id=request, calculation_month=MONTH)
    with pytest.raises(errors.ConversationConflictError):
        await service.start_conversation(
            actor, client_request_id=request, calculation_month="2026-02"
        )


@pytest.mark.parametrize("month", ["2026-13", "26-01", ""])
async def test_invalid_month_is_rejected_before_any_write(
    service: ConversationService, lifecycle: FakeCaseLifecycle, month: str
) -> None:
    with pytest.raises(errors.ConversationRequestError):
        await service.start_conversation(
            _employee(), client_request_id=uuid4(), calculation_month=month
        )
    assert lifecycle.calls == []


@pytest.mark.parametrize("failure", [ErrorCode.INFRA_UNAVAILABLE, ErrorCode.RATE_LIMITED, OSError])
async def test_business_outage_leaves_initializing_and_retry_recovers(
    service: ConversationService,
    lifecycle: FakeCaseLifecycle,
    failure: ErrorCode | type[Exception],
) -> None:
    actor, request = _employee(), uuid4()
    lifecycle.fail_next.append(failure)
    with pytest.raises(errors.BusinessUnavailableError) as raised:
        await service.start_conversation(actor, client_request_id=request, calculation_month=MONTH)
    assert raised.value.retryable
    listed = await service.list_conversations(actor)
    assert [c.status for c in listed] == ["initializing"]
    view = await service.start_conversation(
        actor, client_request_id=request, calculation_month=MONTH
    )
    assert view.is_active and view.conversation_id == listed[0].conversation_id
    operations = {command.operation_id for _, command in lifecycle.calls}
    assert len(operations) == 1  # the retry reused the same Business operation ID


async def test_business_timeout_is_retryable(
    persistence: AgentPersistence, lifecycle: FakeCaseLifecycle
) -> None:
    lifecycle.delay_seconds = 0.5
    slow = ConversationService(persistence, lifecycle, business_timeout_seconds=0.05)
    actor, request = _employee(), uuid4()
    with pytest.raises(errors.BusinessUnavailableError):
        await slow.start_conversation(actor, client_request_id=request, calculation_month=MONTH)
    lifecycle.delay_seconds = 0.0
    view = await slow.start_conversation(actor, client_request_id=request, calculation_month=MONTH)
    assert view.is_active


async def test_crash_after_case_creation_before_activation_recovers(
    persistence: AgentPersistence, lifecycle: FakeCaseLifecycle
) -> None:
    crashing = CrashOnce(persistence, "bind_case")
    service = ConversationService(crashing, lifecycle)
    actor, request = _employee(), uuid4()
    with pytest.raises(ConnectionError):
        await service.start_conversation(actor, client_request_id=request, calculation_month=MONTH)
    [pending] = await service.list_conversations(actor)
    assert pending.status == "initializing" and pending.case_id is None  # bind rolled back
    created_case = lifecycle.case_for(pending.conversation_id)
    assert created_case is not None  # Business already committed its case

    view = await service.resume_initialization(actor, pending.conversation_id)
    assert view.is_active and view.case_id == created_case
    operation_ids = [command.operation_id for _, command in lifecycle.calls]
    assert len(operation_ids) == 2 and len(set(operation_ids)) == 1  # replayed, not re-created


async def test_crash_before_the_conversation_commits_creates_nothing(
    persistence: AgentPersistence, lifecycle: FakeCaseLifecycle
) -> None:
    service = ConversationService(CrashOnce(persistence, "create_conversation"), lifecycle)
    actor, request = _employee(), uuid4()
    with pytest.raises(ConnectionError):
        await service.start_conversation(actor, client_request_id=request, calculation_month=MONTH)
    assert await service.list_conversations(actor) == []
    assert lifecycle.calls == []  # Business is never called before the intent is committed
    view = await service.start_conversation(
        actor, client_request_id=request, calculation_month=MONTH
    )
    assert view.is_active


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (ErrorCode.NOT_FOUND, errors.ConversationAccessError),
        (ErrorCode.FORBIDDEN, errors.ConversationAccessError),
        (ErrorCode.UNAUTHENTICATED, errors.ConversationAccessError),
        (ErrorCode.OPERATION_PAYLOAD_MISMATCH, errors.ConversationConflictError),
        (ErrorCode.INVALID_BUSINESS_INPUT, errors.ConversationInitializationError),
    ],
)
async def test_business_rejections_are_mapped_and_stay_initializing(
    service: ConversationService,
    lifecycle: FakeCaseLifecycle,
    code: ErrorCode,
    expected: type[errors.ConversationError],
) -> None:
    actor = _employee()
    lifecycle.fail_next.append(code)
    with pytest.raises(expected) as raised:
        await service.start_conversation(actor, client_request_id=uuid4(), calculation_month=MONTH)
    assert not raised.value.retryable and raised.value.trace_id == "fake"
    assert [c.status for c in await service.list_conversations(actor)] == ["initializing"]


async def test_concurrent_duplicate_starts_yield_one_conversation_and_case(
    service: ConversationService, lifecycle: FakeCaseLifecycle, migrated_ai_db: str
) -> None:
    actor, request = _employee(), uuid4()
    lifecycle.delay_seconds = 0.02
    views = await asyncio.gather(
        *(
            service.start_conversation(actor, client_request_id=request, calculation_month=MONTH)
            for _ in range(5)
        )
    )
    assert len({(v.conversation_id, v.case_id) for v in views}) == 1
    assert all(v.is_active for v in views)
    rows = sql(
        migrated_ai_db,
        "SELECT count(*) FROM agent.conversations WHERE owner_subject_id = %s",
        actor.subject_id,
    )
    assert rows == [(1,)]
    assert len({c.operation_id for _, c in lifecycle.calls}) == 1


# --- ownership -------------------------------------------------------------------------


async def test_other_employees_cannot_see_or_use_a_conversation(
    service: ConversationService,
) -> None:
    owner, intruder = _employee(), _employee()
    view = await service.start_conversation(
        owner, client_request_id=uuid4(), calculation_month=MONTH
    )
    for call in (
        service.get_conversation(intruder, view.conversation_id),
        service.history(intruder, view.conversation_id),
        service.turn_binding(intruder, view.conversation_id),
        service.resume_initialization(intruder, view.conversation_id),
        service.submit_user_message(
            intruder, view.conversation_id, client_message_id=uuid4(), text="שלום"
        ),
    ):
        with pytest.raises(errors.ConversationAccessError):
            await call
    assert await service.list_conversations(intruder) == []
    with pytest.raises(errors.ConversationAccessError):
        await service.get_conversation(owner, uuid4())


async def test_hidden_conversation_leaves_the_list_but_keeps_its_history(
    service: ConversationService,
) -> None:
    owner, intruder = _employee(), _employee()
    kept = await service.start_conversation(
        owner, client_request_id=uuid4(), calculation_month=MONTH
    )
    hidden = await service.start_conversation(
        owner, client_request_id=uuid4(), calculation_month=MONTH
    )
    with pytest.raises(errors.ConversationAccessError):
        await service.hide_conversation(intruder, hidden.conversation_id)
    await service.hide_conversation(owner, hidden.conversation_id)
    await service.hide_conversation(owner, hidden.conversation_id)  # repeating changes nothing
    listed = await service.list_conversations(owner)
    assert [c.conversation_id for c in listed] == [kept.conversation_id]
    assert (await service.get_conversation(owner, hidden.conversation_id)).conversation_id == (
        hidden.conversation_id
    )


async def test_non_employee_actors_are_rejected(service: ConversationService) -> None:
    representative = ActorRef(
        subject_id=uuid4(), roles=frozenset({Role.REPRESENTATIVE}), origin=Origin.HTTP
    )
    with pytest.raises(errors.ConversationAccessError):
        await service.start_conversation(
            representative, client_request_id=uuid4(), calculation_month=MONTH
        )


async def test_turn_binding_uses_the_stored_thread(service: ConversationService) -> None:
    actor = _employee()
    view = await service.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    binding = await service.turn_binding(actor, view.conversation_id, trace_id="t-1")
    assert binding.thread_id == str(view.thread_id) != str(view.conversation_id)
    assert binding.tool_context.case_id == view.case_id
    assert binding.tool_context.actor == actor and binding.tool_context.trace_id == "t-1"


# --- inputs and history ----------------------------------------------------------------


async def test_user_message_is_queued_and_recorded_once(service: ConversationService) -> None:
    actor = _employee()
    view = await service.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    client_message = uuid4()
    text = "מה יהיה הנטו שלי עם רכב חשמלי?"
    first = await service.submit_user_message(
        actor, view.conversation_id, client_message_id=client_message, text=text
    )
    replay = await service.submit_user_message(
        actor, view.conversation_id, client_message_id=client_message, text=text
    )
    assert not first.duplicate and replay.duplicate
    assert (replay.input_id, replay.message_id) == (first.input_id, first.message_id)
    history = await service.history(actor, view.conversation_id)
    assert [(m.role, m.text, m.input_id) for m in history] == [("user", text, first.input_id)]
    with pytest.raises(errors.ConversationConflictError):
        await service.submit_user_message(
            actor, view.conversation_id, client_message_id=client_message, text="טקסט אחר"
        )


@pytest.mark.parametrize("text", ["", "   ", "א" * 8001], ids=["empty", "blank", "too-long"])
async def test_invalid_user_messages_are_rejected(service: ConversationService, text: str) -> None:
    actor = _employee()
    view = await service.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    with pytest.raises(errors.ConversationRequestError):
        await service.submit_user_message(
            actor, view.conversation_id, client_message_id=uuid4(), text=text
        )
    assert await service.history(actor, view.conversation_id) == []


async def test_message_to_an_initializing_conversation_resumes_it_first(
    service: ConversationService, lifecycle: FakeCaseLifecycle
) -> None:
    actor, request = _employee(), uuid4()
    lifecycle.fail_next.append(ErrorCode.INFRA_UNAVAILABLE)
    with pytest.raises(errors.BusinessUnavailableError):
        await service.start_conversation(actor, client_request_id=request, calculation_month=MONTH)
    [pending] = await service.list_conversations(actor)
    submitted = await service.submit_user_message(
        actor, pending.conversation_id, client_message_id=uuid4(), text="שלום"
    )
    assert submitted.message_sequence == 1
    assert (await service.get_conversation(actor, pending.conversation_id)).is_active


async def test_message_is_not_recorded_while_business_is_unavailable(
    service: ConversationService, lifecycle: FakeCaseLifecycle
) -> None:
    actor = _employee()
    lifecycle.fail_next.append(ErrorCode.INFRA_UNAVAILABLE)
    with pytest.raises(errors.BusinessUnavailableError):
        await service.start_conversation(actor, client_request_id=uuid4(), calculation_month=MONTH)
    [pending] = await service.list_conversations(actor)
    lifecycle.fail_next.append(ErrorCode.INFRA_UNAVAILABLE)
    with pytest.raises(errors.BusinessUnavailableError):
        await service.submit_user_message(
            actor, pending.conversation_id, client_message_id=uuid4(), text="שלום"
        )
    assert await service.history(actor, pending.conversation_id) == []


async def test_concurrent_duplicate_messages_record_one_entry(
    service: ConversationService,
) -> None:
    actor = _employee()
    view = await service.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    client_message = uuid4()
    results = await asyncio.gather(
        *(
            service.submit_user_message(
                actor, view.conversation_id, client_message_id=client_message, text="שלום"
            )
            for _ in range(5)
        )
    )
    assert sorted(r.duplicate for r in results) == [False, True, True, True, True]
    assert len(await service.history(actor, view.conversation_id)) == 1


async def test_assistant_messages_append_after_the_user_turn(
    service: ConversationService,
) -> None:
    actor = _employee()
    view = await service.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    submitted = await service.submit_user_message(
        actor, view.conversation_id, client_message_id=uuid4(), text="שלום"
    )
    replies = (
        AssistantMessage(uuid4(), "שלום! במה אפשר לעזור?", "text"),
        AssistantMessage(uuid4(), "אפשר לבדוק רכבים מתאימים.", "suggestion", {"k": "v"}),
    )
    await service.record_assistant_messages(view.conversation_id, submitted.input_id, replies)
    await service.record_assistant_messages(view.conversation_id, submitted.input_id, replies)
    history = await service.history(actor, view.conversation_id)
    assert [(m.sequence, m.role) for m in history] == [
        (1, "user"),
        (2, "assistant"),
        (3, "assistant"),
    ]
    assert history[2].payload == {"k": "v"}
    other = await service.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    with pytest.raises(errors.ConversationAccessError):
        await service.record_assistant_messages(other.conversation_id, submitted.input_id, replies)


async def test_representative_answers_are_deduplicated_by_event_id(
    service: ConversationService, migrated_ai_db: str
) -> None:
    queue: DurableInputQueue = service
    actor = _employee()
    view = await service.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    assert view.case_id is not None
    event = RepresentativeAnsweredEvent(
        event_id=uuid4(),
        occurred_at=datetime(2026, 1, 5, tzinfo=UTC),
        request_id=uuid4(),
        case_id=view.case_id,
        request_version=1,
        case_version_after=2,
    )
    assert await queue.enqueue_representative_answered(event) is EnqueueOutcome.ENQUEUED
    assert await queue.enqueue_representative_answered(event) is EnqueueOutcome.DUPLICATE
    with pytest.raises(InputPayloadMismatchError):
        await queue.enqueue_representative_answered(event.model_copy(update={"request_version": 9}))
    rows = sql(
        migrated_ai_db,
        "SELECT source, status FROM agent.inputs WHERE conversation_id = %s",
        view.conversation_id,
    )
    assert rows == [("representative_answered", "pending")]
    unknown = event.model_copy(update={"event_id": uuid4(), "case_id": uuid4()})
    with pytest.raises(errors.ConversationAccessError):
        await queue.enqueue_representative_answered(unknown)
