"""Read-only startup and readiness checks for the Business database (no DDL, no migration).

The API never migrates on startup: deployment runs `alembic upgrade head` explicitly. These
helpers only compare the applied revision with the head of the migration scripts and probe
connectivity, so a missing migration fails startup with a clear message instead of failing
later inside a request. No connection details or data are returned.
"""

from dataclasses import dataclass
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class SchemaRevision:
    current: frozenset[str]
    expected: frozenset[str]

    @property
    def up_to_date(self) -> bool:
        return self.current == self.expected


def _current_heads(connection: Connection) -> frozenset[str]:
    return frozenset(MigrationContext.configure(connection).get_current_heads())


async def business_schema_revision(engine: AsyncEngine, scripts: Path) -> SchemaRevision:
    """Applied Business revision (`alembic_version`) against the head of `scripts`."""
    expected = frozenset(ScriptDirectory(str(scripts)).get_heads())
    async with engine.connect() as connection:
        current = await connection.run_sync(_current_heads)
    return SchemaRevision(current=current, expected=expected)


async def database_reachable(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - readiness reports, it never raises
        return False
    return True
