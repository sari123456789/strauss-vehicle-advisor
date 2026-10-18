"""`SqlUnitOfWork` against real PostgreSQL: commit/rollback, single use, no AI input queue and
the idle-in-transaction timeout (decision Q10); no AI input queue (C2).
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.business.application.ports.auth import UserAccount
from app.business.infrastructure.database.engine import (
    DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_MS,
    create_runtime_engine,
)
from app.contracts.tool_context import Role
from tests.integration.business.database.case_helpers import persistence
from tests.integration.business.database.db_helpers import run


def _account() -> UserAccount:
    user_id = uuid4()
    return UserAccount(user_id, f"u{user_id.hex[:16]}", frozenset({Role.EMPLOYEE}))


def test_commit_persists_and_missing_commit_rolls_back(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            kept, dropped, failed = _account(), _account(), _account()
            async with p.uow() as uow:
                await uow.users.add(kept)
                await uow.commit()
            async with p.uow() as uow:
                await uow.users.add(dropped)
            with pytest.raises(RuntimeError, match="boom"):
                async with p.uow() as uow:
                    await uow.users.add(failed)
                    raise RuntimeError("boom")
            async with p.uow() as uow:
                assert await uow.users.get(kept.user_id) == kept
                assert await uow.users.get(dropped.user_id) is None
                assert await uow.users.get(failed.user_id) is None

    run(scenario())


def test_unit_of_work_is_single_use(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            uow = p.uow()
            async with uow:
                await uow.commit()
                with pytest.raises(RuntimeError):
                    await uow.commit()
            with pytest.raises(RuntimeError):
                async with uow:
                    pass

    run(scenario())


def test_business_unit_of_work_never_carries_ai_inputs(migrated_db: str) -> None:
    """C2 / D1: AI inputs are never written from a Business transaction."""

    async def scenario() -> None:
        async with persistence(migrated_db) as p, p.uow() as uow:
            assert not hasattr(uow, "input_queue")

    run(scenario())


def test_runtime_engine_sets_the_postgresql_idle_timeout(migrated_db: str) -> None:
    async def scenario() -> None:
        engine = create_runtime_engine(migrated_db)
        try:
            async with engine.connect() as connection:
                value = await connection.scalar(text("SHOW idle_in_transaction_session_timeout"))
                assert value == f"{DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_MS // 1000}s"
                assert await connection.scalar(text("SHOW statement_timeout")) == "0"
        finally:
            await engine.dispose()

    run(scenario())


def test_idle_open_transaction_is_terminated_and_nothing_is_committed(migrated_db: str) -> None:
    account = _account()

    async def scenario() -> None:
        async with persistence(migrated_db, idle_ms=500) as p:
            with pytest.raises((OperationalError, DBAPIError)):
                async with p.uow() as uow:
                    await uow.users.add(account)
                    await asyncio.sleep(1.5)  # e.g. an LLM call inside the transaction
                    await uow.commit()
            async with p.uow() as uow:
                assert await uow.users.get(account.user_id) is None

    run(scenario())


def test_active_statements_are_not_interrupted_by_the_idle_timeout(migrated_db: str) -> None:
    account = _account()

    async def scenario() -> None:
        async with persistence(migrated_db, idle_ms=500) as p:
            async with p.uow() as uow:
                await uow.users.add(account)
                await uow._session.execute(text("SELECT pg_sleep(1.5)"))
                await uow.commit()
            async with p.uow() as uow:
                assert await uow.users.get(account.user_id) == account

    run(scenario())
