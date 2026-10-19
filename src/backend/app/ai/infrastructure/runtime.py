"""Composition of the AI runtime (AI-owned wiring; no web framework, no Business imports).

The Business side is injected only through the shared ports: `BusinessToolsPort` (agent
tools) and `CaseLifecyclePort` (case creation). The shared bootstrap passes the real
`BusinessToolsFacade` / `CaseLifecycleAdapter`; tests pass fakes. The LLM is injected too, so
automated tests use a scripted model and never call a provider.

Usage (bootstrap, API lifespan or a separate worker process):

    runtime = await build_ai_runtime(url, business_tools=..., case_lifecycle=..., llm=...)
    try:
        stop = asyncio.Event()
        await runtime.worker.run_forever(stop)      # or schedule it as a task in-process
    finally:
        await runtime.aclose()
"""

from dataclasses import dataclass
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


@dataclass
class AiRuntime:
    engine: AsyncEngine
    pool: Pool
    persistence: AgentPersistence
    runner: LangGraphTurnRunner
    conversations: ConversationService
    worker: TurnWorker

    async def aclose(self) -> None:
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
