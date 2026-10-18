"""Freshness of saved calculations: derived on read, never stored (architecture §20).

A run is `stale` when the case changed after it (case version), when the reference release
used is no longer the latest, or - for regular runs only - when the employee's actual profile
changed (latest profile version). Hypothetical runs keep their fixed actual baseline, so a
profile change alone does not make them stale. Historical runs are never modified.
"""

from uuid import UUID

from app.business.domain.cases import ComparisonRunRecord, RunKind
from app.contracts.common import Freshness


def run_freshness(
    run: ComparisonRunRecord,
    *,
    case_version: int,
    latest_profile_version: int,
    latest_release_id: UUID | None,
) -> Freshness:
    stale = (
        run.case_version_used != case_version
        or run.reference_release_id != latest_release_id
        or (run.kind is RunKind.REGULAR and run.profile_version_used != latest_profile_version)
    )
    return Freshness.STALE if stale else Freshness.CURRENT
