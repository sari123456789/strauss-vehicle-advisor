"""Real AI <-> Business composition over ONE PostgreSQL database (marker `db`).

The integration database holds the `business`, `auth` and `agent` schemas, migrated by the two
independent Alembic environments. Ordinary tests only upgrade to head; destructive migration
checks use a separate temporary database (see `test_schema_isolation.py`). Every target is
verified by `db_guard` first. No source data is imported: these tests need no reference data.

Composition happens here, in test code only: the real `BusinessToolsFacade` and
`CaseLifecycleAdapter` come from the existing `build_business_services`, and the AI side gets
them only through the shared ports (`BusinessToolsPort`, `CaseLifecyclePort`).
"""

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg_pool import AsyncConnectionPool

from app.ai.application.conversation_service import ConversationService
from app.ai.infrastructure.checkpoints.postgres import (
    FencedPostgresSaver,
    create_checkpointer_pool,
    setup_checkpointer,
)
from app.ai.infrastructure.persistence.engine import (
    create_ai_engine,
    create_ai_session_factory,
)
from app.ai.infrastructure.persistence.store import AgentPersistence
from app.ai.workflow.runner import LangGraphTurnRunner
from app.bootstrap import (
    BusinessServices,
    build_business_services,
    build_import_components,
    build_seed_components,
)
from app.business.application.seeding.demo_seed_service import employee_subject_id
from app.contracts.business_port import BusinessToolsPort
from app.contracts.tool_context import ActorRef, Origin, Role, ToolContext
from app.runtime import run_async
from app.settings import Settings
from tests.fakes.ai.fake_llm import ScriptedLlm
from tests.integration.ai_business import db_guard
from tests.integration.business.database.case_helpers import new_user, persistence, profile
from tests.paths import REPO_ROOT

REQUIRE_DB = os.environ.get("STRAUSS_REQUIRE_DB") == "1"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "ai_business" in item.path.parts:
            item.add_marker(pytest.mark.db)


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]] | None:
    # psycopg async cannot run on Windows' default Proactor loop.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return None


@pytest.fixture(scope="session")
def integration_db() -> str:
    """The verified, marked integration database, upgraded to head (never reset)."""
    url = db_guard.configured_url()
    try:
        db_guard.ensure_integration_database(url)
    except psycopg.OperationalError as error:
        message = f"PostgreSQL is unreachable for integration tests: {error}".strip()
        if REQUIRE_DB:
            pytest.fail(message)
        pytest.skip(message)
    db_guard.upgrade_to_head(url)
    run_async(setup_checkpointer(url))
    return url


REFERENCE_OPERATOR = ActorRef(
    subject_id=uuid4(), roles=frozenset({Role.IMPORT_OPERATOR}), origin=Origin.CLI
)


@pytest.fixture(scope="module")
def reference_db(integration_db: str) -> str:
    """Verified reference release and the six seeded employees (idempotent, never a reset).

    The seeded E-001..E-006 profiles are canonical: tests read them but never update or
    confirm them; tests that write use `Stack.clone_reference_employee`.
    """
    db_guard.assert_marked(integration_db)
    imports = build_import_components(integration_db, REPO_ROOT)
    try:
        outcome = run_async(imports.service.run(REFERENCE_OPERATOR))
        assert outcome.release is not None
    finally:
        run_async(imports.engine.dispose())
    seed = build_seed_components(integration_db, REPO_ROOT)
    try:
        report = run_async(seed.service.seed(REFERENCE_OPERATOR, dry_run=False))
        assert len(report.profiles_created) + len(report.profiles_existing) == 6
    finally:
        run_async(seed.engine.dispose())
    return integration_db


def reference_subject(employee_ref: str) -> UUID:
    seed = json.loads((REPO_ROOT / "data" / "demo" / "demo-accounts.json").read_text("utf-8"))
    return employee_subject_id(UUID(seed["uuidNamespace"]), employee_ref)


class RecordingToolsPort:
    """Passes every call to the real facade and records the tool name, `ToolContext`, request
    and the Business result (the source of every amount the AI may show)."""

    def __init__(self, inner: BusinessToolsPort) -> None:
        self._inner = inner
        self.calls: list[tuple[str, ToolContext]] = []
        self.exchanges: list[tuple[str, Any, Any]] = []

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)

        async def call(ctx: ToolContext, *args: Any) -> Any:
            self.calls.append((name, ctx))
            result = await target(ctx, *args)
            self.exchanges.append((name, args[0] if args else None, result))
            return result

        return call

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def requests(self, name: str) -> list[Any]:
        return [request for tool, request, _ in self.exchanges if tool == name]

    def results(self, name: str) -> list[Any]:
        return [result for tool, _, result in self.exchanges if tool == name]


@dataclass
class Stack:
    url: str
    business: BusinessServices
    spy: RecordingToolsPort
    llm: ScriptedLlm
    runner: LangGraphTurnRunner
    conversations: ConversationService
    persistence: AgentPersistence

    async def new_actor(self, role: Role = Role.EMPLOYEE, *, with_profile: bool = True) -> ActorRef:
        """A Business user (and profile) created through Business persistence, not raw SQL."""
        async with persistence(self.url) as p:
            subject = await new_user(p, role)
            if with_profile:
                async with p.uow() as uow:
                    await uow.profiles.add(profile(subject))
                    await uow.commit()
        return ActorRef(subject_id=subject, roles=frozenset({role}), origin=Origin.HTTP)

    async def clone_reference_employee(self, employee_ref: str) -> ActorRef:
        """A new, isolated employee whose profile copies a seeded reference profile.

        The canonical profile is only read; the copy (new user, version 1) is the one that
        later confirmations or updates may change.
        """
        async with persistence(self.url) as p:
            async with p.uow() as uow:
                canonical = await uow.profiles.get(reference_subject(employee_ref))
            assert canonical is not None, f"{employee_ref} is not seeded"
            subject = await new_user(p)
            async with p.uow() as uow:
                await uow.profiles.add(
                    replace(canonical, employee_id=subject, version=1),
                    origin={"kind": "integration_test_clone", "of": employee_ref},
                )
                await uow.commit()
        return ActorRef(subject_id=subject, roles=frozenset({Role.EMPLOYEE}), origin=Origin.HTTP)


@pytest.fixture
async def stack(integration_db: str) -> AsyncIterator[Stack]:
    db_guard.assert_marked(integration_db)
    business = build_business_services(
        Settings(
            environment="test",
            database_url=integration_db,
            migration_database_url=integration_db,
        )
    )
    engine = create_ai_engine(integration_db, idle_in_transaction_timeout_ms=15_000)
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = create_checkpointer_pool(integration_db)
    await pool.open()
    try:
        agent = AgentPersistence(create_ai_session_factory(engine))
        spy = RecordingToolsPort(business.tools)
        llm = ScriptedLlm()
        yield Stack(
            url=integration_db,
            business=business,
            spy=spy,
            llm=llm,
            runner=LangGraphTurnRunner(
                cast(BusinessToolsPort, spy), llm, checkpointer=FencedPostgresSaver(pool)
            ),
            conversations=ConversationService(agent, business.lifecycle),
            persistence=agent,
        )
    finally:
        await pool.close()
        await engine.dispose()
        await business.engine.dispose()


def trace() -> str:
    return f"it-{uuid4().hex[:12]}"
