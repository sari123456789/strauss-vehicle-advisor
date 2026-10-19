"""`ThreadLock` over PostgreSQL session-level advisory locks.

Each hold uses its own autocommit connection (never a transaction, so the AI idle-in-
transaction timeout cannot end it during a long turn). The lock is released explicitly on
exit, or by PostgreSQL when the connection dies with its process - which is what makes taking
over an expired claim safe.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection

from app.ai.infrastructure.checkpoints.postgres import libpq_conninfo

LOCK_NAMESPACE = "strauss-ai-thread:"


class PostgresThreadLock:
    def __init__(self, url: str, *, connect_timeout: int = 5) -> None:
        self._conninfo = libpq_conninfo(url)
        self._connect_timeout = connect_timeout

    @asynccontextmanager
    async def hold(self, thread_id: str) -> AsyncIterator[bool]:
        key = LOCK_NAMESPACE + thread_id
        conn = await AsyncConnection.connect(
            self._conninfo, autocommit=True, connect_timeout=self._connect_timeout
        )
        try:
            row = await (
                await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,))
            ).fetchone()
            acquired = bool(row and row[0])
            try:
                yield acquired
            finally:
                if acquired and not conn.closed:
                    await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
        finally:
            await conn.close()
