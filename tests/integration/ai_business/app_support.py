"""Shared support for tests over the real application (`app.main.create_app` + lifespan).

A temporary, marked `_test` database (via `db_guard`) gets both schemas, the checkpointer
tables, the verified reference release and the demo seed; demo logins get a known test
password. Only the model is scripted. Never the development database or Neon.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.ai.application.ports.llm import LlmPort
from app.ai.infrastructure.checkpoints.postgres import setup_checkpointer
from app.bootstrap import build_import_components, build_seed_components
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
DEMO_LOGINS = ("e001", "e002", "e003", "e005", "rep01")


def fresh_database() -> str:
    try:
        return db_guard.create_migration_database(db_guard.configured_url())
    except Exception as error:  # noqa: BLE001 - unreachable server: same policy as other suites
        if REQUIRE_DB:
            pytest.fail(f"PostgreSQL is unreachable: {type(error).__name__}")
        pytest.skip(f"PostgreSQL is unreachable: {type(error).__name__}")


async def set_password(url: str, login: str) -> None:
    async with persistence(url) as p:
        operator = ActorRef(
            subject_id=uuid4(), roles=frozenset({Role.IMPORT_OPERATOR}), origin=Origin.CLI
        )
        admin = CredentialAdminService(p.uow, ScryptPasswordHasher(n=2**10), SystemClock())
        await admin.set_password(operator, login, PASSWORD)


@contextmanager
def application_database() -> Iterator[str]:
    """Both schemas at head, checkpointer tables, reference release, demo seed and logins."""
    url = fresh_database()
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
        for login in DEMO_LOGINS:
            run_async(set_password(url, login))
        yield url
    finally:
        db_guard.drop_migration_database(url)


def settings(url: str, **overrides: Any) -> Settings:
    return Settings(environment="test", database_url=url, migration_database_url=url, **overrides)


def application(url: str, *, llm: LlmPort | None = None, **overrides: Any) -> FastAPI:
    model = llm if llm is not None else ScriptedLlm()
    return create_app(settings(url, **overrides), llm=model, ai_database_url=url)


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
            yield c


async def login(client: httpx.AsyncClient, name: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"login": name, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


async def start_conversation(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/conversations",
        json={"clientRequestId": str(uuid4()), "calculationMonth": MONTH},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def case_version(client: httpx.AsyncClient, headers: dict[str, str], case_id: str) -> int:
    response = await client.get(f"/api/v1/cases/{case_id}", headers=headers)
    assert response.status_code == 200, response.text
    return int(response.json()["data"]["caseVersion"])


def problem(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == code
    assert "Traceback" not in response.text
