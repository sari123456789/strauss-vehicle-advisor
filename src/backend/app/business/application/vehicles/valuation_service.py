"""Vehicle resolution and official value of use: `resolve_vehicle`, `get_vehicle_tax_value`.

Resolution semantics (decisions A1, A7, A11):
- A catalogue ID resolves to that catalogue vehicle (or `not_found`).
- A complete official five-part key resolves to that tax record (or `not_found`).
- A free-text query never resolves, even with a single candidate: the candidates are returned
  as `ambiguous` for the employee to confirm (a unique candidate may be shown as such), or
  `not_found`. Candidates come from the stored source rows only; nothing is guessed.
  With codes in the query, `TAX_KEY_AMBIGUOUS` when several candidates match or the given
  codes alone (without the name) match several official records - the key is incomplete,
  e.g. a missing vehicle type (T25); otherwise `AMBIGUOUS_INPUT`. At most 20 candidates
  are returned (`truncated` marks more).
- `purpose=alternative` is restricted to catalogue vehicles: a vehicle outside the catalogue
  is never a selectable alternative (`not_found` + `UNSUPPORTED_INPUT`/`not_in_catalogue`).
  The current vehicle may be outside the catalogue (T28).
- The value of use is read only by the complete key from the pinned tax dataset version; an
  unknown catalogue ID or key is `NOT_FOUND` with a `VEHICLE_NOT_FOUND` issue.

Both operations are read-only and authorized through the server-bound tool context.
"""

from app.business.application.errors import NotFoundError, translate_errors
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.outcomes import Outcome
from app.business.application.ports.reference_data import ReferenceDataRepository
from app.business.application.ports.unit_of_work import BusinessUnitOfWorkFactory
from app.business.application.reference import PinnedReference, pin_reference
from app.business.application.vehicles.mapping import (
    catalogue_candidate,
    contract_key,
    domain_key,
    money,
    tax_candidate,
)
from app.business.domain.vehicles import CatalogueVehicle, VehicleTaxKey
from app.contracts.catalogue import (
    ResolvePurpose,
    ResolveStatus,
    ResolveVehicleRequest,
    ResolveVehicleResult,
    TaxValueRequest,
    TaxValueResult,
    VehicleCandidate,
    VehicleQuery,
)
from app.contracts.common import EnvelopeStatus, FieldPath, Issue, IssueCode
from app.contracts.tool_context import ToolContext

MAX_CANDIDATES = 20


class ValuationService:
    def __init__(
        self,
        unit_of_work: BusinessUnitOfWorkFactory,
        policy: AuthorizationPolicy,
        reference_data: ReferenceDataRepository,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._reference_data = reference_data

    async def _authorize(self, ctx: ToolContext) -> int:
        async with self._unit_of_work() as uow:
            _actor, case = await self._policy.tool_case(uow, ctx)
            return case.version

    # --- resolve_vehicle ---------------------------------------------------------------

    async def resolve_vehicle(
        self, ctx: ToolContext, request: ResolveVehicleRequest
    ) -> Outcome[ResolveVehicleResult]:
        with translate_errors():
            case_version = await self._authorize(ctx)
            pinned = await pin_reference(self._reference_data)
            catalogue = await self._reference_data.list_vehicles(
                pinned.release.catalogue_version_id
            )
            field = FieldPath.CURRENT_VEHICLE if request.purpose is ResolvePurpose.CURRENT else None
            if request.catalogue_id is not None:
                vehicle = next(
                    (v for v in catalogue if v.catalogue_id == request.catalogue_id), None
                )
                result, issues = (
                    _not_found(field)
                    if vehicle is None
                    else (_resolved([catalogue_candidate(vehicle)]), [])
                )
            elif request.tax_key is not None:
                result, issues = await self._by_key(
                    pinned, catalogue, domain_key(request.tax_key), request.purpose, field
                )
            else:
                assert request.query is not None  # exactly one selector (contract)
                result, issues = await self._by_query(
                    pinned, catalogue, request.query, request.purpose, field
                )
        status = (
            EnvelopeStatus.OK
            if result.status is ResolveStatus.RESOLVED
            else EnvelopeStatus.NEEDS_INFORMATION
        )
        return Outcome(
            result,
            status=status,
            issues=tuple(issues),
            case_version=case_version,
            source_versions=pinned.versions,
        )

    async def _by_key(
        self,
        pinned: PinnedReference,
        catalogue: tuple[CatalogueVehicle, ...],
        key: VehicleTaxKey,
        purpose: ResolvePurpose,
        field: FieldPath | None,
    ) -> tuple[ResolveVehicleResult, list[Issue]]:
        details = await self._reference_data.get_tax_record_details(
            pinned.release.tax_dataset_version_id, key
        )
        if details is None:
            return _not_found(field)
        catalogue_id = _catalogue_ids(catalogue).get(key)
        if purpose is ResolvePurpose.ALTERNATIVE and catalogue_id is None:
            return _not_in_catalogue()
        return _resolved([tax_candidate(details, catalogue_id)]), []

    async def _by_query(
        self,
        pinned: PinnedReference,
        catalogue: tuple[CatalogueVehicle, ...],
        query: VehicleQuery,
        purpose: ResolvePurpose,
        field: FieldPath | None,
    ) -> tuple[ResolveVehicleResult, list[Issue]]:
        if purpose is ResolvePurpose.ALTERNATIVE:
            candidates = [catalogue_candidate(v) for v in catalogue if _matches(v, query)]
            truncated = len(candidates) > MAX_CANDIDATES
        else:
            found = await self._reference_data.find_tax_records(
                pinned.release.tax_dataset_version_id,
                commercial_name=query.commercial_name.strip(),
                registration_year=query.registration_year,
                manufacturer_code=query.manufacturer_code,
                model_code=query.model_code,
                limit=MAX_CANDIDATES + 1,
            )
            ids = _catalogue_ids(catalogue)
            candidates = [tax_candidate(d, ids.get(d.record.key)) for d in found]
            truncated = len(candidates) > MAX_CANDIDATES
        if not candidates:
            return _not_found(field)
        has_codes = query.manufacturer_code is not None or query.model_code is not None
        key_ambiguous = has_codes and (
            len(candidates) > 1 or await self._partial_key_is_ambiguous(pinned, query)
        )
        code = IssueCode.TAX_KEY_AMBIGUOUS if key_ambiguous else IssueCode.AMBIGUOUS_INPUT
        reason = "confirm_candidate" if len(candidates) == 1 else "several_candidates"
        result = ResolveVehicleResult(
            status=ResolveStatus.AMBIGUOUS,
            candidates=candidates[:MAX_CANDIDATES],
            truncated=truncated,
        )
        return result, [Issue(code=code, field=field, reason=reason)]

    async def _partial_key_is_ambiguous(self, pinned: PinnedReference, query: VehicleQuery) -> bool:
        """The given codes (without the name) match several official records, e.g. the same
        manufacturer/model codes and year under two vehicle types (T25)."""
        same_codes = await self._reference_data.find_tax_records(
            pinned.release.tax_dataset_version_id,
            commercial_name="",
            registration_year=query.registration_year,
            manufacturer_code=query.manufacturer_code,
            model_code=query.model_code,
            limit=2,
        )
        return len(same_codes) > 1

    # --- get_vehicle_tax_value ---------------------------------------------------------

    async def get_vehicle_tax_value(
        self, ctx: ToolContext, request: TaxValueRequest
    ) -> Outcome[TaxValueResult]:
        with translate_errors():
            case_version = await self._authorize(ctx)
            pinned = await pin_reference(self._reference_data)
            catalogue = await self._reference_data.list_vehicles(
                pinned.release.catalogue_version_id
            )
            if request.catalogue_id is not None:
                vehicle = next(
                    (v for v in catalogue if v.catalogue_id == request.catalogue_id), None
                )
                if vehicle is None:
                    raise _vehicle_not_found()
                key = vehicle.tax_key
            else:
                assert request.tax_key is not None  # exactly one selector (contract)
                key = domain_key(request.tax_key)
            record = await self._reference_data.get_value_of_use(
                pinned.release.tax_dataset_version_id, key
            )
            if record is None:
                raise _vehicle_not_found()
        result = TaxValueResult(
            value_of_use=money(record.value_of_use),
            tax_key=contract_key(record.key),
            tax_dataset_version_id=pinned.release.tax_dataset_version_id,
            effective_date=record.effective_date,
            catalogue_id=_catalogue_ids(catalogue).get(record.key),
        )
        return Outcome(result, case_version=case_version, source_versions=pinned.versions)


def _catalogue_ids(catalogue: tuple[CatalogueVehicle, ...]) -> dict[VehicleTaxKey, str]:
    return {v.tax_key: v.catalogue_id for v in catalogue}


def _matches(vehicle: CatalogueVehicle, query: VehicleQuery) -> bool:
    key = vehicle.tax_key
    return (
        query.commercial_name.strip().casefold() in vehicle.commercial_name.strip().casefold()
        and query.registration_year in (None, key.registration_year)
        and query.manufacturer_code in (None, key.manufacturer_code)
        and query.model_code in (None, key.model_code)
    )


def _resolved(candidates: list[VehicleCandidate]) -> ResolveVehicleResult:
    return ResolveVehicleResult(status=ResolveStatus.RESOLVED, candidates=candidates)


def _not_found(field: FieldPath | None) -> tuple[ResolveVehicleResult, list[Issue]]:
    return ResolveVehicleResult(status=ResolveStatus.NOT_FOUND), [
        Issue(code=IssueCode.VEHICLE_NOT_FOUND, field=field)
    ]


def _not_in_catalogue() -> tuple[ResolveVehicleResult, list[Issue]]:
    return ResolveVehicleResult(status=ResolveStatus.NOT_FOUND), [
        Issue(code=IssueCode.UNSUPPORTED_INPUT, reason="not_in_catalogue")
    ]


def _vehicle_not_found() -> NotFoundError:
    return NotFoundError(
        "no such vehicle in the pinned reference data",
        issues=[Issue(code=IssueCode.VEHICLE_NOT_FOUND)],
    )
