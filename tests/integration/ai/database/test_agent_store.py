"""AgentStore against real PostgreSQL: idempotency, constraints, ownership, append-only history."""

import asyncio
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.ai.infrastructure.persistence.engine import JsonFloatError
from app.ai.infrastructure.persistence.store import (
    AgentPersistence,
    CaseBindingConflictError,
    ConversationConflictError,
    ConversationNotFoundError,
    EventMismatchError,
    InputPayloadMismatchError,
    MessageMismatchError,
    NewConversation,
    NewEvent,
    NewInput,
    NewMessage,
)
from tests.integration.ai.database.conftest import sql

HEBREW = "שלום, מה יהיה הנטו שלי אם אבחר ברכב חשמלי?  "


def _new_conversation(owner: UUID | None = None) -> NewConversation:
    return NewConversation(
        conversation_id=uuid4(),
        owner_subject_id=owner or uuid4(),
        thread_id=uuid4(),
        calculation_month="2026-01",
        case_operation_id=uuid4(),
    )


async def _create(persistence: AgentPersistence, owner: UUID | None = None) -> NewConversation:
    new = _new_conversation(owner)
    async with persistence.transaction() as store:
        await store.create_conversation(new)
    return new


def _user_input(conversation_id: UUID, *, external_id: UUID | None = None) -> NewInput:
    return NewInput(
        input_id=uuid4(),
        conversation_id=conversation_id,
        source="user",
        external_id=external_id or uuid4(),
        payload={"text": HEBREW},
    )


# --- conversations ---------------------------------------------------------------------


async def test_create_conversation_is_idempotent(persistence: AgentPersistence) -> None:
    new = _new_conversation()
    async with persistence.transaction() as store:
        first = await store.create_conversation(new)
    async with persistence.transaction() as store:
        second = await store.create_conversation(new)
    assert first.created and not second.created
    assert second.record == first.record
    assert first.record.status == "initializing" and first.record.case_id is None
    assert first.record.thread_id != first.record.conversation_id


async def test_conversation_id_reused_with_different_content_is_rejected(
    persistence: AgentPersistence,
) -> None:
    new = await _create(persistence)
    changed = NewConversation(**{**new.__dict__, "owner_subject_id": uuid4()})
    with pytest.raises(ConversationConflictError):
        async with persistence.transaction() as store:
            await store.create_conversation(changed)


async def test_thread_and_case_operation_ids_are_unique(persistence: AgentPersistence) -> None:
    new = await _create(persistence)
    for clash in (
        {"thread_id": new.thread_id},
        {"case_operation_id": new.case_operation_id},
    ):
        other = NewConversation(**{**_new_conversation().__dict__, **clash})
        with pytest.raises(ConversationConflictError):
            async with persistence.transaction() as store:
                await store.create_conversation(other)


async def test_thread_id_must_differ_from_conversation_id(persistence: AgentPersistence) -> None:
    new = _new_conversation()
    same = NewConversation(**{**new.__dict__, "thread_id": new.conversation_id})
    with pytest.raises(ConversationConflictError):
        async with persistence.transaction() as store:
            await store.create_conversation(same)


async def test_ownership_is_checked(persistence: AgentPersistence) -> None:
    owner = uuid4()
    new = await _create(persistence, owner)
    async with persistence.transaction() as store:
        assert await store.get_owned_conversation(new.conversation_id, uuid4()) is None
        owned = await store.get_owned_conversation(new.conversation_id, owner)
        listed = await store.list_owned_conversations(owner)
        foreign = await store.list_owned_conversations(uuid4())
    assert owned is not None and owned.conversation_id == new.conversation_id
    assert [c.conversation_id for c in listed] == [new.conversation_id]
    assert foreign == []


async def test_bind_case_activates_once(persistence: AgentPersistence) -> None:
    new = await _create(persistence)
    case_id = uuid4()
    async with persistence.transaction() as store:
        first = await store.bind_case(new.conversation_id, case_id)
    async with persistence.transaction() as store:
        replay = await store.bind_case(new.conversation_id, case_id)
    assert first.created and first.record.status == "active"
    assert not replay.created and replay.record.case_id == case_id
    with pytest.raises(CaseBindingConflictError):
        async with persistence.transaction() as store:
            await store.bind_case(new.conversation_id, uuid4())


async def test_a_case_binds_to_one_conversation_only(persistence: AgentPersistence) -> None:
    first, second = await _create(persistence), await _create(persistence)
    case_id = uuid4()
    async with persistence.transaction() as store:
        await store.bind_case(first.conversation_id, case_id)
    with pytest.raises(CaseBindingConflictError):
        async with persistence.transaction() as store:
            await store.bind_case(second.conversation_id, case_id)


def test_active_conversation_requires_a_case(migrated_ai_db: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(
            migrated_ai_db,
            "INSERT INTO agent.conversations (id, owner_subject_id, thread_id, status, "
            "calculation_month, case_operation_id) VALUES (%s, %s, %s, 'active', '2026-01', %s)",
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )


async def test_unknown_conversation_is_reported(persistence: AgentPersistence) -> None:
    with pytest.raises(ConversationNotFoundError):
        async with persistence.transaction() as store:
            await store.append_message(
                NewMessage(uuid4(), uuid4(), role="user", kind="text", text="x")
            )


# --- messages --------------------------------------------------------------------------


async def test_messages_keep_order_hebrew_and_are_idempotent(
    persistence: AgentPersistence,
) -> None:
    new = await _create(persistence)
    first = NewMessage(uuid4(), new.conversation_id, role="user", kind="text", text=HEBREW)
    second = NewMessage(
        uuid4(),
        new.conversation_id,
        role="assistant",
        kind="answer",
        text="התוצאה מוכנה.",
        payload={"netDelta": "-1939.40", "items": [1, 2]},
    )
    async with persistence.transaction() as store:
        await store.append_message(first)
        await store.append_message(second)
    async with persistence.transaction() as store:
        replay = await store.append_message(first)
        history = await store.list_messages(new.conversation_id)
    assert not replay.created
    assert [(m.sequence, m.text) for m in history] == [(1, HEBREW), (2, "התוצאה מוכנה.")]
    assert history[1].payload == {"netDelta": "-1939.40", "items": [1, 2]}


async def test_message_id_reuse_with_different_text_is_rejected(
    persistence: AgentPersistence,
) -> None:
    new = await _create(persistence)
    message = NewMessage(uuid4(), new.conversation_id, role="user", kind="text", text="א")
    async with persistence.transaction() as store:
        await store.append_message(message)
    with pytest.raises(MessageMismatchError):
        async with persistence.transaction() as store:
            await store.append_message(NewMessage(**{**message.__dict__, "text": "ב"}))


async def test_float_payloads_are_rejected(persistence: AgentPersistence) -> None:
    new = await _create(persistence)
    with pytest.raises((JsonFloatError, DBAPIError)):
        async with persistence.transaction() as store:
            await store.append_message(
                NewMessage(
                    uuid4(),
                    new.conversation_id,
                    role="assistant",
                    kind="answer",
                    text="x",
                    payload={"net": 1.5},
                )
            )


async def test_blank_text_and_unknown_roles_are_rejected(persistence: AgentPersistence) -> None:
    new = await _create(persistence)
    for bad in (
        NewMessage(uuid4(), new.conversation_id, role="user", kind="text", text="   "),
        NewMessage(uuid4(), new.conversation_id, role="system", kind="text", text="x"),
    ):
        with pytest.raises(IntegrityError):
            async with persistence.transaction() as store:
                await store.append_message(bad)


async def test_message_history_is_append_only(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    new = await _create(persistence)
    message = NewMessage(uuid4(), new.conversation_id, role="user", kind="text", text="א")
    async with persistence.transaction() as store:
        await store.append_message(message)
    for statement in (
        "UPDATE agent.messages SET text = 'ב' WHERE id = %s",
        "DELETE FROM agent.messages WHERE id = %s",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation):
            sql(migrated_ai_db, statement, message.message_id)
    with pytest.raises(psycopg.errors.RestrictViolation):
        sql(migrated_ai_db, "TRUNCATE agent.messages CASCADE")


def test_conversations_and_inputs_cannot_be_deleted(migrated_ai_db: str) -> None:
    for table in ("conversations", "inputs", "events"):
        with pytest.raises(psycopg.errors.RestrictViolation):
            sql(migrated_ai_db, f"TRUNCATE agent.{table} CASCADE")
    rows = sql(migrated_ai_db, "SELECT id FROM agent.conversations LIMIT 1")
    if rows:
        with pytest.raises(psycopg.errors.RestrictViolation):
            sql(migrated_ai_db, "DELETE FROM agent.conversations WHERE id = %s", rows[0][0])


async def test_failed_transaction_writes_nothing(persistence: AgentPersistence) -> None:
    new = await _create(persistence)
    with pytest.raises(RuntimeError):
        async with persistence.transaction() as store:
            await store.append_message(
                NewMessage(uuid4(), new.conversation_id, role="user", kind="text", text="א")
            )
            raise RuntimeError("crash before commit")
    async with persistence.transaction() as store:
        assert await store.list_messages(new.conversation_id) == []


# --- inputs ----------------------------------------------------------------------------


async def test_user_inputs_are_deduplicated_per_conversation(
    persistence: AgentPersistence,
) -> None:
    first, second = await _create(persistence), await _create(persistence)
    client_id = uuid4()
    entry = _user_input(first.conversation_id, external_id=client_id)
    async with persistence.transaction() as store:
        created = await store.enqueue_input(entry)
        replay = await store.enqueue_input(entry)
        other = await store.enqueue_input(
            _user_input(second.conversation_id, external_id=client_id)
        )
    assert created.created and not replay.created and other.created
    assert created.record.sequence == 1 and created.record.status == "pending"
    with pytest.raises(InputPayloadMismatchError):
        async with persistence.transaction() as store:
            await store.enqueue_input(NewInput(**{**entry.__dict__, "payload": {"text": "אחר"}}))


async def test_representative_event_ids_are_globally_unique(
    persistence: AgentPersistence,
) -> None:
    first, second = await _create(persistence), await _create(persistence)
    event_id = uuid4()
    entry = NewInput(
        input_id=uuid4(),
        conversation_id=first.conversation_id,
        source="representative_answered",
        external_id=event_id,
        payload={"eventId": str(event_id), "requestVersion": 2},
    )
    async with persistence.transaction() as store:
        assert (await store.enqueue_input(entry)).created
        assert not (await store.enqueue_input(entry)).created
    with pytest.raises(InputPayloadMismatchError):
        async with persistence.transaction() as store:
            await store.enqueue_input(
                NewInput(**{**entry.__dict__, "conversation_id": second.conversation_id})
            )


async def test_concurrent_duplicate_inputs_create_one_row(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    new = await _create(persistence)
    entry = _user_input(new.conversation_id)

    async def enqueue() -> bool:
        async with persistence.transaction() as store:
            return (await store.enqueue_input(entry)).created

    results = await asyncio.gather(*(enqueue() for _ in range(5)))
    assert sorted(results) == [False, False, False, False, True]
    count = sql(
        migrated_ai_db,
        "SELECT count(*) FROM agent.inputs WHERE conversation_id = %s",
        new.conversation_id,
    )
    assert count == [(1,)]


async def test_concurrent_messages_get_gapless_sequences(persistence: AgentPersistence) -> None:
    new = await _create(persistence)

    async def append(index: int) -> None:
        async with persistence.transaction() as store:
            await store.append_message(
                NewMessage(uuid4(), new.conversation_id, role="user", kind="text", text=f"{index}")
            )

    await asyncio.gather(*(append(i) for i in range(8)))
    async with persistence.transaction() as store:
        history = await store.list_messages(new.conversation_id)
    assert [m.sequence for m in history] == list(range(1, 9))


async def test_message_links_to_its_input(persistence: AgentPersistence) -> None:
    new = await _create(persistence)
    entry = _user_input(new.conversation_id)
    async with persistence.transaction() as store:
        await store.enqueue_input(entry)
        stored = await store.append_message(
            NewMessage(
                uuid4(),
                new.conversation_id,
                role="user",
                kind="text",
                text=HEBREW,
                input_id=entry.input_id,
            )
        )
    assert stored.record.input_id == entry.input_id


# --- events ----------------------------------------------------------------------------


async def test_events_are_sequenced_and_deduplicated(persistence: AgentPersistence) -> None:
    new = await _create(persistence)
    first = NewEvent(uuid4(), new.conversation_id, event_type="message.created", payload={"a": 1})
    second = NewEvent(uuid4(), new.conversation_id, event_type="turn.completed")
    async with persistence.transaction() as store:
        await store.append_event(first)
        await store.append_event(second)
        replay = await store.append_event(first)
        events = await store.list_events(new.conversation_id)
        after = await store.list_events(new.conversation_id, after_sequence=1)
    assert not replay.created
    assert [(e.sequence, e.event_type) for e in events] == [
        (1, "message.created"),
        (2, "turn.completed"),
    ]
    assert [e.sequence for e in after] == [2]
    with pytest.raises(EventMismatchError):
        async with persistence.transaction() as store:
            await store.append_event(NewEvent(**{**first.__dict__, "payload": {"a": 2}}))


async def test_events_are_append_only(persistence: AgentPersistence) -> None:
    new = await _create(persistence)
    async with persistence.transaction() as store:
        await store.append_event(NewEvent(uuid4(), new.conversation_id, event_type="x"))
    with pytest.raises(DBAPIError):
        async with persistence.transaction() as store:
            await store._session.execute(  # noqa: SLF001 - deliberate raw mutation attempt
                text("UPDATE agent.events SET event_type = 'y' WHERE conversation_id = :c"),
                {"c": new.conversation_id},
            )
