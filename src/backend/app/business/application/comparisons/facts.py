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

Every basis applies this case's applicable representative facts (B3a) between the actual
values and the overrides - see `representatives/case_answers.py` for the precedence and the
source-conflict definition. A representative fact is stated after the case was created, so it
never requires confirmation either.
"""

from collections.abc import Mapping
from enum import StrEnum

from app.business.application.representatives.case_answers import (
    representative_conflicts,
    with_representative_facts,
)
from app.business.domain.cases import (
    ComparisonCase,
    FactPath,
    HypotheticalOverride,
    profile_fact,
    with_profile_fact,
)
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.business.domain.representatives import RepresentativeFact
from app.contracts.common import Issue, RunKind


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


def _reported(basis: FactBasis, case: ComparisonCase, latest: EmployeeProfile) -> EmployeeProfile:
    return case.actual_facts if basis is FactBasis.CASE_COPY_WITH_OVERRIDES else latest


def effective_profile(
    basis: FactBasis,
    case: ComparisonCase,
    latest: EmployeeProfile,
    facts: Mapping[FactPath, RepresentativeFact] | None = None,
) -> EmployeeProfile:
    """Actual values, then this case's representative facts, then overrides (if the basis
    applies them). `facts` are the case's applicable facts (`load_case_answers`)."""
    profile = with_representative_facts(_reported(basis, case, latest), facts or {})
    if basis is FactBasis.ACTUAL:
        return profile
    return with_overrides(profile, case.hypothetical_overrides)


def fact_conflicts(
    basis: FactBasis,
    case: ComparisonCase,
    latest: EmployeeProfile,
    facts: Mapping[FactPath, RepresentativeFact],
) -> list[Issue]:
    """Representative facts that differ from the employee-reported value of the same basis;
    a field with an applied hypothetical override is not a conflict."""
    overridden = (
        frozenset()
        if basis is FactBasis.ACTUAL
        else frozenset(o.path for o in case.hypothetical_overrides)
    )
    return representative_conflicts(_reported(basis, case, latest), facts, overridden=overridden)
