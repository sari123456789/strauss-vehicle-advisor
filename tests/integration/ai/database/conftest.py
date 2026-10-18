"""Real-PostgreSQL fixtures for AI persistence tests (marker `db`).

Safety: destructive steps (Alembic downgrade/upgrade) run only against a database whose name
ends in `_test` (default `strauss_ai_test`, override `STRAUSS_AI_TEST_DATABASE_URL`), and
never against the configured AI runtime database. The AI migrations touch only `agent`.
Unreachable PostgreSQL -> skip with a reason, unless STRAUSS_REQUIRE_DB=1 (then fail).
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Callable, Mapping

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.ai.infrastructure.persistence.engine import (
    create_ai_engine,
    create_ai_session_factory,
)
from app.ai.infrastructure.persistence.settings import load_database_settings
from app.ai.infrastructure.persistence.store import AgentPersistence
from tests.paths import REPO_ROOT

BACKEND = REPO_ROOT / "src" / "backend"
DEFAULT_TEST_URL = "postgresql+psycopg://strauss:strauss_dev_only@localhost:55432/strauss_ai_test"
REQUIRE_DB = os.environ.get("STRAUSS_REQUIRE_DB") == "1"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.path.parts and "ai" in item.path.parts:
            item.add_marker(pytest.mark.db)


def require_test_database(url: str) -> None:
    name = make_url(url).database or ""
    runtime = make_url(load_database_settings().database_url.get_secret_value()).database
    if not name.endswith("_test") or name == runtime:
        pytest.fail(f"refusing to run AI DB tests against {name!r}: the name must end in '_test'")


def libpq_url(url: str, database: str | None = None) -> str:
    parsed = make_url(url).set(drivername="postgresql")
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


def alembic_config(url: str) -> Config:
    require_test_database(url)
    config = Config(str(BACKEND / "alembic_ai.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations_ai"))
    config.attributes["database_url"] = url
    return config


def reset_agent_schema(url: str) -> None:
    """Downgrade the AI migrations to base and upgrade to head - only on a `_test` database."""
    config = alembic_config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def sql(url: str, statement: str, *params: object) -> list[tuple[object, ...]]:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        cursor = conn.execute(statement, params or None)
        return cursor.fetchall() if cursor.description else []


@pytest.fixture(scope="session")
def ai_test_database_url() -> str:
    url = os.environ.get("STRAUSS_AI_TEST_DATABASE_URL", DEFAULT_TEST_URL)
    require_test_database(url)
    name = make_url(url).database
    try:
        with psycopg.connect(
            libpq_url(url, "postgres"), autocommit=True, connect_timeout=5
        ) as conn:
            found = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            if found.fetchone() is None:
                conn.execute(f"CREATE DATABASE \"{name}\" ENCODING 'UTF8' TEMPLATE template0")
    except psycopg.OperationalError as error:
        message = f"PostgreSQL is unreachable for AI DB tests: {error}".strip()
        if REQUIRE_DB:
            pytest.fail(message)
        pytest.skip(message)
    return url


@pytest.fixture(scope="module")
def migrated_ai_db(ai_test_database_url: str) -> str:
    reset_agent_schema(ai_test_database_url)
    return ai_test_database_url


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]] | None:
    # psycopg async cannot run on Windows' default Proactor loop.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return None


@pytest.fixture
async def persistence(migrated_ai_db: str) -> AsyncIterator[AgentPersistence]:
    engine = create_ai_engine(migrated_ai_db, idle_in_transaction_timeout_ms=15_000)
    try:
        yield AgentPersistence(create_ai_session_factory(engine))
    finally:
        await engine.dispose()
