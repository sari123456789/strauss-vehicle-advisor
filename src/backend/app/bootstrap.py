"""Composition root.

SHARED file. Wires Application services to Infrastructure implementations of
Application-owned ports, and the AI Engine to the Business Application through the shared
contracts only (`BusinessToolsPort`, `CaseLifecyclePort`, `BusinessEventFeed`).
The FastAPI-specific pieces (`BusinessApi`, routers, middleware) are assembled in `app.main`.
"""

import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ai.application.event_relay import BusinessEventRelay
from app.ai.application.ports.llm import LlmPort
from app.ai.infrastructure.llm.factory import build_llm_port
from app.ai.infrastructure.llm.settings import load_llm_settings
from app.ai.infrastructure.persistence.schema_status import agent_reachable, agent_schema_status
from app.ai.infrastructure.persistence.settings import load_database_settings
from app.ai.infrastructure.runtime import AiRuntime
from app.ai.infrastructure.runtime import build_ai_runtime as build_ai_engine_runtime
from app.business.application.cases.case_service import CaseService
from app.business.application.cases.lifecycle_adapter import CaseLifecycleAdapter
from app.business.application.comparisons.comparison_service import ComparisonService
from app.business.application.comparisons.readiness_service import ReadinessService
from app.business.application.identity.auth_service import AuthService
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.identity.identity_service import IdentityService
from app.business.application.imports.import_service import ImportReferenceDataService
from app.business.application.occupations.occupation_service import OccupationService
from app.business.application.profiles.profile_service import EmployeeProfileService
from app.business.application.representatives.answer_service import RepresentativeAnswerService
from app.business.application.representatives.question_service import (
    RepresentativeQuestionService,
)
from app.business.application.representatives.request_service import (
    RepresentativeRequestService,
)
from app.business.application.seeding.demo_seed_service import DemoSeedService
from app.business.application.tools_facade import BusinessToolsFacade
from app.business.application.vehicles.catalogue_service import CatalogueService
from app.business.application.vehicles.valuation_service import ValuationService
from app.business.infrastructure.clock import SystemClock
from app.business.infrastructure.database.engine import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_engine,
    create_runtime_engine,
    create_session_factory,
)
from app.business.infrastructure.database.event_feed import SqlBusinessEventFeed
from app.business.infrastructure.database.repositories.reference_data import (
    SqlReferenceDataRepository,
)
from app.business.infrastructure.database.schema_status import (
    business_schema_revision,
    database_reachable,
)
from app.business.infrastructure.database.unit_of_work import SqlUnitOfWorkFactory
from app.business.infrastructure.imports.demo_seed_reader import FileDemoSeedSourceReader
from app.business.infrastructure.imports.reference_source_reader import FileReferenceSourceReader
from app.business.infrastructure.security.password_hasher import ScryptPasswordHasher
from app.business.infrastructure.security.token_codec import Hs256TokenCodec
from app.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
BUSINESS_MIGRATIONS = BACKEND_ROOT / "migrations"
AI_MIGRATIONS = BACKEND_ROOT / "migrations_ai"
TOKEN_ISSUER = "strauss"
TOKEN_AUDIENCE = "strauss-api"

logger = logging.getLogger("app.bootstrap")


@dataclass(frozen=True)
class Container:
    settings: Settings


def build_container(settings: Settings) -> Container:
    return Container(settings=settings)


@dataclass(frozen=True)
class ImportComponents:
    engine: AsyncEngine
    repository: SqlReferenceDataRepository
    service: ImportReferenceDataService


def build_reference_source_reader(repo_root: Path = REPO_ROOT) -> FileReferenceSourceReader:
    return FileReferenceSourceReader(
        repo_root=repo_root,
        manifest_path=repo_root / "data" / "source-manifest.json",
        policy_path=repo_root / "data" / "rules" / "derived-demo-policy.json",
    )


def build_import_components(database_url: str, repo_root: Path = REPO_ROOT) -> ImportComponents:
    """Reference-data import wiring; the caller disposes `engine`."""
    engine = create_engine(database_url)
    repository = SqlReferenceDataRepository(create_session_factory(engine))
    service = ImportReferenceDataService(build_reference_source_reader(repo_root), repository)
    return ImportComponents(engine=engine, repository=repository, service=service)


@dataclass(frozen=True)
class BusinessPersistence:
    engine: AsyncEngine
    unit_of_work: SqlUnitOfWorkFactory
    reference_data: SqlReferenceDataRepository
    sessions: async_sessionmaker[AsyncSession] | None = None


def build_business_persistence(settings: Settings) -> BusinessPersistence:
    """Runtime Business persistence (idle-in-transaction timeout, Q10); caller disposes `engine`."""
    engine = create_runtime_engine(
        settings.database_url.get_secret_value(),
        idle_in_transaction_timeout_ms=settings.idle_in_transaction_timeout_ms,
    )
    sessions = create_session_factory(engine)
    return BusinessPersistence(
        engine=engine,
        unit_of_work=SqlUnitOfWorkFactory(sessions),
        reference_data=SqlReferenceDataRepository(sessions),
        sessions=sessions,
    )


@dataclass(frozen=True)
class SeedComponents:
    engine: AsyncEngine
    service: DemoSeedService


def build_demo_seed_reader(repo_root: Path = REPO_ROOT) -> FileDemoSeedSourceReader:
    return FileDemoSeedSourceReader(
        repo_root=repo_root,
        manifest_path=repo_root / "data" / "source-manifest.json",
        policy_path=repo_root / "data" / "rules" / "derived-demo-policy.json",
        seed_path=repo_root / "data" / "demo" / "demo-accounts.json",
    )


def build_seed_components(database_url: str, repo_root: Path = REPO_ROOT) -> SeedComponents:
    """Demo-seed wiring; the caller disposes `engine`."""
    engine = create_engine(database_url)
    sessions = create_session_factory(engine)
    service = DemoSeedService(
        build_demo_seed_reader(repo_root),
        SqlReferenceDataRepository(sessions),
        SqlUnitOfWorkFactory(sessions),
    )
    return SeedComponents(engine=engine, service=service)


@dataclass(frozen=True)
class BusinessServices:
    """Business Application services over runtime persistence; the caller disposes `engine`.

    Injected later into the shared API (4D) and, through the shared contracts only, into the
    AI tool adapter and worker (`lifecycle` implements `CaseLifecyclePort`).
    """

    engine: AsyncEngine
    identity: IdentityService
    authorization: AuthorizationPolicy
    profiles: EmployeeProfileService
    cases: CaseService
    lifecycle: CaseLifecycleAdapter
    catalogue: CatalogueService
    valuation: ValuationService
    occupations: OccupationService
    representative_requests: RepresentativeRequestService
    representative_questions: RepresentativeQuestionService
    readiness: ReadinessService
    comparisons: ComparisonService
    tools: BusinessToolsFacade
    """`BusinessToolsPort` for the AI tool adapter (injected through the shared contract only)."""
    unit_of_work: SqlUnitOfWorkFactory | None = None
    reference_data: SqlReferenceDataRepository | None = None
    representative_answers: RepresentativeAnswerService | None = None
    """Representative inbox and answers (B3a), exposed over HTTP (B3b)."""
    event_feed: SqlBusinessEventFeed | None = None
    """`BusinessEventFeed` (outbox); given only to the AI event relay (decision D4)."""


def build_business_services(settings: Settings) -> BusinessServices:
    persistence = build_business_persistence(settings)
    uow, reference = persistence.unit_of_work, persistence.reference_data
    assert persistence.sessions is not None
    clock = SystemClock()
    policy = AuthorizationPolicy()
    profiles = EmployeeProfileService(uow, policy, clock)
    cases = CaseService(uow, policy, profiles, reference, clock)
    catalogue = CatalogueService(uow, policy, reference, clock)
    valuation = ValuationService(uow, policy, reference)
    occupations = OccupationService(uow, policy, reference)
    requests = RepresentativeRequestService(uow, policy)
    questions = RepresentativeQuestionService(uow, policy, reference, clock)
    readiness = ReadinessService(uow, policy, reference, clock)
    comparisons = ComparisonService(uow, policy, reference, clock)
    return BusinessServices(
        engine=persistence.engine,
        identity=IdentityService(uow),
        authorization=policy,
        profiles=profiles,
        cases=cases,
        lifecycle=CaseLifecycleAdapter(cases),
        catalogue=catalogue,
        valuation=valuation,
        occupations=occupations,
        representative_requests=requests,
        representative_questions=questions,
        readiness=readiness,
        comparisons=comparisons,
        tools=BusinessToolsFacade(
            cases=cases,
            catalogue=catalogue,
            valuation=valuation,
            occupations=occupations,
            readiness=readiness,
            comparisons=comparisons,
            questions=questions,
            requests=requests,
        ),
        unit_of_work=uow,
        reference_data=reference,
        representative_answers=RepresentativeAnswerService(uow, policy, reference, clock),
        event_feed=SqlBusinessEventFeed(persistence.sessions),
    )


# --- Authentication ------------------------------------------------------------------------


def token_secret(settings: Settings) -> bytes:
    """The configured signing secret; in development/test a random per-process one.

    `Settings` already refuses a missing secret outside development/test and a short one
    anywhere. A generated secret makes every token invalid after a restart (acceptable locally).
    """
    if settings.auth_token_secret is not None:
        return settings.auth_token_secret.get_secret_value().encode()
    logger.warning("auth_token_secret_generated: tokens will not survive a restart")
    return secrets.token_bytes(32)


def build_auth_service(settings: Settings, services: BusinessServices) -> AuthService:
    assert services.unit_of_work is not None
    return AuthService(
        services.unit_of_work,
        ScryptPasswordHasher(),
        Hs256TokenCodec(token_secret(settings), issuer=TOKEN_ISSUER, audience=TOKEN_AUDIENCE),
        SystemClock(),
    )


# --- AI runtime ------------------------------------------------------------------------------


def build_llm(env: dict[str, str] | None = None) -> LlmPort:
    """The active LLM profile (`STRAUSS_AI_LLM_*`); disabled when no profile is configured.

    Logs the provider and model only, never the key.
    """
    llm_settings = load_llm_settings(env)
    profile = llm_settings.active
    logger.info(
        "llm_configured profile=%s provider=%s model=%s",
        llm_settings.active_profile,
        profile.provider,
        profile.model,
    )
    return build_llm_port(llm_settings)


async def build_ai_runtime(
    services: BusinessServices,
    *,
    llm: LlmPort | None = None,
    ai_database_url: str | None = None,
    worker_id: str | None = None,
) -> AiRuntime:
    """The AI runtime over the `agent` schema, wired to the Business services through the
    shared ports only (`BusinessToolsPort`, `CaseLifecyclePort`)."""
    url = ai_database_url or load_database_settings().database_url.get_secret_value()
    return await build_ai_engine_runtime(
        url,
        business_tools=services.tools,
        case_lifecycle=services.lifecycle,
        llm=llm if llm is not None else build_llm(),
        worker_id=worker_id,
    )


def build_event_relay(services: BusinessServices, runtime: AiRuntime) -> BusinessEventRelay:
    """The outbox relay: the Business feed is injected only here (decision D4); the AI side
    enqueues through its own durable input queue (`ConversationService`)."""
    assert services.event_feed is not None
    return BusinessEventRelay(services.event_feed, runtime.conversations)


# --- Startup verification and readiness --------------------------------------------------------


class SchemaNotReadyError(RuntimeError):
    """The database is not at the expected migration head (no connection details included)."""


def _describe(revisions: frozenset[str]) -> str:
    return ",".join(sorted(revisions)) or "none"


async def verify_schemas(services: BusinessServices, runtime: AiRuntime) -> None:
    """Fail startup clearly when a migration or the checkpointer setup is missing.

    Read-only: the API never migrates on startup (deployment runs the migrations explicitly).
    """
    business = await business_schema_revision(services.engine, BUSINESS_MIGRATIONS)
    if not business.up_to_date:
        raise SchemaNotReadyError(
            f"Business schema at {_describe(business.current)}, expected "
            f"{_describe(business.expected)}; run `alembic upgrade head`"
        )
    agent = await agent_schema_status(runtime.engine, AI_MIGRATIONS)
    if not agent.up_to_date:
        raise SchemaNotReadyError(
            f"Agent schema at {_describe(agent.current)}, expected {_describe(agent.expected)}; "
            "run `alembic -c alembic_ai.ini upgrade head`"
        )
    if not agent.checkpointer_ready:
        raise SchemaNotReadyError(
            "LangGraph checkpointer tables are missing; run `python -m app.cli.setup_checkpointer`"
        )


ReadinessProbe = Callable[[], Awaitable[dict[str, Any]]]


@dataclass
class ReadinessSources:
    services: BusinessServices
    runtime: AiRuntime
    worker_expected: bool
    extra: dict[str, Callable[[], dict[str, Any]]] = field(default_factory=dict)
    """Informational sections (for example the event relay counters)."""


def build_readiness_probe(sources: ReadinessSources) -> ReadinessProbe:
    """Readiness: databases reachable, a reference release published, worker alive (if enabled).

    Returns booleans and counters only, never connection details or data.
    """

    async def probe() -> dict[str, Any]:
        business = await database_reachable(sources.services.engine)
        release = False
        if business and sources.services.reference_data is not None:
            try:
                release = await sources.services.reference_data.latest_release() is not None
            except Exception:  # noqa: BLE001 - readiness reports, it never raises
                release = False
        agent = await agent_reachable(sources.runtime.engine)
        worker = sources.runtime.in_process.running if sources.worker_expected else True
        checks: dict[str, Any] = {
            "businessDatabase": business,
            "referenceRelease": release,
            "agentDatabase": agent,
            "worker": worker,
        }
        ready = all(checks.values())
        info = {name: read() for name, read in sources.extra.items()}
        return {"ready": ready, "checks": checks, "info": info}

    return probe
