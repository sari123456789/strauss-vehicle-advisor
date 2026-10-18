"""`Outcome[T]`: a successful service result plus the business observations around it.

Services return an `Outcome` (or raise a `BusinessError`); the tools facade (Step 4C) turns it
into the shared `Envelope`. `status` is `ok`, `partial` or `needs_information` - never `error`.
"""

from dataclasses import dataclass, field
from uuid import UUID

from app.contracts.common import Envelope, EnvelopeStatus, Issue, SourceVersions


@dataclass(frozen=True, slots=True)
class Outcome[T]:
    data: T
    status: EnvelopeStatus = EnvelopeStatus.OK
    issues: tuple[Issue, ...] = field(default=())
    case_version: int | None = None
    profile_version: int | None = None
    source_versions: SourceVersions | None = None

    def __post_init__(self) -> None:
        if self.status is EnvelopeStatus.ERROR:
            raise ValueError("an Outcome is never an error; raise a BusinessError instead")
        object.__setattr__(self, "issues", tuple(self.issues))

    def envelope(self, trace_id: str) -> Envelope[T]:
        return Envelope[T](
            status=self.status,
            data=self.data,
            issues=list(self.issues),
            case_version=self.case_version,
            profile_version=self.profile_version,
            source_versions=self.source_versions,
            trace_id=trace_id,
        )


def source_versions(
    *,
    rules_snapshot_id: UUID | None = None,
    catalogue_version_id: UUID | None = None,
    tax_dataset_version_id: UUID | None = None,
) -> SourceVersions:
    return SourceVersions(
        rules_snapshot_id=rules_snapshot_id,
        catalogue_version_id=catalogue_version_id,
        tax_dataset_version_id=tax_dataset_version_id,
    )
