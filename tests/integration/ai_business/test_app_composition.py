"""M1: the real shared application (`app.main.create_app`) with its lifespan (marker `db`).

Unlike the other suites, nothing is assembled by hand here: the lifespan builds the Business
services, `BusinessApi`, the AI runtime and (when enabled) the in-process worker exactly as in
production. Only the model is scripted and the databases are temporary, marked `_test`
databases created through `db_guard` (never the development database or Neon).
"""

from collections.abc import Iterator

import pytest

from app.ai.infrastructure.checkpoints.postgres import setup_checkpointer
from app.bootstrap import SchemaNotReadyError
from app.runtime import run_async
from tests.integration.ai_business import db_guard
from tests.integration.ai_business.app_support import (
    application,
    application_database,
    fresh_database,
    login,
    running,
    start_conversation,
)


@pytest.fixture(scope="module")
def app_db() -> Iterator[str]:
    with application_database() as url:
        yield url


async def test_lifespan_serves_business_and_conversation_routes(app_db: str) -> None:
    app = application(app_db)
    async with running(app) as client:
        ready = await client.get("/api/v1/health/ready")
        assert ready.status_code == 200, ready.text
        assert ready.json()["checks"] == {
            "businessDatabase": True,
            "referenceRelease": True,
            "agentDatabase": True,
            "worker": True,
        }
        headers = await login(client, "e001")
        profile = await client.get("/api/v1/me/profile", headers=headers)
        assert profile.status_code == 200, profile.text
        conversation = await start_conversation(client, headers)
        assert conversation["caseId"] is not None
        listed = await client.get("/api/v1/conversations", headers=headers)
        ids = [c["conversationId"] for c in listed.json()["conversations"]]
        assert conversation["conversationId"] in ids
        case = await client.get(f"/api/v1/cases/{conversation['caseId']}", headers=headers)
        assert case.status_code == 200, case.text


async def test_worker_runs_only_when_enabled_and_stops_on_shutdown(app_db: str) -> None:
    disabled = application(app_db)
    async with running(disabled):
        assert disabled.state.ai_runtime.in_process.running is False

    enabled = application(app_db, worker_in_process=True)
    async with running(enabled) as client:
        runtime = enabled.state.ai_runtime
        assert runtime.in_process.running is True
        assert (await client.get("/api/v1/health/ready")).status_code == 200
    assert runtime.in_process.running is False


async def test_consecutive_lifespans_start_and_stop_cleanly(app_db: str) -> None:
    for _ in range(2):
        app = application(app_db, worker_in_process=True)
        async with running(app) as client:
            assert (await client.get("/api/v1/health/ready")).status_code == 200
        assert not hasattr(app.state, "business_api")
        assert not hasattr(app.state, "ai_conversations")


@pytest.fixture
def migrated_without_checkpointer() -> Iterator[str]:
    url = fresh_database()
    try:
        db_guard.upgrade_to_head(url, migration=True)
        yield url
    finally:
        db_guard.drop_migration_database(url)


@pytest.fixture
def agent_only() -> Iterator[str]:
    url = fresh_database()
    try:
        db_guard.upgrade_ai(url)
        run_async(setup_checkpointer(url))
        yield url
    finally:
        db_guard.drop_migration_database(url)


async def test_startup_fails_clearly_when_the_agent_schema_is_not_ready(
    migrated_without_checkpointer: str,
) -> None:
    url = migrated_without_checkpointer
    with pytest.raises(SchemaNotReadyError, match="checkpointer"):
        async with running(application(url)):
            pass
    db_guard.downgrade_ai(url)  # synchronous Alembic environment
    with pytest.raises(SchemaNotReadyError, match="Agent schema at none"):
        async with running(application(url)):
            pass


async def test_startup_fails_clearly_when_a_business_migration_is_missing(agent_only: str) -> None:
    with pytest.raises(SchemaNotReadyError, match="Business schema at none") as error:
        async with running(application(agent_only)):
            pass
    assert "postgresql" not in str(error.value)
