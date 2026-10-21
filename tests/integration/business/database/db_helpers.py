"""Helpers for real-PostgreSQL tests. Destructive operations accept only `_test` databases."""

import os
from collections.abc import Coroutine
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.runtime import run_async
from app.settings import get_settings
from tests.paths import REPO_ROOT

BACKEND = REPO_ROOT / "src" / "backend"
DEFAULT_TEST_URL = "postgresql+psycopg://strauss:strauss_dev_only@localhost:55432/strauss_test"
REQUIRE_DB = os.environ.get("STRAUSS_REQUIRE_DB") == "1"
TABLES = (
    "rules_snapshots",
    "catalogue_versions",
    "vehicles",
    "occupation_policies",
    "tax_dataset_versions",
    "value_of_use",
    "reference_releases",
)
"""Immutable reference-data tables (migration 0001)."""
CASE_TABLES = (
    "employee_profiles",
    "cases",
    "alternatives",
    "operations",
    "option_result_sets",
    "comparison_runs",
    "representative_requests",
    "representative_answers",
    "request_assignments",
    "case_representative_facts",
    "integration_outbox",
    "audit_entries",
)
"""Business case tables (migrations 0002 and 0004, schema `business`); `auth` is separate."""


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return run_async(coroutine)


def configured_test_url() -> str:
    url = os.environ.get("STRAUSS_TEST_DATABASE_URL", DEFAULT_TEST_URL)
    require_test_database(url)
    return url


def require_test_database(url: str) -> None:
    name = make_url(url).database or ""
    dev_name = make_url(get_settings().database_url.get_secret_value()).database
    if not name.endswith("_test") or name == dev_name:
        pytest.fail(f"refusing to run DB tests against {name!r}: the name must end in '_test'")


def libpq_url(url: str, database: str | None = None) -> str:
    parsed = make_url(url).set(drivername="postgresql")
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.attributes["database_url"] = url
    return config


def reset_schema(url: str) -> None:
    """Downgrade to base and upgrade to head - only ever on a `_test` database."""
    require_test_database(url)
    config = alembic_config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def sql(url: str, statement: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        cursor = conn.execute(statement, params or None)
        return cursor.fetchall() if cursor.description else []


def table_counts(url: str) -> dict[str, int]:
    return {t: sql(url, f"SELECT count(*) FROM business.{t}")[0][0] for t in TABLES}
