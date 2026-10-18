"""`Selection` -> ordered option keys (decisions Q4, Q5, Q7, Q12).

- `explicit`: the given keys; unknown vehicles are rejected by the planner (`NOT_FOUND`).
- `result_set`: a stored list of this case only (else `NOT_FOUND`); it contributes only the
  option keys and their order. It must come from the pinned release (else `VERSION_CONFLICT`
  with `STALE_REVISION`). Eligibility is always re-evaluated with this request's facts; a list
  built from other facts is reported (`STALE_REVISION` / `result_set_built_from_other_facts`),
  never merged.
- `all_eligible`: the 4A listing over this request's facts (default categories regular and
  family; the waiver only when requested). A comparison persists the list (A12 dedupe; the
  fact basis is part of its fingerprint); readiness only computes it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.business.application.comparisons.facts import FactBasis
from app.business.application.errors import NotFoundError, VersionConflict
from app.business.application.ports.reference_data import ReferenceDataRepository
from app.business.application.ports.unit_of_work import BusinessUnitOfWork
from app.business.application.reference import PinnedReference
from app.business.application.vehicles.catalogue_service import (
    build_option_items,
    store_option_list,
)
from app.business.application.vehicles.facts import eligibility_facts
from app.business.domain.cases import ComparisonCase
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.occupations import OccupationPolicy
from app.contracts.catalogue import ListOptionsRequest, OptionFilters
from app.contracts.common import Issue, IssueCode, OptionCategory
from app.contracts.readiness import Selection, SelectionMode

DEFAULT_CATEGORIES = (OptionCategory.REGULAR, OptionCategory.FAMILY)
OTHER_FACTS = "result_set_built_from_other_facts"


@dataclass(frozen=True, slots=True)
class ResolvedSelection:
    option_keys: list[str]
    result_set_id: UUID | None = None
    listed_eligibility: dict[str, str] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)


async def resolve_selection(
    selection: Selection,
    *,
    uow: BusinessUnitOfWork,
    reference_data: ReferenceDataRepository,
    case: ComparisonCase,
    pinned: PinnedReference,
    profile: EmployeeProfile,
    occupation: OccupationPolicy | None,
    basis: FactBasis,
    persist: bool,
    now: datetime,
) -> ResolvedSelection:
    if selection.mode is SelectionMode.EXPLICIT:
        assert selection.option_keys is not None  # contract
        return ResolvedSelection(list(selection.option_keys))
    if selection.mode is SelectionMode.RESULT_SET:
        assert selection.result_set_id is not None  # contract
        stored = await uow.option_result_sets.get(case.case_id, selection.result_set_id)
        if stored is None:
            raise NotFoundError()
        if stored.reference_release_id != pinned.release.release_id:
            raise VersionConflict(
                "the option list was built from older reference data; list again",
                issues=[Issue(code=IssueCode.STALE_REVISION, reason="result_set_release_changed")],
            )
        keys: list[str] = []
        listed: dict[str, str] = {}
        for item in stored.items:
            if not isinstance(item, Mapping):
                raise TypeError("stored option items are JSON objects")
            key = str(item["optionKey"])
            keys.append(key)
            listed[key] = str(item["eligibility"])
        # Same facts: same case version (same overrides) and profile version (a case copy at a
        # version equals the profile at that version), and overrides applied in both or in
        # neither - or the case has none, so every basis reads the same values.
        listed_applies = stored.request.get("factBasis") != FactBasis.ACTUAL.value
        same_facts = (
            stored.case_version_used == case.version
            and stored.request.get("profileVersionUsed") == profile.version
            and (
                listed_applies == (basis is not FactBasis.ACTUAL) or not case.hypothetical_overrides
            )
        )
        issues = [] if same_facts else [Issue(code=IssueCode.STALE_REVISION, reason=OTHER_FACTS)]
        return ResolvedSelection(keys, stored.result_set_id, listed, issues)

    request = ListOptionsRequest(
        categories=list(selection.categories or DEFAULT_CATEGORIES),
        filters=selection.filters or OptionFilters(),
    )
    vehicles = await reference_data.list_vehicles(pinned.release.catalogue_version_id)
    items, unchecked, _missing = build_option_items(
        request, vehicles, eligibility_facts(profile), occupation, pinned
    )
    keys = [item.option_key for item in items]
    if not persist:
        return ResolvedSelection(keys)
    stored_set = await store_option_list(
        uow,
        case=case,
        profile_version=profile.version,
        basis=basis.value,
        pinned=pinned,
        request=request,
        items=items,
        unchecked=unchecked,
        now=now,
    )
    return ResolvedSelection(keys, stored_set.result_set_id)
