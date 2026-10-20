"""M1: the real shared application (`app.main.create_app`) with its lifespan (marker `db`).

Unlike the other suites, nothing is assembled by hand here: the lifespan builds the Business
services, `BusinessApi`, the AI runtime and (when enabled) the in-process worker exactly as in
production. Only the model is scripted and the databases are temporary, marked `_test`
databases created through `db_guard` (never the development database or Neon).
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.ai.infrastructure.checkpoints.postgres import setup_checkpointer
from app.bootstrap import SchemaNotReadyError, build_import_components, build_seed_components
from app.business.application.identity.credential_admin import CredentialAdminService
from app.business.infrastructure.clock import SystemClock
from app.business.infrastructure.security.password_hasher import ScryptPasswordHasher
from app.contracts.tool_context import ActorRef, Origin, Role
from app.main import create_app
from app.runtime import run_async
from app.settings import Settings
from tests.fakes.ai.fake_llm import ScriptedLlm
from tests.integration.ai_business import db_guard
from tests.integration.ai_business.conftest import REFERENCE_OPERATOR, REQUIRE_DB
from tests.integration.business.database.case_helpers import persistence
from tests.paths import REPO_ROOT

PASSWORD = "correct horse battery"
MONTH = "2026-10"


def _fresh_database() -> str:
    try:
        return db_guard.create_migration_database(db_guard.configured_url())
    except Exception as error:  # noqa: BLE001 - unreachable server: same policy as other suites
        if REQUIRE_DB:
            pytest.fail(f"PostgreSQL is unreachable: {type(error).__name__}")
        pytest.skip(f"PostgreSQL is unreachable: {type(error).__name__}")


@pytest.fixture(scope="module")
def app_db() -> Iterator[str]:
    """Both schemas at head, checkpointer tables, the reference release and the demo seed."""
    url = _fresh_database()
    try:
        db_guard.upgrade_to_head(url, migration=True)
        run_async(setup_checkpointer(url))
        imports = build_import_components(url, REPO_ROOT)
        try:
            assert run_async(imports.service.run(REFERENCE_OPERATOR)).release is not None
        finally:
            run_async(imports.engine.dispose())
        seed = build_seed_components(url, REPO_ROOT)
        try:
            run_async(seed.service.seed(REFERENCE_OPERATOR, dry_run=False))
        finally:
            run_async(seed.engine.dispose())
        run_async(_set_password(url, "e001"))
        yield url
    finally:
        db_guard.drop_migration_database(url)


async def _set_password(url: str, login: str) -> None:
    async with persistence(url) as p:
        operator = ActorRef(
            subject_id=uuid4(), roles=frozenset({Role.IMPORT_OPERATOR}), origin=Origin.CLI
        )
        admin = CredentialAdminService(p.uow, ScryptPasswordHasher(n=2**10), SystemClock())
        await admin.set_password(operator, login, PASSWORD)


def _settings(url: str, **overrides: Any) -> Settings:
    return Settings(environment="test", database_url=url, migration_database_url=url, **overrides)


def _app(url: str, **overrides: Any) -> FastAPI:
    return create_app(_settings(url, **overrides), llm=ScriptedLlm(), ai_database_url=url)


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
            yield c


async def _login(client: httpx.AsyncClient, login: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"login": login, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


async def test_lifespan_serves_business_and_conversation_routes(app_db: str) -> None:
    app = _app(app_db)
    async with running(app) as client:
        ready = await client.get("/api/v1/health/ready")
        assert ready.status_code == 200, ready.text
        assert ready.json()["checks"] == {
            "businessDatabase": True,
            "referenceRelease": True,
            "agentDatabase": True,
            "worker": True,
        }
        headers = await _login(client, "e001")
        profile = await client.get("/api/v1/me/profile", headers=headers)
        assert profile.status_code == 200, profile.text
        started = await client.post(
            "/api/v1/conversations",
            json={"clientRequestId": str(uuid4()), "calculationMonth": MONTH},
            headers=headers,
        )
        assert started.status_code == 200, started.text
        conversation = started.json()
        assert conversation["caseId"] is not None
        listed = await client.get("/api/v1/conversations", headers=headers)
        ids = [c["conversationId"] for c in listed.json()["conversations"]]
        assert conversation["conversationId"] in ids
        case = await client.get(f"/api/v1/cases/{conversation['caseId']}", headers=headers)
        assert case.status_code == 200, case.text


async def test_worker_runs_only_when_enabled_and_stops_on_shutdown(app_db: str) -> None:
    disabled = _app(app_db)
    async with running(disabled):
        assert disabled.state.ai_runtime.in_process.running is False

    enabled = _app(app_db, worker_in_process=True)
    async with running(enabled) as client:
        runtime = enabled.state.ai_runtime
        assert runtime.in_process.running is True
        assert (await client.get("/api/v1/health/ready")).status_code == 200
    assert runtime.in_process.running is False


async def test_consecutive_lifespans_start_and_stop_cleanly(app_db: str) -> None:
    for _ in range(2):
        app = _app(app_db, worker_in_process=True)
        async with running(app) as client:
            assert (await client.get("/api/v1/health/ready")).status_code == 200
        assert not hasattr(app.state, "business_api")
        assert not hasattr(app.state, "ai_conversations")


@pytest.fixture
def migrated_without_checkpointer() -> Iterator[str]:
    url = _fresh_database()
    try:
        db_guard.upgrade_to_head(url, migration=True)
        yield url
    finally:
        db_guard.drop_migration_database(url)


@pytest.fixture
def agent_only() -> Iterator[str]:
    url = _fresh_database()
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
        async with running(_app(url)):
            pass
    db_guard.downgrade_ai(url)  # synchronous Alembic environment
    with pytest.raises(SchemaNotReadyError, match="Agent schema at none"):
        async with running(_app(url)):
            pass


async def test_startup_fails_clearly_when_a_business_migration_is_missing(agent_only: str) -> None:
    with pytest.raises(SchemaNotReadyError, match="Business schema at none") as error:
        async with running(_app(agent_only)):
            pass
    assert "postgresql" not in str(error.value)
