"""Alembic environment (async, psycopg 3). Runs with the migration identity.

The URL is taken from `config.attributes["database_url"]` when a caller (e.g. the DB test
fixture) provides one, otherwise from `Settings.migration_database_url`.
"""

from alembic import context
from sqlalchemy import Connection

from app.business.infrastructure.database.engine import create_engine
from app.business.infrastructure.database.models import Base
from app.runtime import run_async
from app.settings import get_settings

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.attributes.get("database_url")
    if isinstance(url, str):
        return url
    return get_settings().migration_database_url.get_secret_value()


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=lambda name, type_, _parent: type_ != "schema" or name in ("business", "auth"),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_engine(_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline (SQL script) migrations are not supported")
run_async(_run_online())
