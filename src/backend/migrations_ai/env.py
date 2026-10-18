"""Alembic environment for the AI-owned `agent` schema (synchronous psycopg 3).

Independent of the Business environment: its own version table `agent.alembic_version_ai`
and a schema filter that sees only `agent`. The URL comes from
`config.attributes["database_url"]` (tests) or `STRAUSS_AI_MIGRATION_DATABASE_URL`.
"""

from alembic import context
from sqlalchemy import create_engine, text

from app.ai.infrastructure.persistence.models import SCHEMA, AgentBase, include_agent_object
from app.ai.infrastructure.persistence.settings import load_database_settings

VERSION_TABLE = "alembic_version_ai"

config = context.config


def _url() -> str:
    url = config.attributes.get("database_url")
    if isinstance(url, str):
        return url
    return load_database_settings().migration_database_url.get_secret_value()


def _run_online() -> None:
    engine = create_engine(_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            # The version table lives in `agent`, so the schema must exist before Alembic
            # looks for it. Downgrades never drop the schema itself.
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
            connection.commit()
            context.configure(
                connection=connection,
                target_metadata=AgentBase.metadata,
                version_table=VERSION_TABLE,
                version_table_schema=SCHEMA,
                include_schemas=True,
                include_name=lambda name, type_, _parent: type_ != "schema" or name == SCHEMA,
                include_object=include_agent_object,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline (SQL script) migrations are not supported")
_run_online()
