"""P2c stage 2: PostgreSQL LangGraph checkpointer in `agent` (real PostgreSQL, fakes only)."""

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import create_engine

from app.ai.application.ports.checkpoints import CheckpointFence
from app.ai.application.ports.conversation_store import (
    InputClaim,
    LineageResult,
    NewConversation,
    NewInput,
    StaleClaimError,
)
from app.ai.application.ports.graph_runner import InputKind, TurnInput, TurnOutcome, TurnStatus
from app.ai.application.ports.response_staging import ResponseStager
from app.ai.infrastructure.checkpoints.postgres import (
    FencedPostgresSaver,
    PostgresCheckpointLineage,
    create_checkpointer_pool,
    setup_checkpointer,
)
from app.ai.infrastructure.persistence.models import (
    CHECKPOINTER_TABLES,
    SCHEMA,
    AgentBase,
    include_agent_object,
)
from app.ai.infrastructure.persistence.staging import SqlResponseStager
from app.ai.infrastructure.persistence.store import AgentPersistence
from app.ai.workflow.models import Intent
from app.ai.workflow.runner import LangGraphTurnRunner
from app.contracts.tool_context import ActorRef, Origin, Role, ToolContext
from tests.fakes.ai.fake_llm import ScriptedLlm, interpretation
from tests.fakes.fake_business_port import FakeBusinessPort
from tests.integration.ai.database.conftest import alembic_config, reset_agent_schema, sql

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]
LEASE = 30
MARKER = "trace-SECRET-MARKER-7f3a"


@pytest.fixture
async def pool(migrated_ai_db: str) -> AsyncIterator[Pool]:
    await setup_checkpointer(migrated_ai_db)
    pool = create_checkpointer_pool(migrated_ai_db)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _park_other_inputs(migrated_ai_db: str) -> None:
    rows = sql(migrated_ai_db, "SELECT id FROM agent.inputs WHERE status IN ('pending', 'retry')")
    for (input_id,) in rows:
        sql(
            migrated_ai_db,
            "UPDATE agent.inputs SET status = 'processing', claim_token = gen_random_uuid(), "
            "claimed_by = 'parked', claimed_at = now(), "
            "lease_expires_at = now() + interval '1 day' WHERE id = %s",
            input_id,
        )


def _runner(pool: Pool, llm: ScriptedLlm | None = None) -> LangGraphTurnRunner:
    return LangGraphTurnRunner(
        FakeBusinessPort("success"),
        llm or ScriptedLlm(),
        checkpointer=FencedPostgresSaver(pool),
    )


def _context(conversation_id: UUID) -> ToolContext:
    return ToolContext(
        actor=ActorRef(subject_id=uuid4(), roles=frozenset({Role.EMPLOYEE}), origin=Origin.WORKER),
        conversation_id=conversation_id,
        case_id=uuid4(),
        trace_id=MARKER,
    )


def _list_options_llm() -> ScriptedLlm:
    llm = ScriptedLlm()
    llm.interpret(interpretation(Intent.LIST_OPTIONS))
    return llm


async def _turn(
    pool: Pool,
    thread_id: UUID,
    conversation_id: UUID,
    input_id: UUID,
    *,
    llm: ScriptedLlm | None = None,
    fence: CheckpointFence | None = None,
    stager: ResponseStager | None = None,
) -> TurnOutcome:
    turn = TurnInput(input_id, InputKind.USER_MESSAGE, text="אילו רכבים מתאימים לי?")
    return await _runner(pool, llm or _list_options_llm()).run_turn(
        thread_id=str(thread_id),
        tool_context=_context(conversation_id),
        turn=turn,
        fence=fence,
        stager=stager,
    )


async def _active_conversation(persistence: AgentPersistence) -> NewConversation:
    new = NewConversation(uuid4(), uuid4(), uuid4(), "2026-01", uuid4())
    async with persistence.transaction() as store:
        await store.create_conversation(new)
        await store.bind_case(new.conversation_id, uuid4())
    return new


async def _claim_one(persistence: AgentPersistence, conversation_id: UUID) -> InputClaim:
    async with persistence.transaction() as store:
        entry = NewInput(uuid4(), conversation_id, "user", uuid4(), {"text": "שלום"})
        await store.enqueue_input(entry)
    async with persistence.transaction() as store:
        claim = await store.claim_next_input("w", lease_seconds=LEASE)
    assert claim is not None and claim.input.conversation_id == conversation_id
    return claim


def _fence(persistence: AgentPersistence, claim: InputClaim) -> CheckpointFence:
    async def fence() -> None:
        async with persistence.transaction() as store:
            if not await store.is_claim_live(claim):
                raise StaleClaimError(str(claim.input.input_id))

    return fence


def _checkpoint_text(url: str, thread_id: UUID) -> str:
    """Every stored byte of the thread's checkpoint rows, as text (msgpack keeps UTF-8)."""
    parts: list[str] = []
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        for (row,) in sql(
            url,
            f"SELECT row_to_json(t)::text FROM agent.{table} t WHERE thread_id = %s",
            str(thread_id),
        ):
            parts.append(str(row))
        if table != "checkpoints":
            column = "blob"
            for (data,) in sql(
                url, f"SELECT {column} FROM agent.{table} WHERE thread_id = %s", str(thread_id)
            ):
                if data is not None:
                    assert isinstance(data, bytes | memoryview)
                    parts.append(bytes(data).decode("utf-8", errors="replace"))
    return "\n".join(parts)


# --- setup and isolation ---------------------------------------------------------------


async def test_r20_setup_creates_library_tables_only_in_agent(
    pool: Pool, migrated_ai_db: str
) -> None:
    await setup_checkpointer(migrated_ai_db)  # idempotent second call
    rows = sql(
        migrated_ai_db,
        "SELECT table_schema, table_name FROM information_schema.tables WHERE table_name = ANY(%s)",
        sorted(CHECKPOINTER_TABLES),
    )
    assert {(str(s), str(n)) for s, n in rows} == {(SCHEMA, n) for n in CHECKPOINTER_TABLES}
    versions = sql(
        migrated_ai_db, "SELECT count(*), count(DISTINCT v) FROM agent.checkpoint_migrations"
    )
    assert versions[0][0] == versions[0][1]  # each library migration applied once


async def test_r20_alembic_ignores_and_keeps_library_tables(
    pool: Pool, migrated_ai_db: str
) -> None:
    engine = create_engine(migrated_ai_db)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_schemas": True,
                    "include_name": lambda name, type_, _p: type_ != "schema" or name == SCHEMA,
                    "include_object": include_agent_object,
                    "version_table": "alembic_version_ai",
                    "version_table_schema": SCHEMA,
                    "compare_type": True,
                },
            )
            diffs = compare_metadata(context, AgentBase.metadata)
    finally:
        engine.dispose()
    assert diffs == []  # no `remove_table` for the library tables
    thread = uuid4()
    await _turn(pool, thread, uuid4(), uuid4())
    config = alembic_config(migrated_ai_db)
    command.downgrade(config, "base")
    remaining = sql(
        migrated_ai_db,
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'agent'",
    )
    assert {str(r[0]) for r in remaining} == CHECKPOINTER_TABLES | {"alembic_version_ai"}
    reset_agent_schema(migrated_ai_db)
    assert sql(
        migrated_ai_db, "SELECT count(*) FROM agent.checkpoints WHERE thread_id = %s", str(thread)
    ) != [(0,)]


# --- persistence across restarts -------------------------------------------------------


async def test_r21_state_survives_a_new_runner_and_pool(migrated_ai_db: str) -> None:
    await setup_checkpointer(migrated_ai_db)
    thread, conversation, input_id = uuid4(), uuid4(), uuid4()
    first_pool = create_checkpointer_pool(migrated_ai_db)
    await first_pool.open()
    try:
        first = await _turn(first_pool, thread, conversation, input_id)
        before = await _runner(first_pool).conversation_state(str(thread))
    finally:
        await first_pool.close()  # "process restart": nothing in memory survives

    second_pool = create_checkpointer_pool(migrated_ai_db)
    await second_pool.open()
    try:
        llm = ScriptedLlm()
        runner = _runner(second_pool, llm)
        after = await runner.conversation_state(str(thread))
        replay = await runner.run_turn(
            thread_id=str(thread),
            tool_context=_context(conversation),
            turn=TurnInput(input_id, InputKind.USER_MESSAGE, text="אילו רכבים מתאימים לי?"),
        )
    finally:
        await second_pool.close()
    assert after["displayed_options"] == before["displayed_options"] != []
    assert input_id.__str__() in after["processed_input_ids"]
    assert replay.status is TurnStatus.DUPLICATE
    assert replay.messages == () and replay.message_refs == first.message_refs != ()
    assert replay.llm_calls == 0 and replay.tool_calls == 0
    stored = _checkpoint_text(migrated_ai_db, thread)  # neither run keeps the turn's text
    assert "אילו רכבים" not in stored and "₪" not in stored


async def test_thread_id_not_conversation_id_keys_the_checkpoint(
    pool: Pool, migrated_ai_db: str
) -> None:
    thread, conversation = uuid4(), uuid4()
    outcome = await _turn(pool, thread, conversation, uuid4())
    assert outcome.checkpoint_id is not None
    count = "SELECT count(*) FROM agent.checkpoints WHERE thread_id = %s"
    assert sql(migrated_ai_db, count, str(thread)) != [(0,)]
    assert sql(migrated_ai_db, count, str(conversation)) == [(0,)]


async def test_checkpoint_holds_no_secrets_or_tool_context(pool: Pool, migrated_ai_db: str) -> None:
    thread, conversation = uuid4(), uuid4()
    await _turn(pool, thread, conversation, uuid4())
    stored = _checkpoint_text(migrated_ai_db, thread)
    assert stored  # the scan really read the rows
    for forbidden in (MARKER, str(conversation), "strauss_dev_only", "postgresql", "AIza"):
        assert forbidden not in stored


async def test_checkpoint_holds_no_salary_amounts(pool: Pool, migrated_ai_db: str) -> None:
    llm = ScriptedLlm()
    llm.interpret(interpretation(Intent.COMPARE_TO_CURRENT, all_options=True))
    thread = uuid4()
    outcome = await _turn(pool, thread, uuid4(), uuid4(), llm=llm)
    assert "11,693" in " ".join(m.text for m in outcome.messages)  # the turn showed amounts
    stored = _checkpoint_text(migrated_ai_db, thread)
    # formatted amounts, the shekel sign and the word "net": no response text at all
    for fragment in ("11,693", "11693", "11,032", "+661", "₪", "נטו"):
        assert fragment not in stored


# --- fencing ---------------------------------------------------------------------------


async def test_r18_a_stale_claim_cannot_write_a_checkpoint(
    pool: Pool, persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _active_conversation(persistence)
    claim = await _claim_one(persistence, conversation.conversation_id)
    sql(
        migrated_ai_db,
        "UPDATE agent.inputs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
        claim.input.input_id,
    )
    async with persistence.transaction() as store:
        [expired] = [
            e for e in await store.list_expired_claims() if e.input_id == claim.input.input_id
        ]
        assert await store.take_over_expired_claim(expired, "new", lease_seconds=LEASE)
    with pytest.raises(StaleClaimError):
        await _turn(
            pool,
            conversation.thread_id,
            conversation.conversation_id,
            claim.input.input_id,
            fence=_fence(persistence, claim),
            stager=SqlResponseStager(persistence, claim),
        )
    lineage = PostgresCheckpointLineage(FencedPostgresSaver(pool))
    observed = await lineage.observe(str(conversation.thread_id), str(claim.input.input_id))
    assert observed.latest_checkpoint_id is None  # nothing was written


async def test_thread_deletion_is_refused(pool: Pool) -> None:
    with pytest.raises(PermissionError):
        await FencedPostgresSaver(pool).adelete_thread(str(uuid4()))


# --- lineage and recovery --------------------------------------------------------------


async def test_fresh_run_completes_with_verified_lineage(
    pool: Pool, persistence: AgentPersistence
) -> None:
    conversation = await _active_conversation(persistence)
    claim = await _claim_one(persistence, conversation.conversation_id)
    lineage = PostgresCheckpointLineage(FencedPostgresSaver(pool))
    thread, input_id = str(conversation.thread_id), str(claim.input.input_id)
    async with persistence.transaction() as store:
        observed = await lineage.observe(thread, input_id)
        assert await store.bind_lineage(claim, observed) is LineageResult.FRESH
    outcome = await _turn(
        pool,
        conversation.thread_id,
        conversation.conversation_id,
        claim.input.input_id,
        fence=_fence(persistence, claim),
        stager=SqlResponseStager(persistence, claim),
    )
    assert outcome.checkpoint_id is not None
    after = await lineage.observe(thread, input_id)
    assert after.latest_checkpoint_id == outcome.checkpoint_id and after.latest_contains_input
    async with persistence.transaction() as store:
        status = await store.complete_input(
            claim,
            checkpoint_id=outcome.checkpoint_id,
            parent_checkpoint_id=outcome.parent_checkpoint_id,
            message_refs=outcome.message_refs,
        )
    assert status == "completed"


async def test_r23_recovery_after_checkpoint_completion_needs_no_rerun(
    pool: Pool, persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _active_conversation(persistence)
    claim = await _claim_one(persistence, conversation.conversation_id)
    lineage = PostgresCheckpointLineage(FencedPostgresSaver(pool))
    thread, input_id = str(conversation.thread_id), str(claim.input.input_id)
    async with persistence.transaction() as store:
        await store.bind_lineage(claim, await lineage.observe(thread, input_id))
    first = await _turn(
        pool,
        conversation.thread_id,
        conversation.conversation_id,
        claim.input.input_id,
        fence=_fence(persistence, claim),
        stager=SqlResponseStager(persistence, claim),
    )
    # crash: the checkpoint is durable, the input is still `processing`, nothing was published
    sql(
        migrated_ai_db,
        "UPDATE agent.inputs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
        claim.input.input_id,
    )
    async with persistence.transaction() as store:
        [expired] = [
            e for e in await store.list_expired_claims() if e.input_id == claim.input.input_id
        ]
        recovered = await store.take_over_expired_claim(expired, "recovery", lease_seconds=LEASE)
    assert recovered is not None
    observed = await lineage.observe(thread, input_id)
    refs = await lineage.latest_message_refs(thread, input_id)
    assert refs is not None and refs == list(first.message_refs)
    async with persistence.transaction() as store:
        assert await store.bind_lineage(recovered, observed) is LineageResult.ALREADY_EXECUTED
        assert observed.latest_checkpoint_id is not None
        status = await store.complete_input(
            recovered,
            checkpoint_id=observed.latest_checkpoint_id,
            parent_checkpoint_id=observed.latest_parent_checkpoint_id,
            message_refs=refs,
        )
    assert status == "completed"
    history = sql(
        migrated_ai_db,
        "SELECT id FROM agent.messages WHERE conversation_id = %s ORDER BY sequence",
        conversation.conversation_id,
    )
    assert [r[0] for r in history] == [m.message_id for m in first.messages]


async def test_r17_foreign_checkpoint_blocks_the_next_input(
    pool: Pool, persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _active_conversation(persistence)
    claim = await _claim_one(persistence, conversation.conversation_id)
    lineage = PostgresCheckpointLineage(FencedPostgresSaver(pool))
    thread = str(conversation.thread_id)
    async with persistence.transaction() as store:
        await store.bind_lineage(claim, await lineage.observe(thread, str(claim.input.input_id)))
    outcome = await _turn(
        pool,
        conversation.thread_id,
        conversation.conversation_id,
        claim.input.input_id,
        fence=_fence(persistence, claim),
        stager=SqlResponseStager(persistence, claim),
    )
    assert outcome.checkpoint_id is not None
    async with persistence.transaction() as store:
        await store.complete_input(
            claim,
            checkpoint_id=outcome.checkpoint_id,
            parent_checkpoint_id=outcome.parent_checkpoint_id,
            message_refs=outcome.message_refs,
        )
    # a stale writer (no fence) adds a checkpoint after the completion
    await _turn(pool, conversation.thread_id, conversation.conversation_id, uuid4())
    nxt = await _claim_one(persistence, conversation.conversation_id)
    assert nxt.expected_base_checkpoint_id == outcome.checkpoint_id
    async with persistence.transaction() as store:
        observed = await lineage.observe(thread, str(nxt.input.input_id))
        assert await store.bind_lineage(nxt, observed) is LineageResult.MISMATCH
    reason = sql(
        migrated_ai_db,
        "SELECT status, reconciliation_reason FROM agent.inputs WHERE id = %s",
        nxt.input.input_id,
    )
    assert reason == [("needs_reconciliation", "checkpoint_lineage_mismatch")]
