"""Import use case: authorization, whole-set validation before any write, idempotency."""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from app.business.application.imports.import_service import (
    ImportNotAuthorizedError,
    ImportReferenceDataService,
    ImportStatus,
)
from app.business.application.ports.reference_data import (
    ReferenceDataRepository,
    ReferenceSourceReader,
    ReferenceSourceSet,
)
from app.business.bl.reference_data.consistency import SourceConflictError
from app.contracts.tool_context import ActorRef, Origin, Role
from tests.fakes.fake_reference_data import InMemoryReferenceDataRepository, StaticSourceReader
from tests.reference_sources import real_sources

OPERATOR = ActorRef(subject_id=uuid4(), roles=frozenset({Role.IMPORT_OPERATOR}), origin=Origin.CLI)


def _service(
    sources: ReferenceSourceSet | None = None,
) -> tuple[ImportReferenceDataService, InMemoryReferenceDataRepository]:
    repository = InMemoryReferenceDataRepository()
    return ImportReferenceDataService(
        StaticSourceReader(sources or real_sources()), repository
    ), repository


def test_fakes_conform_to_the_ports() -> None:
    assert isinstance(StaticSourceReader(real_sources()), ReferenceSourceReader)
    assert isinstance(InMemoryReferenceDataRepository(), ReferenceDataRepository)


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.REPRESENTATIVE])
async def test_only_import_operators_may_import(role: Role) -> None:
    service, repository = _service()
    actor = ActorRef(subject_id=uuid4(), roles=frozenset({role}), origin=Origin.HTTP)
    with pytest.raises(ImportNotAuthorizedError):
        await service.run(actor)
    assert repository.releases == []


async def test_dry_run_validates_without_publishing() -> None:
    service, repository = _service()
    outcome = await service.run(OPERATOR, dry_run=True)
    assert outcome.status is ImportStatus.VALIDATED
    assert outcome.release is None and repository.releases == []
    assert outcome.consistency.checked_vehicle_keys == 20


async def test_publish_then_identical_reimport_is_a_no_op() -> None:
    service, repository = _service()
    first = await service.run(OPERATOR)
    second = await service.run(OPERATOR)
    assert first.status is ImportStatus.PUBLISHED
    assert second.status is ImportStatus.UNCHANGED
    assert first.release is not None and second.release == first.release
    assert len(repository.releases) == 1
    assert first.release.derived_policy_version == "derived-demo-v2"


async def test_inconsistent_set_is_rejected_before_any_write() -> None:
    sources = real_sources()
    vehicles = list(sources.catalogue.vehicles)
    vehicles[0] = replace(
        vehicles[0], vehicle=replace(vehicles[0].vehicle, catalogue_value_of_use=Decimal(1))
    )
    broken = replace(sources, catalogue=replace(sources.catalogue, vehicles=tuple(vehicles)))
    service, repository = _service(broken)
    with pytest.raises(SourceConflictError):
        await service.run(OPERATOR)
    assert repository.releases == []
