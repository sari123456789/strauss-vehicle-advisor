"""Read-only startup and readiness checks for the `agent` schema (no DDL, no migration).

Deployment applies `alembic -c alembic_ai.ini upgrade head` and then creates the LangGraph
tables with `python -m app.cli.setup_checkpointer`; the API only verifies both happened.
"""

from dataclasses import dataclass
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.infrastructure.persistence.models import SCHEMA

VERSION_TABLE = "alembic_version_ai"
CHECKPOINTER_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


@dataclass(frozen=True)
class AgentSchemaStatus:
    current: frozenset[str]
    expected: frozenset[str]
    checkpointer_ready: bool

    @property
    def up_to_date(self) -> bool:
        return self.current == self.expected


def _current_heads(connection: Connection) -> frozenset[str]:
    context = MigrationContext.configure(
        connection, opts={"version_table": VERSION_TABLE, "version_table_schema": SCHEMA}
    )
    return frozenset(context.get_current_heads())


def _checkpointer_ready(connection: Connection) -> bool:
    found: int = connection.execute(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_name = ANY(:names)"
        ),
        {"schema": SCHEMA, "names": list(CHECKPOINTER_TABLES)},
    ).scalar_one()
    return found == len(CHECKPOINTER_TABLES)


async def agent_schema_status(engine: AsyncEngine, scripts: Path) -> AgentSchemaStatus:
    expected = frozenset(ScriptDirectory(str(scripts)).get_heads())
    async with engine.connect() as connection:
        current = await connection.run_sync(_current_heads)
        ready = await connection.run_sync(_checkpointer_ready)
    return AgentSchemaStatus(current=current, expected=expected, checkpointer_ready=ready)


async def agent_reachable(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - readiness reports, it never raises
        return False
    return True
