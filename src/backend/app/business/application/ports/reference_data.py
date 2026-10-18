"""Reference-data ports: reading the supplied sources and publishing immutable versions.

Infrastructure implements both Protocols; bootstrap wires them. Only Domain types and plain
values cross this boundary - no ORM objects, file handles or framework types.

A *release* is the consistent set (rules snapshot, catalogue version, tax dataset version) that
was validated together. The active set is always the latest release, never components selected
independently. Releases and versions are immutable; history is never rewritten.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.business.domain.occupations import OccupationPolicy
from app.business.domain.rules import RulesConfig
from app.business.domain.vehicles import CatalogueVehicle, Propulsion, TaxRecord, VehicleTaxKey

JsonScalar = str | int | bool | None


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    kind: str
    """e.g. `business_rules_json`, `vehicle_tax_dataset_csv` (data/source-manifest.json kinds)."""
    path: str
    """Repository-relative path, for traceability only."""
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ImportReport:
    kind: str
    sha256: str
    record_count: int
    warnings: tuple[str, ...] = ()
    details: Mapping[str, JsonScalar | Sequence[JsonScalar]] | None = None
    """Rejected records are always zero: any invalid record fails the whole import."""


@dataclass(frozen=True, slots=True)
class SourceRow:
    """Original source values (untrimmed, unconverted) and their 1-based source row number."""

    row_number: int
    values: tuple[JsonScalar, ...]


@dataclass(frozen=True, slots=True)
class TracedVehicle:
    vehicle: CatalogueVehicle
    source: SourceRow


@dataclass(frozen=True, slots=True)
class TracedOccupation:
    occupation: OccupationPolicy
    source: SourceRow


@dataclass(frozen=True, slots=True)
class TracedTaxRecord:
    record: TaxRecord
    drive_code: str
    """Raw CSV drive-type code, stored uninterpreted (SRC-04)."""
    source: SourceRow


@dataclass(frozen=True, slots=True)
class RulesSource:
    config: RulesConfig
    rules: SourceFingerprint
    policy: SourceFingerprint
    rules_content: Mapping[str, object]
    """JSON-safe copy of the rules document (Decimals as strings)."""
    policy_content: Mapping[str, object]
    drive_field: Mapping[str, Propulsion]
    """Rules JSON `valueOfUse.driveField`: raw CSV drive code -> propulsion category."""

    @property
    def derived_policy_version(self) -> str:
        return self.config.derived.version


@dataclass(frozen=True, slots=True)
class CatalogueSource:
    workbook: SourceFingerprint
    policy_sha256: str
    """The derived policy supplies occupation IDs and vehicle-type codes for this catalogue."""
    vehicles: tuple[TracedVehicle, ...]
    occupations: tuple[TracedOccupation, ...]
    report: ImportReport


@dataclass(frozen=True, slots=True)
class TaxDatasetSource:
    csv: SourceFingerprint
    original_header: tuple[str, ...]
    """Header strings exactly as decoded, including surrounding whitespace."""
    tax_years: tuple[str, ...]
    records: tuple[TracedTaxRecord, ...]
    report: ImportReport


@dataclass(frozen=True, slots=True)
class PdfSource:
    """Integrity/readability only: nothing in the PDF is interpreted as a business rule."""

    pdf: SourceFingerprint
    page_count: int


@dataclass(frozen=True, slots=True)
class ReferenceSourceSet:
    rules: RulesSource
    catalogue: CatalogueSource
    tax_dataset: TaxDatasetSource
    challenge_pdf: PdfSource


@dataclass(frozen=True, slots=True)
class ReleaseIdentity:
    rules_sha256: str
    policy_sha256: str
    workbook_sha256: str
    tax_dataset_sha256: str
    challenge_pdf_sha256: str

    @classmethod
    def of(cls, sources: ReferenceSourceSet) -> "ReleaseIdentity":
        return cls(
            rules_sha256=sources.rules.rules.sha256,
            policy_sha256=sources.rules.policy.sha256,
            workbook_sha256=sources.catalogue.workbook.sha256,
            tax_dataset_sha256=sources.tax_dataset.csv.sha256,
            challenge_pdf_sha256=sources.challenge_pdf.pdf.sha256,
        )


@dataclass(frozen=True, slots=True)
class PublishedRelease:
    release_id: UUID
    release_seq: int
    rules_snapshot_id: UUID
    catalogue_version_id: UUID
    tax_dataset_version_id: UUID
    identity: ReleaseIdentity
    derived_policy_version: str
    published_at: datetime


@dataclass(frozen=True, slots=True)
class PublicationResult:
    release: PublishedRelease
    created: bool
    """False when the identical set was already the latest release (idempotent no-op)."""


class ReferenceDataConflictError(Exception):
    """Publication would contradict already-published immutable data (e.g. reused version)."""

    code = "SOURCE_CONFLICT"


@runtime_checkable
class ReferenceSourceReader(Protocol):
    def read_all(self) -> ReferenceSourceSet:
        """Read and validate every source; raise on any decoding/schema/identifier problem."""
        ...


@runtime_checkable
class ReferenceDataRepository(Protocol):
    async def latest_release(self) -> PublishedRelease | None: ...

    async def get_release(self, release_id: UUID) -> PublishedRelease | None: ...

    async def publish(
        self,
        sources: ReferenceSourceSet,
        consistency_report: Mapping[str, JsonScalar | Sequence[JsonScalar]],
        published_by: UUID,
    ) -> PublicationResult:
        """Atomically store new versions (or reuse identical ones) and append a release.

        All-or-nothing: on any failure nothing is written and the latest release is unchanged.
        """
        ...

    async def get_rules(self, rules_snapshot_id: UUID) -> RulesConfig | None:
        """The immutable rules snapshot (source rules + derived policy) as Domain config."""
        ...

    async def get_vehicle(
        self, catalogue_version_id: UUID, catalogue_id: str
    ) -> CatalogueVehicle | None: ...

    async def list_vehicles(self, catalogue_version_id: UUID) -> tuple[CatalogueVehicle, ...]: ...

    async def get_occupation(
        self, catalogue_version_id: UUID, occupation_id: str
    ) -> OccupationPolicy | None: ...

    async def get_value_of_use(
        self, tax_dataset_version_id: UUID, key: VehicleTaxKey
    ) -> TaxRecord | None:
        """Lookup by the complete five-part key only; there is no partial-key lookup."""
        ...
