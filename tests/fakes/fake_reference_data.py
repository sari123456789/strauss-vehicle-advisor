"""In-memory `ReferenceSourceReader` / `ReferenceDataRepository` for Application tests."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.business.application.ports.reference_data import (
    JsonScalar,
    PublicationResult,
    PublishedRelease,
    ReferenceSourceSet,
    ReleaseIdentity,
)
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.rules import RulesConfig
from app.business.domain.vehicles import CatalogueVehicle, TaxRecord, VehicleTaxKey


class StaticSourceReader:
    def __init__(self, sources: ReferenceSourceSet) -> None:
        self.sources = sources
        self.reads = 0

    def read_all(self) -> ReferenceSourceSet:
        self.reads += 1
        return self.sources


class InMemoryReferenceDataRepository:
    def __init__(self) -> None:
        self.releases: list[PublishedRelease] = []
        self._sets: dict[UUID, ReferenceSourceSet] = {}
        self._rules: dict[UUID, RulesConfig] = {}

    async def latest_release(self) -> PublishedRelease | None:
        return self.releases[-1] if self.releases else None

    async def get_release(self, release_id: UUID) -> PublishedRelease | None:
        return next((r for r in self.releases if r.release_id == release_id), None)

    async def publish(
        self,
        sources: ReferenceSourceSet,
        consistency_report: Mapping[str, JsonScalar | Sequence[JsonScalar]],
        published_by: UUID,
    ) -> PublicationResult:
        identity = ReleaseIdentity.of(sources)
        latest = await self.latest_release()
        if latest is not None and latest.identity == identity:
            return PublicationResult(release=latest, created=False)
        release = PublishedRelease(
            release_id=uuid4(),
            release_seq=len(self.releases) + 1,
            rules_snapshot_id=uuid4(),
            catalogue_version_id=uuid4(),
            tax_dataset_version_id=uuid4(),
            identity=identity,
            derived_policy_version=sources.rules.derived_policy_version,
            published_at=datetime.now(UTC),
        )
        self.releases.append(release)
        self._sets[release.catalogue_version_id] = sources
        self._rules[release.rules_snapshot_id] = sources.rules.config
        return PublicationResult(release=release, created=True)

    async def get_rules(self, rules_snapshot_id: UUID) -> RulesConfig | None:
        return self._rules.get(rules_snapshot_id)

    async def get_vehicle(
        self, catalogue_version_id: UUID, catalogue_id: str
    ) -> CatalogueVehicle | None:
        sources = self._sets.get(catalogue_version_id)
        vehicles = () if sources is None else sources.catalogue.vehicles
        return next((t.vehicle for t in vehicles if t.vehicle.catalogue_id == catalogue_id), None)

    async def list_vehicles(self, catalogue_version_id: UUID) -> tuple[CatalogueVehicle, ...]:
        sources = self._sets.get(catalogue_version_id)
        return () if sources is None else tuple(t.vehicle for t in sources.catalogue.vehicles)

    async def get_occupation(
        self, catalogue_version_id: UUID, occupation_id: str
    ) -> OccupationPolicy | None:
        sources = self._sets.get(catalogue_version_id)
        occupations = () if sources is None else sources.catalogue.occupations
        return next(
            (t.occupation for t in occupations if t.occupation.occupation_id == occupation_id),
            None,
        )

    async def get_value_of_use(
        self, tax_dataset_version_id: UUID, key: VehicleTaxKey
    ) -> TaxRecord | None:
        return None
