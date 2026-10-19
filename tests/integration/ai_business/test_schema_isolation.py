"""Schema ownership and migration isolation for the shared database (Stage A).

The regular integration database is only upgraded. Downgrades run exclusively on a temporary,
uniquely named database that this test creates, marks, verifies and drops.
"""

from uuid import uuid4

import pytest

from app.ai.application.ports.conversation_store import NewConversation
from app.ai.infrastructure.persistence.engine import (
    create_ai_engine,
    create_ai_session_factory,
)
from app.ai.infrastructure.persistence.store import AgentPersistence
from app.runtime import run_async
from tests.integration.ai_business import db_guard
from tests.integration.ai_business.db_guard import UnsafeDatabaseError, snapshot, sql
from tests.integration.business.database.case_helpers import new_user, persistence, profile


def _schemas(url: str) -> set[str]:
    return {
        row[0]
        for row in sql(
            url,
            "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)",
            list(db_guard.OWNED_SCHEMAS),
        )
    }


def _tables(url: str, schema: str) -> set[str]:
    return {
        row[0]
        for row in sql(
            url,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            schema,
        )
    }


# --- The regular integration database --------------------------------------------------------


def test_one_database_holds_the_three_owned_schemas(integration_db: str) -> None:
    assert _schemas(integration_db) == {"business", "auth", "agent"}
    assert "alembic_version" in _tables(integration_db, "public")  # Business environment
    assert "alembic_version_ai" in _tables(integration_db, "agent")  # AI environment
    assert {"checkpoints", "checkpoint_blobs", "checkpoint_writes"} <= _tables(
        integration_db, "agent"
    )
    leaked = sql(
        integration_db,
        "SELECT table_schema FROM information_schema.tables "
        "WHERE table_name IN ('checkpoints', 'conversations', 'messages') "
        "AND table_schema <> 'agent'",
    )
    assert leaked == []
    db_guard.assert_marked(integration_db)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://strauss:x@localhost:55432/strauss",
        "postgresql+psycopg://strauss:x@localhost:55432/strauss_test",
        "postgresql+psycopg://strauss:x@localhost:55432/strauss_ai_test",
        "postgresql+psycopg://strauss:x@localhost:55432/postgres",
        "postgresql+psycopg://strauss:x@db.example.com:5432/strauss_integration_test",
        "postgresql+psycopg://strauss:x@localhost:55432/anything_test",
        "postgresql://strauss:x@localhost:55432/strauss_integration_test",
    ],
)
def test_the_guard_rejects_unapproved_targets(url: str) -> None:
    with pytest.raises(UnsafeDatabaseError):
        db_guard.check_target(url)


def test_the_guard_rejects_an_existing_unmarked_database(integration_db: str) -> None:
    unmarked = integration_db.rsplit("/", 1)[0] + "/strauss_ai_test"
    with pytest.raises(UnsafeDatabaseError):
        db_guard.assert_marked(unmarked)  # rejected by name before any connection


def test_destructive_operations_refuse_the_regular_integration_database(
    integration_db: str,
) -> None:
    for destructive in (db_guard.downgrade_business, db_guard.downgrade_ai):
        with pytest.raises(UnsafeDatabaseError):
            destructive(integration_db)
    assert _schemas(integration_db) == {"business", "auth", "agent"}


# --- Migration isolation on a temporary database ---------------------------------------------


async def _seed_agent(url: str) -> None:
    engine = create_ai_engine(url)
    try:
        agent = AgentPersistence(create_ai_session_factory(engine))
        new = NewConversation(uuid4(), uuid4(), uuid4(), "2026-10", uuid4())
        async with agent.transaction() as store:
            await store.create_conversation(new)
            await store.bind_case(new.conversation_id, uuid4())
    finally:
        await engine.dispose()


async def _seed_business(url: str) -> None:
    async with persistence(url) as p:
        subject = await new_user(p)
        async with p.uow() as uow:
            await uow.profiles.add(profile(subject))
            await uow.commit()


def test_migrations_preserve_the_other_modules_data(integration_db: str) -> None:
    url = db_guard.create_migration_database(integration_db)
    try:
        db_guard.upgrade_to_head(url, migration=True)
        run_async(_seed_agent(url))
        run_async(_seed_business(url))

        # Business migrations never touch agent-owned tables or data.
        agent_before = snapshot(url, ("agent",))
        assert any(count for count, _ in agent_before.values())
        db_guard.downgrade_business(url)
        assert "business" not in _schemas(url) and "auth" not in _schemas(url)
        assert snapshot(url, ("agent",)) == agent_before
        db_guard.upgrade_business(url)
        assert snapshot(url, ("agent",)) == agent_before

        # AI migrations never touch business- or auth-owned tables or data.
        run_async(_seed_business(url))
        business_before = snapshot(url, ("business", "auth"))
        assert any(count for count, _ in business_before.values())
        db_guard.downgrade_ai(url)
        assert not (_tables(url, "agent") - {"alembic_version_ai"})
        assert snapshot(url, ("business", "auth")) == business_before
        db_guard.upgrade_ai(url)
        assert snapshot(url, ("business", "auth")) == business_before
    finally:
        db_guard.drop_migration_database(url)
    assert not sql(
        integration_db,
        "SELECT 1 FROM pg_database WHERE datname = %s",
        url.rsplit("/", 1)[1],
    )
