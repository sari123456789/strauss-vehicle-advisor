"""Real-PostgreSQL fixtures for reference-data persistence tests (marker `db`).

Safety: tests run only against a separate database whose name ends in `_test` and differs from
the configured development database; migrations are downgraded only there.
Unreachable PostgreSQL -> skip with an explicit reason, unless STRAUSS_REQUIRE_DB=1 (then fail).
"""

from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy.engine import make_url

from app.bootstrap import ImportComponents, build_import_components
from tests.integration.database.db_helpers import (
    REQUIRE_DB,
    configured_test_url,
    libpq_url,
    reset_schema,
    run,
)
from tests.paths import REPO_ROOT


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.path.parts and "database" in item.path.parts:
            item.add_marker(pytest.mark.db)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = configured_test_url()
    name = make_url(url).database
    try:
        maintenance = libpq_url(url, "postgres")
        with psycopg.connect(maintenance, autocommit=True, connect_timeout=5) as conn:
            found = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            if found.fetchone() is None:
                conn.execute(f"CREATE DATABASE \"{name}\" ENCODING 'UTF8' TEMPLATE template0")
    except psycopg.OperationalError as error:
        message = f"PostgreSQL is unreachable for DB integration tests: {error}".strip()
        if REQUIRE_DB:
            pytest.fail(message)
        pytest.skip(message)
    return url


@pytest.fixture
def clean_database(test_database_url: str) -> str:
    reset_schema(test_database_url)
    return test_database_url


@pytest.fixture
def components(clean_database: str) -> Iterator[ImportComponents]:
    built = build_import_components(clean_database, REPO_ROOT)
    yield built
    run(built.engine.dispose())
