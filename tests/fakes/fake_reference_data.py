"""In-memory `ReferenceSourceReader` / `ReferenceDataRepository` for Application tests."""

from collections.abc import Mapping, Sequence
from dataclasses import astuple
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.business.application.ports.reference_data import (
    JsonScalar,
    PublicationResult,
    PublishedRelease,
    ReferenceSourceSet,
    ReleaseIdentity,
    TaxRecordDetails,
    TracedTaxRecord,
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
        self._tax: dict[UUID, ReferenceSourceSet] = {}

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
        self._tax[release.tax_dataset_version_id] = sources
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

    async def list_occupations(self, catalogue_version_id: UUID) -> tuple[OccupationPolicy, ...]:
        sources = self._sets.get(catalogue_version_id)
        return () if sources is None else tuple(t.occupation for t in sources.catalogue.occupations)

    def _tax_records(self, tax_dataset_version_id: UUID) -> tuple[TracedTaxRecord, ...]:
        sources = self._tax.get(tax_dataset_version_id)
        return () if sources is None else sources.tax_dataset.records

    async def get_tax_record_details(
        self, tax_dataset_version_id: UUID, key: VehicleTaxKey
    ) -> TaxRecordDetails | None:
        return next(
            (_details(t) for t in self._tax_records(tax_dataset_version_id) if t.record.key == key),
            None,
        )

    async def find_tax_records(
        self,
        tax_dataset_version_id: UUID,
        *,
        commercial_name: str,
        registration_year: str | None = None,
        manufacturer_code: str | None = None,
        model_code: str | None = None,
        limit: int,
    ) -> tuple[TaxRecordDetails, ...]:
        needle = commercial_name.casefold()
        found = [
            _details(t)
            for t in self._tax_records(tax_dataset_version_id)
            if needle in str(t.source.values[COMMERCIAL_NAME] or "").strip().casefold()
            and registration_year in (None, t.record.key.registration_year)
            and manufacturer_code in (None, t.record.key.manufacturer_code)
            and model_code in (None, t.record.key.model_code)
        ]
        found.sort(key=lambda d: astuple(d.record.key))
        return tuple(found[:limit])

    async def get_value_of_use(
        self, tax_dataset_version_id: UUID, key: VehicleTaxKey
    ) -> TaxRecord | None:
        details = await self.get_tax_record_details(tax_dataset_version_id, key)
        return None if details is None else details.record


COMMERCIAL_NAME, MANUFACTURER_NAME, MODEL_DESCRIPTION = 8, 4, 6


def _details(traced: TracedTaxRecord) -> TaxRecordDetails:
    values = traced.source.values
    return TaxRecordDetails(
        record=traced.record,
        manufacturer_name=str(values[MANUFACTURER_NAME] or "").strip(),
        commercial_name=str(values[COMMERCIAL_NAME] or "").strip(),
        model_description=str(values[MODEL_DESCRIPTION] or "").strip(),
    )
