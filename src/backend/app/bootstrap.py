"""Composition root.

SHARED file (Business + AI developers) - provisional skeleton from Step 0.
Wires Application services to Infrastructure implementations of Application-owned
ports, and (later) the AI Engine to the Business Application.
"""

from dataclasses import dataclass
from pathlib import Path

from app.business.application.cases.case_service import CaseService
from app.business.application.cases.lifecycle_adapter import CaseLifecycleAdapter
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.identity.identity_service import IdentityService
from app.business.application.imports.import_service import ImportReferenceDataService
from app.business.application.profiles.profile_service import EmployeeProfileService
from app.business.application.seeding.demo_seed_service import DemoSeedService
from app.business.infrastructure.clock import SystemClock
from app.business.infrastructure.database.engine import (
    AsyncEngine,
    create_engine,
    create_runtime_engine,
    create_session_factory,
)
from app.business.infrastructure.database.repositories.reference_data import (
    SqlReferenceDataRepository,
)
from app.business.infrastructure.database.unit_of_work import SqlUnitOfWorkFactory
from app.business.infrastructure.imports.demo_seed_reader import FileDemoSeedSourceReader
from app.business.infrastructure.imports.reference_source_reader import FileReferenceSourceReader
from app.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]


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


def build_business_persistence(settings: Settings) -> BusinessPersistence:
    """Runtime Business persistence (idle-in-transaction timeout, Q10); caller disposes `engine`.

    The durable input queue stays the explicit placeholder until D1/D2 are decided.
    """
    engine = create_runtime_engine(
        settings.database_url.get_secret_value(),
        idle_in_transaction_timeout_ms=settings.idle_in_transaction_timeout_ms,
    )
    sessions = create_session_factory(engine)
    return BusinessPersistence(
        engine=engine,
        unit_of_work=SqlUnitOfWorkFactory(sessions),
        reference_data=SqlReferenceDataRepository(sessions),
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


def build_business_services(settings: Settings) -> BusinessServices:
    persistence = build_business_persistence(settings)
    clock = SystemClock()
    policy = AuthorizationPolicy()
    profiles = EmployeeProfileService(persistence.unit_of_work, policy, clock)
    cases = CaseService(
        persistence.unit_of_work, policy, profiles, persistence.reference_data, clock
    )
    return BusinessServices(
        engine=persistence.engine,
        identity=IdentityService(persistence.unit_of_work),
        authorization=policy,
        profiles=profiles,
        cases=cases,
        lifecycle=CaseLifecycleAdapter(cases),
    )
