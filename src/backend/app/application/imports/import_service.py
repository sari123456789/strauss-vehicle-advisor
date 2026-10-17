"""Import and publish one consistent reference-data set (rules, catalogue, tax dataset).

Validation phase (no writes): read every source, check rules-policy support and cross-source
consistency for the whole set. Publication phase: one atomic repository call that stores new
immutable versions (or reuses identical ones) and appends a release. If anything fails the
previous release stays the active one.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.application.ports.reference_data import (
    ImportReport,
    JsonScalar,
    PublishedRelease,
    ReferenceDataRepository,
    ReferenceSourceReader,
    ReferenceSourceSet,
    ReleaseIdentity,
)
from app.business.reference_data.consistency import (
    ConsistencyReport,
    TaxEntry,
    check_reference_consistency,
)
from app.business.rules.validator import validate_supported_rules
from app.contracts.tool_context import ActorRef, Role


class ImportNotAuthorizedError(Exception):
    code = "FORBIDDEN"


class ImportStatus(StrEnum):
    VALIDATED = "validated"
    """Dry run: the set is valid; nothing was written."""
    PUBLISHED = "published"
    UNCHANGED = "unchanged"
    """The identical set is already the latest release."""


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    status: ImportStatus
    identity: ReleaseIdentity
    consistency: ConsistencyReport
    reports: tuple[ImportReport, ...]
    pdf_page_count: int
    release: PublishedRelease | None


def _validate(sources: ReferenceSourceSet) -> ConsistencyReport:
    validate_supported_rules(sources.rules.config)
    catalogue = sources.catalogue
    return check_reference_consistency(
        rules=sources.rules.config,
        vehicles=[t.vehicle for t in catalogue.vehicles],
        occupations=[t.occupation for t in catalogue.occupations],
        tax_records={
            t.record.key: TaxEntry(t.record, t.drive_code) for t in sources.tax_dataset.records
        },
        drive_field=sources.rules.drive_field,
    )


class ImportReferenceDataService:
    def __init__(self, reader: ReferenceSourceReader, repository: ReferenceDataRepository) -> None:
        self._reader = reader
        self._repository = repository

    async def run(self, actor: ActorRef, *, dry_run: bool = False) -> ImportOutcome:
        if Role.IMPORT_OPERATOR not in actor.roles:
            raise ImportNotAuthorizedError(
                "reference-data import requires the import_operator role"
            )

        sources = self._reader.read_all()
        consistency = _validate(sources)
        identity = ReleaseIdentity.of(sources)

        def outcome(status: ImportStatus, release: PublishedRelease | None) -> ImportOutcome:
            return ImportOutcome(
                status=status,
                identity=identity,
                consistency=consistency,
                reports=(sources.catalogue.report, sources.tax_dataset.report),
                pdf_page_count=sources.challenge_pdf.page_count,
                release=release,
            )

        if dry_run:
            return outcome(ImportStatus.VALIDATED, None)
        report: dict[str, JsonScalar | Sequence[JsonScalar]] = {
            **consistency.as_dict(),
            "warnings": list(sources.catalogue.report.warnings),
            "challengePdfPages": sources.challenge_pdf.page_count,
        }
        result = await self._repository.publish(sources, report, actor.subject_id)
        status = ImportStatus.PUBLISHED if result.created else ImportStatus.UNCHANGED
        return outcome(status, result.release)
