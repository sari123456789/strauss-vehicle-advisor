"""Migration 0003: response staging - idempotent, all-or-nothing, verified before publishing."""

import asyncio
from uuid import UUID, uuid4

import psycopg
import pytest

from app.ai.application.ports.conversation_store import (
    InputClaim,
    LineageObservation,
    LineageResult,
    NewConversation,
    NewInput,
    StagingConflictError,
    StaleClaimError,
)
from app.ai.application.ports.response_staging import MessageRef, StagedMessage
from app.ai.infrastructure.persistence.staging import SqlResponseStager
from app.ai.infrastructure.persistence.store import AgentPersistence
from tests.integration.ai.database.conftest import libpq_url, sql

LEASE = 30
FRESH = LineageObservation(None, None, latest_contains_input=False)


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


async def _claimed(persistence: AgentPersistence) -> InputClaim:
    new = NewConversation(uuid4(), uuid4(), uuid4(), "2026-01", uuid4())
    async with persistence.transaction() as store:
        await store.create_conversation(new)
        await store.bind_case(new.conversation_id, uuid4())
        await store.enqueue_input(
            NewInput(uuid4(), new.conversation_id, "user", uuid4(), {"text": "שלום"})
        )
    async with persistence.transaction() as store:
        claim = await store.claim_next_input("w", lease_seconds=LEASE)
    assert claim is not None and claim.input.conversation_id == new.conversation_id
    return claim


def _response(input_id: UUID, *texts: str) -> list[StagedMessage]:
    return [
        StagedMessage(str(uuid4()), "comparison", text, {"runId": str(input_id)}) for text in texts
    ]


async def _finish(persistence: AgentPersistence, claim: InputClaim, refs: list[MessageRef]) -> str:
    async with persistence.transaction() as store:
        return await store.complete_input(
            claim, checkpoint_id=f"cp-{uuid4()}", parent_checkpoint_id=None, message_refs=refs
        )


def _published(url: str, conversation_id: UUID) -> list[tuple[str, str]]:
    rows = sql(
        url,
        "SELECT id::text, text FROM agent.messages WHERE conversation_id = %s ORDER BY sequence",
        conversation_id,
    )
    return [(str(a), str(b)) for a, b in rows]


async def test_staged_response_is_published_with_completion(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    claim = await _claimed(persistence)
    response = _response(claim.input.input_id, "נטו משוער 11,693 ₪", "חלופות שלא חושבו: —")
    await SqlResponseStager(persistence, claim).stage(str(claim.input.input_id), response)
    async with persistence.transaction() as store:
        await store.bind_lineage(claim, FRESH)
    assert await _finish(persistence, claim, [m.ref for m in response]) == "completed"
    assert _published(migrated_ai_db, claim.input.conversation_id) == [
        (m.message_id, m.text) for m in response
    ]
    kept = sql(
        migrated_ai_db,
        "SELECT count(*) FROM agent.response_staging_messages WHERE input_id = %s",
        claim.input.input_id,
    )
    assert kept == [(2,)]  # no automatic cleanup (retention requires approval)


async def test_staging_is_idempotent_per_claim(persistence: AgentPersistence) -> None:
    claim = await _claimed(persistence)
    response = _response(claim.input.input_id, "א")
    async with persistence.transaction() as store:
        assert await store.stage_response(claim, response) is True
    async with persistence.transaction() as store:
        assert await store.stage_response(claim, response) is False
    with pytest.raises(StagingConflictError):
        async with persistence.transaction() as store:
            await store.stage_response(claim, _response(claim.input.input_id, "ב"))


async def test_concurrent_staging_of_one_claim_writes_once(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    claim = await _claimed(persistence)
    response = _response(claim.input.input_id, "א", "ב")

    async def stage() -> bool:
        async with persistence.transaction() as store:
            return await store.stage_response(claim, response)

    results = await asyncio.gather(*(stage() for _ in range(5)))
    assert sorted(results) == [False, False, False, False, True]
    count = sql(
        migrated_ai_db,
        "SELECT count(*) FROM agent.response_staging_messages WHERE input_id = %s",
        claim.input.input_id,
    )
    assert count == [(2,)]


async def test_stale_or_expired_claims_cannot_stage(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    claim = await _claimed(persistence)
    sql(
        migrated_ai_db,
        "UPDATE agent.inputs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
        claim.input.input_id,
    )
    with pytest.raises(StaleClaimError):
        await SqlResponseStager(persistence, claim).stage(
            str(claim.input.input_id), _response(claim.input.input_id, "א")
        )
    with pytest.raises(StaleClaimError):  # a stager is bound to its own input
        await SqlResponseStager(persistence, claim).stage(str(uuid4()), [])


@pytest.mark.parametrize(
    "payload",
    [{"netAmount": "11693"}, {"runId": {"net": "1"}}, {"code": 1.5}, {"fields": [1, 2]}],
    ids=["unknown-key", "nested-object", "float", "non-string-list"],
)
async def test_payloads_hold_identifiers_only(
    persistence: AgentPersistence, migrated_ai_db: str, payload: dict[str, object]
) -> None:
    claim = await _claimed(persistence)
    with pytest.raises(ValueError):
        async with persistence.transaction() as store:
            await store.stage_response(claim, [StagedMessage(str(uuid4()), "x", "y", payload)])
    count = sql(
        migrated_ai_db,
        "SELECT count(*) FROM agent.response_stagings WHERE input_id = %s",
        claim.input.input_id,
    )
    assert count == [(0,)]


async def test_partial_staging_cannot_be_committed(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    claim = await _claimed(persistence)
    header = (
        "INSERT INTO agent.response_stagings "
        "(input_id, claim_token, attempt, message_count, manifest_sha256) "
        "VALUES (%s, %s, 1, %s, repeat('a', 64))"
    )
    message = (
        "INSERT INTO agent.response_staging_messages (input_id, claim_token, position, "
        "message_id, role, kind, text, payload, content_sha256) "
        "VALUES (%s, %s, %s, gen_random_uuid(), 'assistant', 'text', 'x', '{}', repeat('b', 64))"
    )
    with psycopg.connect(libpq_url(migrated_ai_db)) as conn:  # one transaction
        conn.execute(header, (claim.input.input_id, claim.claim_token, 2))
        conn.execute(message, (claim.input.input_id, claim.claim_token, 0))
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            conn.commit()  # 1 of 2 messages: the deferred trigger rejects the commit
    other = uuid4()
    with psycopg.connect(libpq_url(migrated_ai_db)) as conn:
        conn.execute(header, (claim.input.input_id, other, 1))
        conn.execute(message, (claim.input.input_id, other, 5))
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            conn.commit()  # right count, wrong position
    rows = sql(
        migrated_ai_db,
        "SELECT count(*) FROM agent.response_stagings WHERE input_id = %s",
        claim.input.input_id,
    )
    assert rows == [(0,)]


async def test_staging_is_append_only(persistence: AgentPersistence, migrated_ai_db: str) -> None:
    claim = await _claimed(persistence)
    async with persistence.transaction() as store:
        await store.stage_response(claim, _response(claim.input.input_id, "א"))
    for statement in (
        "UPDATE agent.response_staging_messages SET text = 'ב' WHERE input_id = %s",
        "DELETE FROM agent.response_staging_messages WHERE input_id = %s",
        "UPDATE agent.response_stagings SET message_count = 0 WHERE input_id = %s",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation):
            sql(migrated_ai_db, statement, claim.input.input_id)


@pytest.mark.parametrize("tamper", ["hash", "missing", "extra", "order"])
async def test_references_that_do_not_match_staging_need_reconciliation(
    persistence: AgentPersistence, migrated_ai_db: str, tamper: str
) -> None:
    claim = await _claimed(persistence)
    response = _response(claim.input.input_id, "א", "ב")
    async with persistence.transaction() as store:
        await store.bind_lineage(claim, FRESH)
        await store.stage_response(claim, response)
    refs = [m.ref for m in response]
    if tamper == "hash":
        refs[0] = MessageRef(refs[0].message_id, "0" * 64)
    elif tamper == "missing":
        refs = refs[:1]
    elif tamper == "extra":
        refs.append(MessageRef(str(uuid4()), "1" * 64))
    else:
        refs.reverse()
    assert await _finish(persistence, claim, refs) == "needs_reconciliation"
    assert _published(migrated_ai_db, claim.input.conversation_id) == []
    reason = sql(
        migrated_ai_db,
        "SELECT reconciliation_reason FROM agent.inputs WHERE id = %s",
        claim.input.input_id,
    )
    assert reason == [("staged_response_missing_or_mismatched",)]


async def test_completion_without_any_staging_needs_reconciliation(
    persistence: AgentPersistence,
) -> None:
    claim = await _claimed(persistence)
    async with persistence.transaction() as store:
        await store.bind_lineage(claim, FRESH)
    assert await _finish(persistence, claim, []) == "needs_reconciliation"


async def test_the_staging_matching_the_checkpoint_is_published(
    persistence: AgentPersistence, migrated_ai_db: str
) -> None:
    """Attempt 1 staged A and checkpointed it, then crashed; attempt 2 must publish A."""
    first = await _claimed(persistence)
    message_id = str(uuid4())
    a = [StagedMessage(message_id, "text", "תשובה א")]
    b = [StagedMessage(message_id, "text", "תשובה ב")]  # same ID, different content
    async with persistence.transaction() as store:
        await store.stage_response(first, a)
    sql(
        migrated_ai_db,
        "UPDATE agent.inputs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
        first.input.input_id,
    )
    async with persistence.transaction() as store:
        [expired] = [
            e for e in await store.list_expired_claims() if e.input_id == first.input.input_id
        ]
        second = await store.take_over_expired_claim(expired, "recovery", lease_seconds=LEASE)
    assert second is not None
    async with persistence.transaction() as store:
        await store.stage_response(second, b)  # a later attempt staged something else
        observed = LineageObservation("cp-a", None, latest_contains_input=True)
        assert await store.bind_lineage(second, observed) is LineageResult.ALREADY_EXECUTED
        status = await store.complete_input(
            second, checkpoint_id="cp-a", parent_checkpoint_id=None, message_refs=[a[0].ref]
        )
    assert status == "completed"
    assert _published(migrated_ai_db, first.input.conversation_id) == [(message_id, "תשובה א")]
