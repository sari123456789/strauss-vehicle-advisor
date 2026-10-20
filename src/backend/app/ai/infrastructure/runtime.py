"""Composition of the AI runtime (AI-owned wiring; no web framework, no Business imports).

The Business side is injected only through the shared ports: `BusinessToolsPort` (agent
tools) and `CaseLifecyclePort` (case creation). The shared bootstrap passes the real
`BusinessToolsFacade` / `CaseLifecycleAdapter`; tests pass fakes. The LLM is injected too, so
automated tests use a scripted model and never call a provider.

Usage in the FastAPI lifespan (in-process worker, cloud plan DECISION W):

    runtime = await build_ai_runtime(url, business_tools=..., case_lifecycle=..., llm=...)
    app.state.ai_conversations = runtime.conversations
    runtime.in_process.start()
    try:
        yield
    finally:
        await runtime.aclose()                     # stops the worker, then the connections

A separate worker process instead awaits `runtime.worker.run_forever(stop)`.
"""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.application.conversation_service import ConversationService
from app.ai.application.ports.conversation_store import InputClaim
from app.ai.application.ports.llm import LlmPort
from app.ai.application.ports.response_staging import ResponseStager
from app.ai.application.turn_worker import DEFAULT_LEASE_SECONDS, TurnWorker
from app.ai.infrastructure.checkpoints.postgres import (
    FencedPostgresSaver,
    PostgresCheckpointLineage,
    create_checkpointer_pool,
    setup_checkpointer,
)
from app.ai.infrastructure.persistence.engine import create_ai_engine, create_ai_session_factory
from app.ai.infrastructure.persistence.staging import SqlResponseStager
from app.ai.infrastructure.persistence.store import AgentPersistence
from app.ai.infrastructure.persistence.thread_lock import PostgresThreadLock
from app.ai.workflow.runner import LangGraphTurnRunner
from app.contracts.business_port import BusinessToolsPort
from app.contracts.case_lifecycle import CaseLifecyclePort

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]

logger = logging.getLogger("app.ai.worker")
DEFAULT_IDLE_SECONDS = 1.0
DEFAULT_STOP_TIMEOUT_SECONDS = 20.0


class InProcessWorker:
    """Runs `TurnWorker.run_forever` as one asyncio task inside the API process (cloud plan
    DECISION W: Render's free tier has no background workers).

    `start()` from the FastAPI lifespan; `stop()` on shutdown. Stop first asks the loop to
    finish its current input; after `stop_timeout` it cancels the task. A cancelled attempt
    writes nothing more: its lease expires and the input is taken over later (as after a
    crash), and its thread lock is released with its connection.
    """

    def __init__(
        self,
        worker: TurnWorker,
        *,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
    ) -> None:
        self._worker = worker
        self._idle = idle_seconds
        self._stop_timeout = stop_timeout
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            raise RuntimeError("the in-process worker is already running")
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._worker.run_forever(self._stop, idle_seconds=self._idle), name="ai-turn-worker"
        )
        self._task.add_done_callback(_log_unexpected_exit)
        logger.info("in_process_worker_started")

    async def stop(self) -> None:
        """Idempotent; returns once the task has finished."""
        task, stop = self._task, self._stop
        if task is None or stop is None:
            return
        stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._stop_timeout)
        except TimeoutError:
            logger.warning("in_process_worker_stop_timeout: cancelling the current attempt")
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        except Exception:  # noqa: BLE001 - already logged by the done callback
            pass
        finally:
            self._task = None
            self._stop = None
        logger.info("in_process_worker_stopped")


def _log_unexpected_exit(task: "asyncio.Task[None]") -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("in_process_worker_exited error=%s", type(task.exception()).__name__)


@dataclass
class AiRuntime:
    engine: AsyncEngine
    pool: Pool
    persistence: AgentPersistence
    runner: LangGraphTurnRunner
    conversations: ConversationService
    worker: TurnWorker
    in_process: InProcessWorker = field(init=False)

    def __post_init__(self) -> None:
        self.in_process = InProcessWorker(self.worker)

    async def aclose(self) -> None:
        """Stops the in-process worker (if started), then releases the connections."""
        await self.in_process.stop()
        await self.pool.close()
        await self.engine.dispose()


async def build_ai_runtime(
    database_url: str,
    *,
    business_tools: BusinessToolsPort,
    case_lifecycle: CaseLifecyclePort,
    llm: LlmPort,
    worker_id: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    idle_in_transaction_timeout_ms: int | None = 15_000,
    setup_checkpointer_tables: bool = False,
) -> AiRuntime:
    """Build every AI runtime component over one database (`agent` schema).

    `setup_checkpointer_tables` runs the idempotent LangGraph table setup in `agent`; leave it
    off where a deployment step does it (the AI migrations must already be applied).
    """
    if setup_checkpointer_tables:
        await setup_checkpointer(database_url)
    engine = create_ai_engine(
        database_url, idle_in_transaction_timeout_ms=idle_in_transaction_timeout_ms
    )
    pool: Pool = create_checkpointer_pool(database_url)
    try:
        await pool.open()
        persistence = AgentPersistence(create_ai_session_factory(engine))
        saver = FencedPostgresSaver(pool)
        runner = LangGraphTurnRunner(business_tools, llm, checkpointer=saver)

        def stager(claim: InputClaim) -> ResponseStager:
            return SqlResponseStager(persistence, claim)

        worker = TurnWorker(
            persistence,
            runner,
            PostgresCheckpointLineage(saver),
            stager,
            PostgresThreadLock(database_url),
            worker_id=worker_id or f"worker-{uuid4().hex[:12]}",
            lease_seconds=lease_seconds,
        )
    except BaseException:
        await pool.close()
        await engine.dispose()
        raise
    return AiRuntime(
        engine=engine,
        pool=pool,
        persistence=persistence,
        runner=runner,
        conversations=ConversationService(persistence, case_lifecycle),
        worker=worker,
    )
