"""Safety guard for the shared AI-Business integration test database.

Every operation that creates, migrates, downgrades or drops a database goes through this
module. A target is accepted only when ALL of the following hold:

- it is a local `postgresql+psycopg` URL whose name is exactly the integration database
  (`strauss_integration_test`, or a `*_integration_test` name set explicitly) or a temporary
  migration-isolation database created by this run;
- the name is not any configured development, runtime, migration or existing test database;
- the configured environment is not production;
- after connecting, `current_database()` is the expected name and the database carries the
  positive ownership marker (`COMMENT ON DATABASE`) bound to this server's system identifier.

An existing database without the marker is never reset or adopted automatically: adoption
needs `STRAUSS_INTEGRATION_TEST_ADOPT=1` AND a database with no `business`, `auth` or `agent`
schema and no table in `public`. Anything else stops with `UnsafeDatabaseError`.
"""

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.ai.infrastructure.persistence.settings import load_database_settings
from app.settings import Settings
from tests.paths import REPO_ROOT

ENV_URL = "STRAUSS_INTEGRATION_TEST_DATABASE_URL"
ENV_ADOPT = "STRAUSS_INTEGRATION_TEST_ADOPT"
DEFAULT_NAME = "strauss_integration_test"
DEFAULT_URL = f"postgresql+psycopg://strauss:strauss_dev_only@localhost:55432/{DEFAULT_NAME}"
MARKER = "strauss-vehicle-advisor:ai-business-integration-test:v1"
MIGRATION_MARKER = MARKER + ":migration-isolation"
OWNED_SCHEMAS = ("business", "auth", "agent")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MAIN_NAME = re.compile(r"^[a-z][a-z0-9_]*_integration_test$")
MIGRATION_NAME = re.compile(r"^strauss_integration_migration_[0-9a-f]{12}_test$")
ALWAYS_PROTECTED = frozenset(
    {"postgres", "template0", "template1", "strauss", "strauss_test", "strauss_ai_test"}
)
BACKEND = REPO_ROOT / "src" / "backend"


class UnsafeDatabaseError(RuntimeError):
    """The target is not positively identified as an integration test database."""


def _name(url: str) -> str:
    return make_url(url).database or ""


def protected_names() -> frozenset[str]:
    """Development, runtime, migration and existing per-module test databases."""
    settings = Settings()
    ai = load_database_settings()
    urls = [
        settings.database_url.get_secret_value(),
        settings.migration_database_url.get_secret_value(),
        ai.database_url.get_secret_value(),
        ai.migration_database_url.get_secret_value(),
    ]
    urls += [
        os.environ[key]
        for key in ("STRAUSS_TEST_DATABASE_URL", "STRAUSS_AI_TEST_DATABASE_URL")
        if os.environ.get(key)
    ]
    return ALWAYS_PROTECTED | {_name(url) for url in urls}


def check_target(url: str, *, migration: bool = False) -> str:
    """Static checks before any connection. Returns the database name."""
    parsed = make_url(url)
    name = parsed.database or ""
    if parsed.drivername != "postgresql+psycopg":
        raise UnsafeDatabaseError("the integration database URL must use postgresql+psycopg")
    if parsed.host not in LOCAL_HOSTS:
        raise UnsafeDatabaseError("integration tests run only against a local PostgreSQL server")
    pattern = MIGRATION_NAME if migration else MAIN_NAME
    if not pattern.match(name):
        raise UnsafeDatabaseError(f"{name!r} is not an integration test database name")
    if name in protected_names():
        raise UnsafeDatabaseError(f"{name!r} is a protected development or test database")
    if Settings().environment.strip().lower() in {"production", "prod", "staging"}:
        raise UnsafeDatabaseError("integration tests never run in a production environment")
    return name


def configured_url() -> str:
    url = os.environ.get(ENV_URL, "").strip() or DEFAULT_URL
    check_target(url)
    return url


def libpq_url(url: str, database: str | None = None) -> str:
    parsed = make_url(url).set(drivername="postgresql")
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


@contextmanager
def _connect(url: str, database: str | None = None) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(libpq_url(url, database), autocommit=True, connect_timeout=5) as conn:
        yield conn


def _server_id(conn: psycopg.Connection[Any]) -> str:
    row = conn.execute("SELECT system_identifier FROM pg_control_system()").fetchone()
    if row is None:
        raise UnsafeDatabaseError("the server identity could not be read")
    return str(row[0])


def marker_for(server_id: str, *, migration: bool = False) -> str:
    return f"{MIGRATION_MARKER if migration else MARKER}:server={server_id}"


def _description(conn: psycopg.Connection[Any], name: str) -> tuple[bool, str | None]:
    row = conn.execute(
        "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s",
        (name,),
    ).fetchone()
    return (row is not None, row[0] if row else None)


def _is_empty(url: str) -> bool:
    with _connect(url) as conn:
        schemas = conn.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname = ANY(%s)", (list(OWNED_SCHEMAS),)
        ).fetchone()
        tables = conn.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
        ).fetchone()
    return bool(schemas and tables and schemas[0] == 0 and tables[0] == 0)


def assert_marked(url: str, *, migration: bool = False) -> None:
    """Connected identity check: the exact database, on this server, carrying our marker."""
    name = check_target(url, migration=migration)
    with _connect(url) as conn:
        current = conn.execute("SELECT current_database()").fetchone()
        if current is None or current[0] != name:
            raise UnsafeDatabaseError("the connection did not reach the expected database")
        _, description = _description(conn, name)
        if description != marker_for(_server_id(conn), migration=migration):
            raise UnsafeDatabaseError(f"{name!r} does not carry the integration test marker")


def ensure_integration_database(url: str) -> None:
    """Create and mark the database, or accept it only when it is already ours."""
    name = check_target(url)
    with _connect(url, "postgres") as admin:
        server = _server_id(admin)
        exists, description = _description(admin, name)
        if not exists:
            admin.execute(f"CREATE DATABASE \"{name}\" ENCODING 'UTF8' TEMPLATE template0")
            admin.execute(f"COMMENT ON DATABASE \"{name}\" IS '{marker_for(server)}'")
        elif description != marker_for(server):
            if os.environ.get(ENV_ADOPT) != "1":
                raise UnsafeDatabaseError(
                    f"{name!r} exists without the integration marker; it was not touched. "
                    f"Set {ENV_ADOPT}=1 only after confirming it is an empty test database."
                )
            if description is not None or not _is_empty(url):
                raise UnsafeDatabaseError(f"{name!r} is not empty or is marked for another use")
            admin.execute(f"COMMENT ON DATABASE \"{name}\" IS '{marker_for(server)}'")
    assert_marked(url)


def create_migration_database(base_url: str) -> str:
    """A new, uniquely named, marked database for destructive migration-isolation tests."""
    name = f"strauss_integration_migration_{uuid4().hex[:12]}_test"
    url = make_url(base_url).set(database=name).render_as_string(hide_password=False)
    check_target(url, migration=True)
    with _connect(url, "postgres") as admin:
        if _description(admin, name)[0]:
            raise UnsafeDatabaseError(f"{name!r} already exists; it was not touched")
        server = _server_id(admin)
        admin.execute(f"CREATE DATABASE \"{name}\" ENCODING 'UTF8' TEMPLATE template0")
        admin.execute(f"COMMENT ON DATABASE \"{name}\" IS '{marker_for(server, migration=True)}'")
    assert_marked(url, migration=True)
    return url


def drop_migration_database(url: str) -> None:
    assert_marked(url, migration=True)
    name = check_target(url, migration=True)
    with _connect(url, "postgres") as admin:
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def _business_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.attributes["database_url"] = url
    return config


def _ai_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic_ai.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations_ai"))
    config.attributes["database_url"] = url
    return config


def upgrade_to_head(url: str, *, migration: bool = False) -> None:
    """Both independent migration environments, upgrade only (never a reset)."""
    assert_marked(url, migration=migration)
    command.upgrade(_business_config(url), "head")
    command.upgrade(_ai_config(url), "head")


def downgrade_business(url: str) -> None:
    """Destructive: only on a temporary migration-isolation database."""
    assert_marked(url, migration=True)
    command.downgrade(_business_config(url), "base")


def upgrade_business(url: str) -> None:
    assert_marked(url, migration=True)
    command.upgrade(_business_config(url), "head")


def downgrade_ai(url: str) -> None:
    """Destructive: only on a temporary migration-isolation database."""
    assert_marked(url, migration=True)
    command.downgrade(_ai_config(url), "base")


def upgrade_ai(url: str) -> None:
    assert_marked(url, migration=True)
    command.upgrade(_ai_config(url), "head")


def sql(url: str, statement: str, *params: object) -> list[tuple[Any, ...]]:
    with _connect(url) as conn:
        cursor = conn.execute(statement, params or None)
        return cursor.fetchall() if cursor.description else []


def snapshot(url: str, schemas: tuple[str, ...]) -> dict[str, tuple[int, str]]:
    """Row count and content digest of every table in the given schemas."""
    tables = sql(
        url,
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema = ANY(%s) AND table_type = 'BASE TABLE' ORDER BY 1, 2",
        list(schemas),
    )
    result: dict[str, tuple[int, str]] = {}
    for schema, table in tables:
        row = sql(
            url,
            f"SELECT count(*), coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '') "
            f'FROM "{schema}"."{table}" t',
        )[0]
        result[f"{schema}.{table}"] = (int(row[0]), str(row[1]))
    return result
