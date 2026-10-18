"""Composition root.

SHARED file (Business + AI developers) - provisional skeleton from Step 0.
Wires Application services to Infrastructure implementations of Application-owned
ports, and (later) the AI Engine to the Business Application.
"""

from dataclasses import dataclass
from pathlib import Path

from app.application.imports.import_service import ImportReferenceDataService
from app.infrastructure.database.engine import AsyncEngine, create_engine, create_session_factory
from app.infrastructure.database.repositories.reference_data import SqlReferenceDataRepository
from app.infrastructure.imports.reference_source_reader import FileReferenceSourceReader
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
