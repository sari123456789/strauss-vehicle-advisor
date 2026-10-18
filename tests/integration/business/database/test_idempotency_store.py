"""Operation idempotency on real PostgreSQL (architecture §10, AC31)."""

import asyncio
from uuid import UUID, uuid4

import pytest

from app.business.application.idempotency import canonical_request_hash
from app.business.application.ports.repositories import (
    Claimed,
    OperationKey,
    OperationPayloadMismatchError,
    Replay,
    ScopeKind,
)
from tests.integration.business.database.case_helpers import (
    Persistence,
    new_owner_with_case,
    persistence,
)
from tests.integration.business.database.db_helpers import run, sql

HASH_A = canonical_request_hash({"value": "18500.00"})
HASH_B = canonical_request_hash({"value": "18600.00"})


def _key(actor: UUID, case_id: UUID) -> OperationKey:
    return OperationKey(actor, ScopeKind.CASE, case_id, "update_inputs", uuid4())


async def _setup(p: Persistence) -> OperationKey:
    owner, case = await new_owner_with_case(p)
    return _key(owner, case.case_id)


def test_claim_complete_replay_and_payload_mismatch(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            key = await _setup(p)
            async with p.uow() as uow:
                assert await uow.operations.claim(key, HASH_A) == Claimed(key)
                await uow.operations.complete(key, {"newCaseVersion": 4})
                await uow.commit()
            async with p.uow() as uow:
                replay = await uow.operations.claim(key, HASH_A)
                assert isinstance(replay, Replay) and replay.result == {"newCaseVersion": 4}
                with pytest.raises(OperationPayloadMismatchError):
                    await uow.operations.claim(key, HASH_B)

    run(scenario())
    assert sql(
        migrated_db, "SELECT count(*) FROM business.operations WHERE request_hash = %s", HASH_A
    ) == [(1,)]


def test_concurrent_claims_produce_exactly_one_execution(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            key = await _setup(p)

            async def first() -> object:
                async with p.uow() as uow:
                    claimed = await uow.operations.claim(key, HASH_A)
                    await asyncio.sleep(0.4)
                    await uow.operations.complete(key, {"executed": "first"})
                    await uow.commit()
                    return claimed

            async def second() -> object:
                await asyncio.sleep(0.1)
                async with p.uow() as uow:
                    return await uow.operations.claim(key, HASH_A)  # waits for `first`

            outcomes = await asyncio.gather(first(), second())
            assert outcomes[0] == Claimed(key)
            assert outcomes[1] == Replay(key, {"executed": "first"})

    run(scenario())


def test_rolled_back_claim_can_be_claimed_again(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            key = await _setup(p)

            async def first() -> None:
                async with p.uow() as uow:
                    await uow.operations.claim(key, HASH_A)
                    await asyncio.sleep(0.3)  # no commit: rolled back on exit

            async def second() -> object:
                await asyncio.sleep(0.1)
                async with p.uow() as uow:
                    result = await uow.operations.claim(key, HASH_A)
                    await uow.operations.complete(key, {"executed": "second"})
                    await uow.commit()
                    return result

            _, claimed = await asyncio.gather(first(), second())
            assert claimed == Claimed(key)

    run(scenario())


def test_complete_requires_an_open_claim(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            key = await _setup(p)
            async with p.uow() as uow:
                with pytest.raises(RuntimeError):
                    await uow.operations.complete(key, {"x": 1})

    run(scenario())
