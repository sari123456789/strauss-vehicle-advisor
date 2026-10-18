"""LangGraph `AsyncPostgresSaver` in the `agent` schema (P2c stage 2).

Isolation: `langgraph-checkpoint-postgres` 3.1.2 issues unqualified DDL/DML
(`CREATE TABLE IF NOT EXISTS checkpoints ...`, verified in its `base.py`), so every connection
is pinned to `search_path=agent` and the tables can only resolve inside `agent`. Its tables are
managed by the library (`setup()`), never by Alembic: `CHECKPOINTER_TABLES` is excluded from
the AI Alembic environment and no AI migration creates or drops them.

Initialization is explicit (`setup_checkpointer`) and idempotent; importing this module has no
side effects. Its `CREATE INDEX CONCURRENTLY` migrations require autocommit connections.

Fencing: `FencedPostgresSaver` calls the run's `CheckpointFence` before every write. This is
check-then-write, not atomic (see `app.ai.application.ports.checkpoints`); lineage verification
handles the residual race. Thread deletion is refused: conversation history is preserved.
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url

from app.ai.application.ports.checkpoints import current_checkpoint_fence
from app.ai.application.ports.conversation_store import LineageObservation
from app.ai.application.ports.response_staging import MessageRef
from app.ai.infrastructure.persistence.models import CHECKPOINTER_TABLES, SCHEMA

_CONNECTION_KWARGS: dict[str, Any] = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
    "options": f"-c search_path={SCHEMA}",
}


class CheckpointerSetupError(RuntimeError):
    """The checkpointer could not be initialized inside the `agent` schema."""


def libpq_conninfo(url: str) -> str:
    """SQLAlchemy `postgresql+psycopg://` URL -> libpq connection string."""
    parsed = make_url(url).set(drivername="postgresql")
    return parsed.render_as_string(hide_password=False)


def create_checkpointer_pool(
    url: str, *, min_size: int = 1, max_size: int = 4
) -> AsyncConnectionPool[AsyncConnection[DictRow]]:
    """A dedicated pool (not opened yet: `await pool.open()`), pinned to `agent`."""
    return AsyncConnectionPool(
        libpq_conninfo(url),
        min_size=min_size,
        max_size=max_size,
        kwargs=dict(_CONNECTION_KWARGS),
        open=False,
    )


async def setup_checkpointer(url: str) -> None:
    """Create or migrate the library tables in `agent` (explicit, idempotent).

    Refuses to run unless the session's current schema is `agent`, and verifies afterwards that
    no library table exists in any other schema.
    """
    conn = await AsyncConnection.connect(libpq_conninfo(url), **_CONNECTION_KWARGS)
    try:
        current = await (await conn.execute("SELECT current_schema() AS s")).fetchone()
        if current is None or current["s"] != SCHEMA:
            raise CheckpointerSetupError(
                f"the {SCHEMA!r} schema must exist (run the AI migrations first)"
            )
        await AsyncPostgresSaver(conn).setup()
        rows = await (
            await conn.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_name = ANY(%s)",
                (sorted(CHECKPOINTER_TABLES),),
            )
        ).fetchall()
    finally:
        await conn.close()
    found = {(r["table_schema"], r["table_name"]) for r in rows}
    if {name for schema, name in found if schema == SCHEMA} != CHECKPOINTER_TABLES:
        raise CheckpointerSetupError("checkpointer tables are missing from the agent schema")
    outside = sorted(f"{schema}.{name}" for schema, name in found if schema != SCHEMA)
    if outside:
        raise CheckpointerSetupError(f"checkpointer tables found outside agent: {outside}")


class FencedPostgresSaver(AsyncPostgresSaver):
    """`AsyncPostgresSaver` that checks the current run's claim before each write."""

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        await _check_fence()
        return await super().aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await _check_fence()
        await super().aput_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        raise PermissionError("conversation checkpoints are preserved; deletion is not allowed")


async def _check_fence() -> None:
    fence = current_checkpoint_fence.get()
    if fence is not None:
        await fence()


class PostgresCheckpointLineage:
    """`CheckpointLineageReader` over the saver (library API only, no internal SQL)."""

    def __init__(self, saver: AsyncPostgresSaver) -> None:
        self._saver = saver

    async def observe(self, thread_id: str, input_id: str) -> LineageObservation:
        latest = await self._saver.aget_tuple(_config(thread_id))
        if latest is None:
            return LineageObservation(None, None, latest_contains_input=False)
        parent = (
            latest.parent_config["configurable"]["checkpoint_id"] if latest.parent_config else None
        )
        processed = latest.checkpoint["channel_values"].get("processed_input_ids") or []
        return LineageObservation(
            latest_checkpoint_id=str(latest.config["configurable"]["checkpoint_id"]),
            latest_parent_checkpoint_id=None if parent is None else str(parent),
            latest_contains_input=input_id in processed,
        )

    async def latest_message_refs(self, thread_id: str, input_id: str) -> list[MessageRef] | None:
        latest = await self._saver.aget_tuple(_config(thread_id))
        if latest is None:
            return None
        outbox = latest.checkpoint["channel_values"].get("last_outbox")
        if not outbox or outbox.get("inputId") != input_id:
            return None
        return [
            MessageRef(m["messageId"], m["contentSha256"]) for m in outbox.get("messages") or []
        ]


def _config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
