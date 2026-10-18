"""AI Alembic environment: own version table, `agent` only, reversible, matches the ORM."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, create_engine

from app.ai.infrastructure.persistence.models import (
    CHECKPOINTER_TABLES,
    SCHEMA,
    AgentBase,
    include_agent_object,
)
from tests.integration.ai.database.conftest import alembic_config, reset_agent_schema, sql

AGENT_TABLES = {
    "conversations",
    "inputs",
    "messages",
    "events",
    "input_side_effects",
    "response_stagings",
    "response_staging_messages",
    "alembic_version_ai",
}


def _tables(url: str, schema: str) -> set[str]:
    rows = sql(
        url, "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", schema
    )
    # library-owned checkpointer tables persist across AI migrations by design (stage 2)
    return {str(r[0]) for r in rows} - CHECKPOINTER_TABLES


def test_upgrade_creates_agent_tables_and_own_version_table(migrated_ai_db: str) -> None:
    assert _tables(migrated_ai_db, SCHEMA) == AGENT_TABLES
    version = sql(migrated_ai_db, "SELECT version_num FROM agent.alembic_version_ai")
    assert version == [("0003_response_staging",)]


def test_ai_migrations_do_not_touch_other_schemas(migrated_ai_db: str) -> None:
    assert _tables(migrated_ai_db, "public") == set()
    assert _tables(migrated_ai_db, "business") == set()


def test_database_encoding_is_utf8(migrated_ai_db: str) -> None:
    rows = sql(
        migrated_ai_db,
        "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = current_database()",
    )
    assert rows == [("UTF8",)]


def test_no_foreign_key_leaves_the_agent_schema(migrated_ai_db: str) -> None:
    rows = sql(
        migrated_ai_db,
        """
        SELECT c.conname, n2.nspname
        FROM pg_constraint c
        JOIN pg_namespace n1 ON n1.oid = c.connamespace
        JOIN pg_class t2 ON t2.oid = c.confrelid
        JOIN pg_namespace n2 ON n2.oid = t2.relnamespace
        WHERE c.contype = 'f' AND n1.nspname = 'agent'
        """,
    )
    assert rows and {r[1] for r in rows} == {SCHEMA}


def test_orm_metadata_matches_the_migration(migrated_ai_db: str) -> None:
    engine = create_engine(migrated_ai_db)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_schemas": True,
                    "include_name": lambda name, type_, _p: type_ != "schema" or name == SCHEMA,
                    "version_table": "alembic_version_ai",
                    "version_table_schema": SCHEMA,
                    "include_object": include_agent_object,
                    "compare_type": True,
                },
            )
            diffs = compare_metadata(context, AgentBase.metadata)
    finally:
        engine.dispose()
    assert diffs == []


def test_downgrade_and_upgrade_round_trip(migrated_ai_db: str) -> None:
    config = alembic_config(migrated_ai_db)
    command.downgrade(config, "base")
    assert _tables(migrated_ai_db, SCHEMA) == {"alembic_version_ai"}
    command.upgrade(config, "head")
    assert _tables(migrated_ai_db, SCHEMA) == AGENT_TABLES
    reset_agent_schema(migrated_ai_db)


def test_check_constraint_names_match_the_orm(migrated_ai_db: str) -> None:
    """Alembic autogenerate does not compare CHECK constraints, so compare names directly."""
    rows = sql(
        migrated_ai_db,
        "SELECT conrelid::regclass::text, conname FROM pg_constraint "
        "WHERE contype = 'c' AND connamespace = 'agent'::regnamespace",
    )
    database = {(str(t).removeprefix("agent."), str(n)) for t, n in rows}
    orm = {
        (table.name, str(constraint.name))
        for table in AgentBase.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert database == orm
