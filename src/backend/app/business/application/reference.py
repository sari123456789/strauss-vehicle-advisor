"""The pinned reference set for one call: the latest published release and its rules."""

from dataclasses import dataclass

from app.business.application.errors import InfrastructureUnavailable
from app.business.application.outcomes import source_versions
from app.business.application.ports.reference_data import PublishedRelease, ReferenceDataRepository
from app.business.domain.rules import RulesConfig
from app.contracts.common import SourceVersions


@dataclass(frozen=True, slots=True)
class PinnedReference:
    release: PublishedRelease
    rules: RulesConfig

    @property
    def versions(self) -> SourceVersions:
        return source_versions(
            rules_snapshot_id=self.release.rules_snapshot_id,
            catalogue_version_id=self.release.catalogue_version_id,
            tax_dataset_version_id=self.release.tax_dataset_version_id,
        )


async def pin_reference(reference_data: ReferenceDataRepository) -> PinnedReference:
    """Components are never chosen independently: everything comes from one release."""
    release = await reference_data.latest_release()
    rules = None if release is None else await reference_data.get_rules(release.rules_snapshot_id)
    if release is None or rules is None:
        raise InfrastructureUnavailable("reference data has not been published")
    return PinnedReference(release, rules)
