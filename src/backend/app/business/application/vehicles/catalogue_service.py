"""Catalogue use cases: `list_eligible_options` and `check_eligibility` (Step 4A).

Eligibility is decided only by BL from the effective facts (latest actual profile + this
case's hypothetical overrides, A4/A5) and the pinned release. Nothing here asks for salary.

Listing (A6, A11, A12):
- Items are the eligible and `needs_information` options of the requested categories, in a
  stable order (requested category order, then catalogue order); ineligible vehicles are
  omitted. The waiver item is listed whenever `waiver` is requested, even when ineligible.
- A category with any `needs_information` item is reported in `uncheckedCategories`;
  `scopeComplete` is true only when no category is unchecked.
- Filters: `propulsion` applies to every vehicle; `groups` applies to regular vehicles only
  (the family track is not a ranked group). The waiver has no filterable attributes.
- Each listing is persisted as an immutable result set (ordinal references resolve against
  it). An identical retry (same case version, profile version, release and request) returns
  the already stored set instead of a new one - no public operation ID is needed.
- A closed case cannot be listed (A9); a stale `expectedCaseVersion` is `VERSION_CONFLICT`.
"""

from collections.abc import Mapping
from datetime import datetime
from uuid import uuid4

from app.business.application.errors import (
    InvalidStateTransition,
    NotFoundError,
    VersionConflict,
    translate_errors,
)
from app.business.application.idempotency import canonical_request_hash
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.outcomes import Outcome
from app.business.application.ports.clock import Clock
from app.business.application.ports.reference_data import ReferenceDataRepository
from app.business.application.ports.unit_of_work import (
    BusinessUnitOfWork,
    BusinessUnitOfWorkFactory,
)
from app.business.application.profiles.profile_service import load_profile
from app.business.application.reference import PinnedReference, pin_reference
from app.business.application.vehicles.facts import eligibility_facts, missing_issues
from app.business.application.vehicles.mapping import vehicle_category, vehicle_item, waiver_item
from app.business.bl.eligibility.evaluator import evaluate_vehicle, evaluate_waiver_option
from app.business.domain.arrangements import WAIVER_OPTION_KEY
from app.business.domain.cases import (
    CaseLifecycleStatus,
    ComparisonCase,
    OptionResultSetRecord,
)
from app.business.domain.eligibility import (
    EligibilityDecision,
    EligibilityFacts,
    EligibilityStatus,
)
from app.business.domain.json_values import JsonValue, freeze_object
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.results import MissingFact
from app.business.domain.vehicles import CatalogueVehicle, Track
from app.contracts.catalogue import (
    EligibilityReason,
    EligibilityRequest,
    EligibilityResult,
    ListOptionsRequest,
    ListOptionsResult,
    OptionItem,
)
from app.contracts.catalogue import EligibilityStatus as ContractEligibilityStatus
from app.contracts.common import EnvelopeStatus, Issue, IssueCode, OptionCategory
from app.contracts.tool_context import ToolContext

FINGERPRINT_KEY = "inputFingerprint"
RESULT_SET_SCHEMA = 1
LISTING_BASIS = "latest_with_overrides"
"""Fact basis of `list_eligible_options` (A4/A5); stored with every result set (Step 4B Q7)."""


class CatalogueService:
    def __init__(
        self,
        unit_of_work: BusinessUnitOfWorkFactory,
        policy: AuthorizationPolicy,
        reference_data: ReferenceDataRepository,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._reference_data = reference_data
        self._clock = clock

    # --- list_eligible_options ---------------------------------------------------------

    async def list_eligible_options(
        self, ctx: ToolContext, request: ListOptionsRequest
    ) -> Outcome[ListOptionsResult]:
        with translate_errors():
            async with self._unit_of_work() as uow:
                actor, case = await self._policy.tool_case(uow, ctx)
                if case.lifecycle_status is CaseLifecycleStatus.CLOSED:
                    raise InvalidStateTransition("a closed case cannot list new options")
                if (
                    request.expected_case_version is not None
                    and request.expected_case_version != case.version
                ):
                    raise VersionConflict("the case changed in the meantime; reload first")
                profile = await load_profile(uow, actor.subject_id)
                pinned = await pin_reference(self._reference_data)
                facts = eligibility_facts(profile, case.hypothetical_overrides)
                occupation = await self._occupation(pinned, facts)
                vehicles = await self._reference_data.list_vehicles(
                    pinned.release.catalogue_version_id
                )
                items, unchecked, missing = build_option_items(
                    request, vehicles, facts, occupation, pinned
                )
                stored = await store_option_list(
                    uow,
                    case=case,
                    profile_version=profile.version,
                    basis=LISTING_BASIS,
                    pinned=pinned,
                    request=request,
                    items=items,
                    unchecked=unchecked,
                    now=self._clock.now(),
                )
                await uow.commit()
        issues = missing_issues(missing)
        result = ListOptionsResult(
            result_set_id=stored.result_set_id,
            items=[OptionItem.model_validate(plain_json(item)) for item in stored.items],
            unchecked_categories=[OptionCategory(c) for c in stored.unchecked_categories],
            scope_complete=stored.scope_complete,
            missing_information=issues,
        )
        return Outcome(
            result,
            status=EnvelopeStatus.OK if stored.scope_complete else EnvelopeStatus.PARTIAL,
            issues=tuple(issues),
            case_version=case.version,
            profile_version=profile.version,
            source_versions=pinned.versions,
        )

    # --- check_eligibility -------------------------------------------------------------

    async def check_eligibility(
        self, ctx: ToolContext, request: EligibilityRequest
    ) -> Outcome[EligibilityResult]:
        with translate_errors():
            async with self._unit_of_work() as uow:
                actor, case = await self._policy.tool_case(uow, ctx)
                if request.expected_case_version != case.version:
                    raise VersionConflict("the case changed in the meantime; reload first")
                profile = await load_profile(uow, actor.subject_id)
            pinned = await pin_reference(self._reference_data)
            facts = eligibility_facts(profile, case.hypothetical_overrides)
            occupation = await self._occupation(pinned, facts)
            if request.option_key == WAIVER_OPTION_KEY:
                decision = evaluate_waiver_option(facts, occupation, pinned.rules)
            else:
                catalogue_id = request.option_key.removeprefix("vehicle:")
                vehicle = await self._reference_data.get_vehicle(
                    pinned.release.catalogue_version_id, catalogue_id
                )
                if vehicle is None:
                    raise NotFoundError(
                        "no such catalogue vehicle",
                        issues=[
                            Issue(
                                code=IssueCode.VEHICLE_NOT_FOUND,
                                option_key=request.option_key,
                                reason="not_in_catalogue",
                            )
                        ],
                    )
                decision = evaluate_vehicle(vehicle, facts, occupation, pinned.rules)
        missing = missing_issues(decision.missing)
        result = EligibilityResult(
            option_key=request.option_key,
            status=ContractEligibilityStatus(decision.status.value),
            reasons=[EligibilityReason(r.value) for r in decision.reasons],
            missing_information=missing,
        )
        needs_information = decision.status is EligibilityStatus.NEEDS_INFORMATION
        return Outcome(
            result,
            status=EnvelopeStatus.NEEDS_INFORMATION if needs_information else EnvelopeStatus.OK,
            issues=tuple(missing),
            case_version=case.version,
            profile_version=profile.version,
            source_versions=pinned.versions,
        )

    # --- helpers -----------------------------------------------------------------------

    async def _occupation(
        self, pinned: PinnedReference, facts: EligibilityFacts
    ) -> OccupationPolicy | None:
        field = facts.occupation_id
        if not field.is_usable or field.value is None:
            return None
        return await self._reference_data.get_occupation(
            pinned.release.catalogue_version_id, field.value
        )


async def store_option_list(
    uow: BusinessUnitOfWork,
    *,
    case: ComparisonCase,
    profile_version: int,
    basis: str,
    pinned: PinnedReference,
    request: ListOptionsRequest,
    items: list[OptionItem],
    unchecked: list[OptionCategory],
    now: datetime,
) -> OptionResultSetRecord:
    """Persist (or reuse, A12) the displayed list. The fact basis and profile version are part
    of the fingerprint, so lists built from different facts are never merged (Q7)."""
    request_json = request.model_dump(mode="json", by_alias=True)
    fingerprint = canonical_request_hash(
        {
            "schema": RESULT_SET_SCHEMA,
            "caseVersion": case.version,
            "profileVersion": profile_version,
            "factBasis": basis,
            "releaseId": pinned.release.release_id,
            "request": request_json,
        }
    )
    record = OptionResultSetRecord(
        result_set_id=uuid4(),
        case_id=case.case_id,
        case_version_used=case.version,
        reference_release_id=pinned.release.release_id,
        request=freeze_object(
            {
                **request_json,
                "profileVersionUsed": profile_version,
                "factBasis": basis,
                FINGERPRINT_KEY: fingerprint,
            }
        ),
        items=tuple(freeze_object(item.model_dump(mode="json", by_alias=True)) for item in items),
        unchecked_categories=tuple(c.value for c in unchecked),
        scope_complete=not unchecked,
        created_at=now,
    )
    stored, _created = await uow.option_result_sets.add_or_get_identical(record)
    return stored


def plain_json(value: JsonValue) -> object:
    """Frozen JSON -> plain dicts/lists, for re-validating the stored items."""
    if isinstance(value, Mapping):
        return {key: plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [plain_json(item) for item in value]
    return value


def _wanted(vehicle: CatalogueVehicle, request: ListOptionsRequest) -> bool:
    filters = request.filters
    if filters.propulsion is not None and vehicle.propulsion.value not in {
        p.value for p in filters.propulsion
    }:
        return False
    if (
        filters.groups is not None
        and vehicle.track is Track.REGULAR
        and vehicle.group is not None
        and vehicle.group.value not in {g.value for g in filters.groups}
    ):
        return False
    return True


def build_option_items(
    request: ListOptionsRequest,
    vehicles: tuple[CatalogueVehicle, ...],
    facts: EligibilityFacts,
    occupation: OccupationPolicy | None,
    pinned: PinnedReference,
) -> tuple[list[OptionItem], list[OptionCategory], list[MissingFact]]:
    items: list[OptionItem] = []
    unchecked: list[OptionCategory] = []
    missing: list[MissingFact] = []
    ordered = sorted(vehicles, key=lambda v: _catalogue_order(v.catalogue_id))
    for category in dict.fromkeys(request.categories):
        decisions: list[tuple[CatalogueVehicle | None, EligibilityDecision]] = []
        if category is OptionCategory.WAIVER:
            decisions.append((None, evaluate_waiver_option(facts, occupation, pinned.rules)))
        else:
            for vehicle in ordered:
                if vehicle_category(vehicle) is category and _wanted(vehicle, request):
                    decisions.append(
                        (vehicle, evaluate_vehicle(vehicle, facts, occupation, pinned.rules))
                    )
        incomplete = False
        for subject, decision in decisions:
            missing.extend(decision.missing)
            if decision.status is EligibilityStatus.NEEDS_INFORMATION:
                incomplete = True
            if subject is None:
                items.append(waiver_item(decision, occupation))
            elif decision.status is not EligibilityStatus.INELIGIBLE:
                items.append(vehicle_item(subject, decision, pinned.rules))
        if incomplete:
            unchecked.append(category)
    return items, unchecked, missing


def _catalogue_order(catalogue_id: str) -> tuple[str, int]:
    prefix, _, number = catalogue_id.partition("-")
    return prefix, int(number)
