"""The one set of effective facts used by a request (decision Q7 of Step 4B).

Within one request, eligibility, readiness and calculation read the same effective profile
and the same pinned release; facts from different snapshots are never combined.

- `actual` (regular calculations): the latest shared actual profile, to which the case copy
  is synchronized first; hypothetical overrides are not applied.
- `case_copy_with_overrides` (hypothetical calculations): the case's saved actual copy
  (`salaryProfileVersionUsed`) with this case's overrides - a fixed baseline.
- `latest_with_overrides` (option listing, 4A decisions A4/A5): the latest actual profile with
  this case's overrides.

An override counts as stated in this conversation (its time is the override's creation), so
the case-relative confirmation rule (G4) never asks to confirm a scenario value.
"""

from enum import StrEnum

from app.business.domain.cases import (
    ComparisonCase,
    HypotheticalOverride,
    profile_fact,
    with_profile_fact,
)
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.contracts.common import RunKind


class FactBasis(StrEnum):
    ACTUAL = "actual"
    CASE_COPY_WITH_OVERRIDES = "case_copy_with_overrides"
    LATEST_WITH_OVERRIDES = "latest_with_overrides"


def basis_for(kind: RunKind) -> FactBasis:
    return FactBasis.ACTUAL if kind is RunKind.REGULAR else FactBasis.CASE_COPY_WITH_OVERRIDES


def with_overrides(
    profile: EmployeeProfile, overrides: tuple[HypotheticalOverride, ...]
) -> EmployeeProfile:
    """A scenario-local profile value; the stored profile and case are never changed."""
    for override in overrides:
        current = profile_fact(profile, override.path)
        scenario: FieldValue[object] = FieldValue(
            value=override.value,
            status=FieldStatus.USABLE,
            source=SourceType.EMPLOYEE,
            source_message_id=override.source_message_id,
            updated_at=override.created_at,
            revision=current.revision,
        )
        profile = with_profile_fact(profile, override.path, scenario)
    return profile


def effective_profile(
    basis: FactBasis, case: ComparisonCase, latest: EmployeeProfile
) -> EmployeeProfile:
    if basis is FactBasis.ACTUAL:
        return latest
    if basis is FactBasis.CASE_COPY_WITH_OVERRIDES:
        return with_overrides(case.actual_facts, case.hypothetical_overrides)
    return with_overrides(latest, case.hypothetical_overrides)
