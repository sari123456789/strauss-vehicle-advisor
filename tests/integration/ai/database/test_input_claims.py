"""P2c stage 1: input claims, fencing, leases, transitions, retry/dead/reconciliation, journal.

Time is controlled by moving `lease_expires_at` / `next_attempt_at` in SQL (allowed by the
transition trigger: they are not status changes), never by sleeping.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from app.ai.application.ports.conversation_store import (
    InputClaim,
    LineageObservation,
    LineageResult,
    NewConversation,
    NewEvent,
    NewInput,
    NewMessage,
    NewSideEffectEvent,
    SideEffectConflictError,
    SideEffectPhase,
    StaleClaimError,
)
from app.ai.application.ports.response_staging import StagedMessage
from app.ai.infrastructure.persistence.store import AgentPersistence, AgentStore, payload_sha256
from tests.integration.ai.database.conftest import sql

LEASE = 30
SAFE = frozenset({"update_conversation_inputs"})
FRESH = LineageObservation(None, None, latest_contains_input=False)


async def _conversation(persistence: AgentPersistence, *, active: bool = True) -> UUID:
    new = NewConversation(uuid4(), uuid4(), uuid4(), "2026-01", uuid4())
    async with persistence.transaction() as store:
        await store.create_conversation(new)
        if active:
            await store.bind_case(new.conversation_id, uuid4())
    return new.conversation_id


async def _enqueue(persistence: AgentPersistence, conversation_id: UUID, n: int = 1) -> list[UUID]:
    ids = []
    async with persistence.transaction() as store:
        for i in range(n):
            entry = NewInput(uuid4(), conversation_id, "user", uuid4(), {"text": f"הודעה {i}"})
            ids.append((await store.enqueue_input(entry)).record.input_id)
    return ids


async def _claim(persistence: AgentPersistence, worker: str = "w1") -> InputClaim | None:
    async with persistence.transaction() as store:
        return await store.claim_next_input(worker, lease_seconds=LEASE)


async def _complete(
    persistence: AgentPersistence,
    claim: InputClaim,
    checkpoint: str,
    *,
    staged: tuple[StagedMessage, ...] = (),
) -> str:
    async with persistence.transaction() as store:
        observation = LineageObservation(claim.expected_base_checkpoint_id, None, False)
        assert await store.bind_lineage(claim, observation) is LineageResult.FRESH
        await store.stage_response(claim, staged)
        return await store.complete_input(
            claim,
            checkpoint_id=checkpoint,
            parent_checkpoint_id=claim.expected_base_checkpoint_id,
            message_refs=[m.ref for m in staged],
        )


async def _fail(persistence: AgentPersistence, claim: InputClaim, **kwargs: object) -> str:
    async with persistence.transaction() as store:
        decision = await store.fail_input(
            claim,
            error_code="INFRA_UNAVAILABLE",
            replay_safe_tools=SAFE,
            **kwargs,  # type: ignore[arg-type]
        )
    return decision.status


def _staged(message_id: UUID | str, text: str = "x") -> StagedMessage:
    return StagedMessage(str(message_id), "text", text)


def _status(url: str, input_id: UUID) -> str:
    return str(sql(url, "SELECT status FROM agent.inputs WHERE id = %s", input_id)[0][0])


def _expire_lease(url: str, input_id: UUID) -> None:
    sql(
        url,
        "UPDATE agent.inputs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
        input_id,
    )


def _make_due(url: str, input_id: UUID) -> None:
    sql(url, "UPDATE agent.inputs SET next_attempt_at = now() WHERE id = %s", input_id)


def _started(operation: str, tool: str, request: str = "r") -> NewSideEffectEvent:
    return NewSideEffectEvent(
        uuid4(), operation, tool, SideEffectPhase.STARTED, payload_sha256({"r": request})
    )


@pytest.fixture(autouse=True)
def _isolated_queue(migrated_ai_db: str) -> None:
    """Claims pick any due input in the database: park inputs left by earlier tests."""
    sql(
        migrated_ai_db,
        "UPDATE agent.inputs SET next_attempt_at = now() + interval '1 day' WHERE status = 'retry'",
    )
    rows = sql(migrated_ai_db, "SELECT id FROM agent.inputs WHERE status = 'pending'")
    for (input_id,) in rows:
        sql(
            migrated_ai_db,
            "UPDATE agent.inputs SET status = 'processing', claim_token = gen_random_uuid(), "
            "claimed_by = 'parked', claimed_at = now(), "
            "lease_expires_at = now() + interval '1 day' WHERE id = %s",
            input_id,
        )


# --- claiming --------------------------------------------------------------------------


async def test_nothing_to_claim(persistence: AgentPersistence) -> None:
    assert await _claim(persistence) is None


async def test_initializing_conversations_are_not_claimed(persistence: AgentPersistence) -> None:
    conversation = await _conversation(persistence, active=False)
    await _enqueue(persistence, conversation)
    assert await _claim(persistence) is None


async def test_claim_sets_fenced_ownership(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    [input_id] = await _enqueue(persistence, conversation)
    claim = await _claim(persistence, "worker-a")
    assert claim is not None and claim.input.input_id == input_id
    assert claim.attempt == 1 and claim.claimed_by == "worker-a"
    assert claim.expected_base_checkpoint_id is None
    row = sql(
        migrated_ai_db,
        "SELECT status, claim_token, lease_expires_at > now() FROM agent.inputs WHERE id = %s",
        input_id,
    )
    assert row == [("processing", claim.claim_token, True)]


async def test_r1_concurrent_claims_on_one_input_yield_one_owner(
    persistence: AgentPersistence,
) -> None:
    conversation = await _conversation(persistence)
    await _enqueue(persistence, conversation)
    claims = await asyncio.gather(*(_claim(persistence, f"w{i}") for i in range(10)))
    owners = [c for c in claims if c is not None]
    assert len(owners) == 1


async def test_r2_inputs_of_one_conversation_are_claimed_in_order(
    persistence: AgentPersistence,
) -> None:
    conversation = await _conversation(persistence)
    first_id, second_id = await _enqueue(persistence, conversation, 2)
    first = await _claim(persistence)
    assert first is not None and first.input.input_id == first_id
    assert await _claim(persistence) is None  # the second waits behind `processing`
    assert await _complete(persistence, first, "cp-1") == "completed"
    second = await _claim(persistence)
    assert second is not None and second.input.input_id == second_id
    assert second.expected_base_checkpoint_id == "cp-1"


async def test_r3_parallel_workers_process_every_input_once_in_order(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversations = [await _conversation(persistence) for _ in range(5)]
    for conversation in conversations:
        await _enqueue(persistence, conversation, 3)
    completed: list[tuple[UUID, int]] = []

    async def worker(name: str) -> None:
        while (claim := await _claim(persistence, name)) is not None:
            await _complete(persistence, claim, f"cp-{claim.input.input_id}")
            completed.append((claim.input.conversation_id, claim.input.sequence))

    await asyncio.gather(*(worker(f"w{i}") for i in range(6)))
    assert len(completed) == 15 and len(set(completed)) == 15
    for conversation in conversations:
        order = [seq for conv, seq in completed if conv == conversation]
        assert order == [1, 2, 3]


# --- fencing, leases and takeover ------------------------------------------------------


async def test_r4_stale_worker_cannot_commit_after_takeover(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    [input_id] = await _enqueue(persistence, conversation)
    old = await _claim(persistence, "old")
    assert old is not None
    _expire_lease(migrated_ai_db, input_id)
    async with persistence.transaction() as store:
        [expired] = [e for e in await store.list_expired_claims() if e.input_id == input_id]
        new = await store.take_over_expired_claim(expired, "new", lease_seconds=LEASE)
    assert new is not None and new.attempt == 2 and new.claim_token != old.claim_token
    message = NewMessage(uuid4(), conversation, role="assistant", kind="text", text="ישן")
    stale_writes: list[Callable[[AgentStore], Awaitable[object]]] = [
        lambda s: s.bind_lineage(old, FRESH),
        lambda s: s.complete_input(
            old, checkpoint_id="x", parent_checkpoint_id=None, message_refs=()
        ),
        lambda s: s.stage_response(old, [_staged(message.message_id)]),
        lambda s: s.fail_input(old, error_code="E", replay_safe_tools=SAFE),
        lambda s: s.mark_needs_reconciliation(old, reason="r"),
        lambda s: s.renew_lease(old, lease_seconds=LEASE),
        lambda s: s.record_side_effect(old, _started("op", "update_conversation_inputs")),
    ]
    for stale_write in stale_writes:
        with pytest.raises(StaleClaimError):
            async with persistence.transaction() as store:
                await stale_write(store)
    assert sql(
        migrated_ai_db, "SELECT count(*) FROM agent.messages WHERE id = %s", message.message_id
    ) == [(0,)]
    row = sql(migrated_ai_db, "SELECT claimed_by, status FROM agent.inputs WHERE id = %s", input_id)
    assert row == [("new", "processing")]


async def test_r6_live_lease_cannot_be_taken_over(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    [input_id] = await _enqueue(persistence, conversation)
    claim = await _claim(persistence)
    assert claim is not None
    async with persistence.transaction() as store:
        assert all(e.input_id != input_id for e in await store.list_expired_claims())
    _expire_lease(migrated_ai_db, input_id)
    async with persistence.transaction() as store:
        [expired] = [e for e in await store.list_expired_claims() if e.input_id == input_id]
    sql(
        migrated_ai_db,
        "UPDATE agent.inputs SET lease_expires_at = now() + interval '1 minute' WHERE id = %s",
        input_id,
    )
    async with persistence.transaction() as store:  # lease is live again: CAS refuses
        assert await store.take_over_expired_claim(expired, "x", lease_seconds=LEASE) is None


async def test_concurrent_takeovers_have_one_winner(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    [input_id] = await _enqueue(persistence, conversation)
    assert await _claim(persistence) is not None
    _expire_lease(migrated_ai_db, input_id)
    async with persistence.transaction() as store:
        [expired] = [e for e in await store.list_expired_claims() if e.input_id == input_id]

    async def take(name: str) -> InputClaim | None:
        async with persistence.transaction() as store:
            return await store.take_over_expired_claim(expired, name, lease_seconds=LEASE)

    winners = [c for c in await asyncio.gather(*(take(f"t{i}") for i in range(5))) if c]
    assert len(winners) == 1


async def test_r7_heartbeat_renews_only_a_live_owned_lease(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    [input_id] = await _enqueue(persistence, conversation)
    claim = await _claim(persistence)
    assert claim is not None
    async with persistence.transaction() as store:
        renewed = await store.renew_lease(claim, lease_seconds=120)
    assert renewed - claim.lease_expires_at > timedelta(seconds=60)
    _expire_lease(migrated_ai_db, input_id)
    with pytest.raises(StaleClaimError):  # an expired lease is not resurrected
        async with persistence.transaction() as store:
            await store.renew_lease(claim, lease_seconds=LEASE)
    with pytest.raises(ValueError):
        async with persistence.transaction() as store:
            await store.renew_lease(claim, lease_seconds=0)


# --- transitions -----------------------------------------------------------------------


async def test_r8_transition_table_is_enforced_by_the_database(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    pending_id, other_id = await _enqueue(persistence, conversation, 2)
    with pytest.raises(psycopg.errors.RestrictViolation):
        sql(
            migrated_ai_db,
            "UPDATE agent.inputs SET status = 'retry', next_attempt_at = now() WHERE id = %s",
            pending_id,
        )
    with pytest.raises(psycopg.errors.CheckViolation):  # processing without a claim
        sql(
            migrated_ai_db,
            "UPDATE agent.inputs SET status = 'processing' WHERE id = %s",
            pending_id,
        )
    claim = await _claim(persistence)
    assert claim is not None and claim.input.input_id == pending_id
    with pytest.raises(psycopg.errors.CheckViolation):  # completed needs a checked lineage
        sql(
            migrated_ai_db,
            "UPDATE agent.inputs SET status = 'completed', completed_at = now() WHERE id = %s",
            pending_id,
        )
    with pytest.raises(psycopg.errors.CheckViolation):  # reconciliation needs a reason
        sql(
            migrated_ai_db,
            "UPDATE agent.inputs SET status = 'needs_reconciliation' WHERE id = %s",
            pending_id,
        )
    assert await _complete(persistence, claim, "cp-a") == "completed"
    for statement in (
        "UPDATE agent.inputs SET status = 'retry', next_attempt_at = now() WHERE id = %s",
        "UPDATE agent.inputs SET last_error_code = 'X' WHERE id = %s",
        "UPDATE agent.inputs SET status = 'processing' WHERE id = %s",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation):  # completed is final
            sql(migrated_ai_db, statement, pending_id)
    second = await _claim(persistence)
    assert second is not None and second.input.input_id == other_id
    async with persistence.transaction() as store:
        await store.mark_needs_reconciliation(second, reason="operator_test")
    # needs_reconciliation -> retry is an allowed (manual) transition
    sql(
        migrated_ai_db,
        "UPDATE agent.inputs SET status = 'retry', next_attempt_at = now() WHERE id = %s",
        other_id,
    )
    assert _status(migrated_ai_db, other_id) == "retry"


async def test_r9_input_payload_and_identity_are_immutable(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    [input_id] = await _enqueue(persistence, conversation)
    for statement in (
        'UPDATE agent.inputs SET payload = \'{"text": "x"}\' WHERE id = %s',
        "UPDATE agent.inputs SET payload_sha256 = repeat('0', 64) WHERE id = %s",
        "UPDATE agent.inputs SET sequence = 99 WHERE id = %s",
        "UPDATE agent.inputs SET attempts = -1 WHERE id = %s",
    ):
        with pytest.raises((psycopg.errors.RestrictViolation, psycopg.errors.CheckViolation)):
            sql(migrated_ai_db, statement, input_id)


async def test_r22_failure_inside_the_completion_transaction_writes_nothing(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    [input_id] = await _enqueue(persistence, conversation)
    claim = await _claim(persistence)
    assert claim is not None
    message = _staged(uuid4(), "תשובה")
    with pytest.raises(RuntimeError):
        async with persistence.transaction() as store:
            await store.bind_lineage(claim, FRESH)
            await store.stage_response(claim, [message])
            await store.complete_input(
                claim,
                checkpoint_id="cp",
                parent_checkpoint_id=None,
                message_refs=[message.ref],
                events=(NewEvent(uuid4(), conversation, "turn.completed"),),
            )
            raise RuntimeError("crash before commit")
    assert _status(migrated_ai_db, input_id) == "processing"
    assert sql(
        migrated_ai_db,
        "SELECT count(*) FROM agent.messages WHERE conversation_id = %s",
        conversation,
    ) == [(0,)]
    assert sql(
        migrated_ai_db, "SELECT count(*) FROM agent.response_stagings WHERE input_id = %s", input_id
    ) == [(0,)]  # the staging rolled back with it
    assert await _complete(persistence, claim, "cp", staged=(message,)) == "completed"


# --- retry, dead, reconciliation -------------------------------------------------------


async def test_r10_retry_backoff_then_dead_without_side_effects(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    first_id, second_id = await _enqueue(persistence, conversation, 2)
    claim = await _claim(persistence)
    assert claim is not None
    assert await _fail(persistence, claim) == "retry"
    delay = sql(
        migrated_ai_db,
        "SELECT extract(epoch FROM next_attempt_at - updated_at)::int "
        "FROM agent.inputs WHERE id = %s",
        first_id,
    )
    assert delay == [(5,)]
    assert await _claim(persistence) is None  # not due yet, and it still blocks the second
    for attempt in range(2, 11):
        _make_due(migrated_ai_db, first_id)
        claim = await _claim(persistence)
        assert claim is not None and claim.input.input_id == first_id and claim.attempt == attempt
        expected = "dead" if attempt == 10 else "retry"
        assert await _fail(persistence, claim) == expected
    nxt = await _claim(persistence)
    assert nxt is not None and nxt.input.input_id == second_id  # dead does not block


async def test_r11_replay_safe_side_effects_retry(persistence: AgentPersistence) -> None:
    conversation = await _conversation(persistence)
    await _enqueue(persistence, conversation)
    claim = await _claim(persistence)
    assert claim is not None
    async with persistence.transaction() as store:
        await store.record_side_effect(claim, _started("update:1", "update_conversation_inputs"))
    assert await _fail(persistence, claim) == "retry"


async def test_r11_unverified_side_effect_needs_reconciliation_and_blocks_only_its_conversation(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation, other = await _conversation(persistence), await _conversation(persistence)
    first_id, _ = await _enqueue(persistence, conversation, 2)
    claim = await _claim(persistence)
    assert claim is not None and claim.input.input_id == first_id
    async with persistence.transaction() as store:
        await store.record_side_effect(claim, _started("compare:1", "compare_scenarios"))
    assert await _fail(persistence, claim) == "needs_reconciliation"
    reason = sql(
        migrated_ai_db, "SELECT reconciliation_reason FROM agent.inputs WHERE id = %s", first_id
    )
    assert reason == [("unverified_side_effect",)]
    assert await _claim(persistence) is None  # the second input of that conversation waits
    [other_id] = await _enqueue(persistence, other)
    nxt = await _claim(persistence)
    assert nxt is not None and nxt.input.input_id == other_id


async def test_r11_exhausted_attempts_after_side_effects_need_reconciliation(
    persistence: AgentPersistence,
) -> None:
    conversation = await _conversation(persistence)
    await _enqueue(persistence, conversation)
    claim = await _claim(persistence)
    assert claim is not None
    async with persistence.transaction() as store:
        await store.record_side_effect(claim, _started("update:1", "update_conversation_inputs"))
    assert await _fail(persistence, claim, max_attempts=1) == "needs_reconciliation"


# --- checkpoint lineage ----------------------------------------------------------------


async def test_completion_without_checked_lineage_needs_reconciliation(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    [input_id] = await _enqueue(persistence, conversation)
    claim = await _claim(persistence)
    assert claim is not None
    staged = _staged(uuid4())
    async with persistence.transaction() as store:
        await store.stage_response(claim, [staged])
        status = await store.complete_input(
            claim, checkpoint_id="cp", parent_checkpoint_id=None, message_refs=[staged.ref]
        )
    message = NewMessage(UUID(staged.message_id), conversation, "assistant", "text", "x")
    assert status == "needs_reconciliation"
    assert sql(
        migrated_ai_db, "SELECT count(*) FROM agent.messages WHERE id = %s", message.message_id
    ) == [(0,)]
    assert sql(
        migrated_ai_db, "SELECT reconciliation_reason FROM agent.inputs WHERE id = %s", input_id
    ) == [("checkpoint_lineage_unverified",)]


@pytest.mark.parametrize(
    "observation",
    [
        LineageObservation("foreign", None, latest_contains_input=False),
        LineageObservation("foreign", "cp-1", latest_contains_input=False),
        LineageObservation("child", "other-parent", latest_contains_input=True),
    ],
    ids=["unknown-latest", "child-without-input", "wrong-parent"],
)
async def test_unverifiable_lineage_needs_reconciliation(
    persistence: AgentPersistence, observation: LineageObservation
) -> None:
    conversation = await _conversation(persistence)
    await _enqueue(persistence, conversation, 2)
    first = await _claim(persistence)
    assert first is not None
    await _complete(persistence, first, "cp-1")
    second = await _claim(persistence)
    assert second is not None
    async with persistence.transaction() as store:
        assert await store.bind_lineage(second, observation) is LineageResult.MISMATCH
    with pytest.raises(StaleClaimError):  # it is no longer processing
        async with persistence.transaction() as store:
            await store.complete_input(
                second, checkpoint_id="x", parent_checkpoint_id="cp-1", message_refs=()
            )


async def test_already_executed_lineage_completes_without_rerun(
    persistence: AgentPersistence,
) -> None:
    conversation = await _conversation(persistence)
    await _enqueue(persistence, conversation, 2)
    first = await _claim(persistence)
    assert first is not None
    await _complete(persistence, first, "cp-1")
    second = await _claim(persistence)
    assert second is not None
    observation = LineageObservation("cp-2", "cp-1", latest_contains_input=True)
    async with persistence.transaction() as store:
        assert await store.bind_lineage(second, observation) is LineageResult.ALREADY_EXECUTED
        await store.stage_response(second, [])
        status = await store.complete_input(
            second, checkpoint_id="cp-2", parent_checkpoint_id="cp-1", message_refs=()
        )
    assert status == "completed"


async def test_parent_mismatch_at_completion_needs_reconciliation(
    persistence: AgentPersistence,
) -> None:
    conversation = await _conversation(persistence)
    await _enqueue(persistence, conversation)
    claim = await _claim(persistence)
    assert claim is not None
    async with persistence.transaction() as store:
        await store.bind_lineage(claim, FRESH)
        status = await store.complete_input(
            claim, checkpoint_id="cp", parent_checkpoint_id="someone-else", message_refs=()
        )
    assert status == "needs_reconciliation"


# --- side-effect journal ---------------------------------------------------------------


async def test_journal_records_phases_per_attempt(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    [input_id] = await _enqueue(persistence, conversation)
    claim = await _claim(persistence)
    assert claim is not None
    started = _started("update:1", "update_conversation_inputs")
    confirmed = NewSideEffectEvent(
        uuid4(),
        "update:1",
        "update_conversation_inputs",
        SideEffectPhase.CONFIRMED,
        started.request_sha256,
        outcome_code="ok",
    )
    async with persistence.transaction() as store:
        assert (await store.record_side_effect(claim, started)).created
        assert not (await store.record_side_effect(claim, started)).created  # replay no-op
        await store.record_side_effect(claim, confirmed)
    with pytest.raises(SideEffectConflictError):  # a second outcome in the same attempt
        async with persistence.transaction() as store:
            await store.record_side_effect(
                claim,
                NewSideEffectEvent(
                    uuid4(),
                    "update:1",
                    "update_conversation_inputs",
                    SideEffectPhase.FAILED,
                    started.request_sha256,
                ),
            )
    with pytest.raises(SideEffectConflictError):  # outcome without started
        async with persistence.transaction() as store:
            await store.record_side_effect(
                claim,
                NewSideEffectEvent(
                    uuid4(),
                    "update:2",
                    "update_conversation_inputs",
                    SideEffectPhase.CONFIRMED,
                    started.request_sha256,
                ),
            )
    with pytest.raises(SideEffectConflictError):  # same operation, different request
        async with persistence.transaction() as store:
            await store.record_side_effect(
                claim, _started("update:1", "update_conversation_inputs", "other")
            )
    with pytest.raises(SideEffectConflictError):  # event ID reused with different content
        async with persistence.transaction() as store:
            await store.record_side_effect(
                claim,
                NewSideEffectEvent(
                    started.event_id,
                    "update:9",
                    "x",
                    SideEffectPhase.STARTED,
                    started.request_sha256,
                ),
            )
    async with persistence.transaction() as store:
        events = await store.list_side_effects(input_id)
    assert [(e.operation_key, e.phase, e.attempt) for e in events] == [
        ("update:1", "started", 1),
        ("update:1", "confirmed", 1),
    ]
    with pytest.raises(psycopg.errors.RestrictViolation):
        sql(
            migrated_ai_db,
            "UPDATE agent.input_side_effects SET outcome_code = 'x' WHERE input_id = %s",
            input_id,
        )
    with pytest.raises(psycopg.errors.RestrictViolation):
        sql(migrated_ai_db, "DELETE FROM agent.input_side_effects WHERE input_id = %s", input_id)


async def test_journal_keeps_the_operation_across_attempts(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    [input_id] = await _enqueue(persistence, conversation)
    first = await _claim(persistence)
    assert first is not None
    async with persistence.transaction() as store:
        await store.record_side_effect(first, _started("update:1", "update_conversation_inputs"))
    assert await _fail(persistence, first) == "retry"
    _make_due(migrated_ai_db, input_id)
    second = await _claim(persistence)
    assert second is not None and second.attempt == 2
    async with persistence.transaction() as store:
        await store.record_side_effect(second, _started("update:1", "update_conversation_inputs"))
        events = await store.list_side_effects(input_id)
    assert [(e.attempt, e.claim_token) for e in events] == [
        (1, first.claim_token),
        (2, second.claim_token),
    ]


async def test_no_new_side_effect_after_the_lease_expired(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    conversation = await _conversation(persistence)
    [input_id] = await _enqueue(persistence, conversation)
    claim = await _claim(persistence)
    assert claim is not None
    _expire_lease(migrated_ai_db, input_id)
    with pytest.raises(StaleClaimError):
        async with persistence.transaction() as store:
            await store.record_side_effect(
                claim, _started("update:1", "update_conversation_inputs")
            )
