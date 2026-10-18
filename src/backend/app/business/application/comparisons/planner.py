"""Scenario planning shared by readiness and comparison (Step 4B).

From one effective profile and one pinned release, every selected option becomes either a
BL scenario specification (eligible), an `IneligibleSpec` (business rejection), or a plan
blocked by missing/unclear eligibility facts or an open representative request. The current
arrangement becomes a `BaselineSpec` plus any blockers. Readiness and comparison both use
this module, so they always agree on which scenarios are calculable.

Baseline reliability (decision Q2): a baseline is not calculated - and no delta is shown -
when a material current-arrangement fact is not found, contradicts the catalogue
(`SOURCE_CONFLICT`), is unclear, or is a usable value that has not been stated or confirmed
in this conversation (case-relative rule G4, reason `confirmation_required`). Only facts
material to the reported arrangement kind are checked; salary facts never need confirmation.

Current vehicle (Q3): a catalogue vehicle supplies track and propulsion (a different
reported propulsion is a conflict); a vehicle outside the catalogue is regular-track and uses
the reported propulsion (never inferred, D6); a family deduction reported for it is unclear.
Pending requests (Q6): an open request tied to an option blocks only that option; an open
request about the current vehicle blocks only the baseline.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.business.application.errors import NotFoundError
from app.business.application.ports.reference_data import ReferenceDataRepository
from app.business.application.reference import PinnedReference
from app.business.application.vehicles.facts import eligibility_facts
from app.business.bl.eligibility.evaluator import evaluate_vehicle, evaluate_waiver_option
from app.business.domain.arrangements import (
    WAIVER_OPTION_KEY,
    AlternativeSpec,
    AlternativeVehicleSpec,
    ArrangementKind,
    BaselineSpec,
    CurrentAllowanceSpec,
    CurrentCompanyCarSpec,
    CurrentNoCarSpec,
    IneligibleSpec,
    WaiverSpec,
)
from app.business.domain.eligibility import (
    EligibilityDecision,
    EligibilityReason,
    EligibilityStatus,
)
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.business.domain.representatives import (
    RepresentativeFieldCode,
    RepresentativeRequest,
)
from app.business.domain.vehicles import CatalogueVehicle, Propulsion, Track, VehicleTaxKey
from app.contracts.common import FieldPath, Issue, IssueCode

PARTICIPATION_SOURCE = "catalogue_default"
VEHICLE_PREFIX = "vehicle:"
BASELINE_REQUEST_FIELDS = frozenset(
    {RepresentativeFieldCode.VEHICLE_PROPULSION, RepresentativeFieldCode.VEHICLE_TAX_KEY}
)


@dataclass(frozen=True, slots=True)
class OptionPlan:
    option_key: str
    decision: EligibilityDecision
    spec: AlternativeSpec | None
    """BL input; None when blocked before calculation (unclear eligibility or pending)."""
    blockers: tuple[Issue, ...] = ()
    vehicle: CatalogueVehicle | None = None
    occupation: OccupationPolicy | None = None


@dataclass(frozen=True, slots=True)
class BaselinePlan:
    spec: BaselineSpec | None
    """None when the arrangement kind itself is unknown."""
    blockers: tuple[Issue, ...] = ()
    """Non-empty: the baseline is not calculated and no delta is shown."""
    catalogue_id: str | None = None
    tax_key: VehicleTaxKey | None = None

    @property
    def calculable(self) -> bool:
        return self.spec is not None and not self.blockers


@dataclass(frozen=True, slots=True)
class Plan:
    profile: EmployeeProfile
    options: tuple[OptionPlan, ...]
    baseline: BaselinePlan | None
    pending: tuple[RepresentativeRequest, ...]


def _usable[T](value: T, source: SourceType) -> FieldValue[T]:
    return FieldValue.usable(value, source)


def _eligibility_issues(decision: EligibilityDecision) -> tuple[Issue, ...]:
    """Unclear eligibility without a missing fact (A3 conflict, occupation not in table)."""
    key = decision.option_key
    issues: list[Issue] = []
    if EligibilityReason.OCCUPATION_GROUP_CONFLICT in decision.reasons:
        issues.append(
            Issue(
                code=IssueCode.AMBIGUOUS_INPUT,
                field=FieldPath.ENTITLEMENT_GROUP,
                option_key=key,
                reason=EligibilityReason.OCCUPATION_GROUP_CONFLICT.value,
            )
        )
    if EligibilityReason.OCCUPATION_NOT_IN_TABLE in decision.reasons:
        issues.append(
            Issue(
                code=IssueCode.AMBIGUOUS_INPUT,
                field=FieldPath.OCCUPATION_ID,
                option_key=key,
                reason=EligibilityReason.OCCUPATION_NOT_IN_TABLE.value,
            )
        )
    return tuple(issues)


def unknown_option(option_key: str) -> NotFoundError:
    return NotFoundError(
        "unknown option",
        issues=[
            Issue(
                code=IssueCode.VEHICLE_NOT_FOUND, option_key=option_key, reason="not_in_catalogue"
            )
        ],
    )


class ScenarioPlanner:
    def __init__(self, reference_data: ReferenceDataRepository) -> None:
        self._reference_data = reference_data

    async def occupation(
        self, pinned: PinnedReference, profile: EmployeeProfile
    ) -> OccupationPolicy | None:
        field = profile.occupation_id
        if not field.is_usable or field.value is None:
            return None
        return await self._reference_data.get_occupation(
            pinned.release.catalogue_version_id, field.value
        )

    async def plan(
        self,
        pinned: PinnedReference,
        profile: EmployeeProfile,
        option_keys: Sequence[str],
        *,
        include_baseline: bool,
        confirmation_required: Iterable[FieldPath],
        open_requests: Sequence[RepresentativeRequest],
    ) -> Plan:
        catalogue = await self._reference_data.list_vehicles(pinned.release.catalogue_version_id)
        by_id = {v.catalogue_id: v for v in catalogue}
        occupation = await self.occupation(pinned, profile)
        options = [
            await self._option(pinned, profile, key, by_id, occupation, open_requests)
            for key in option_keys
        ]
        baseline = None
        if include_baseline:
            baseline = await self._baseline(
                pinned, profile, catalogue, set(confirmation_required), open_requests
            )
        pending = tuple(
            r for r in open_requests if r.option_key is None or r.option_key in set(option_keys)
        )
        return Plan(profile, tuple(options), baseline, pending)

    # --- alternatives ------------------------------------------------------------------

    async def _option(
        self,
        pinned: PinnedReference,
        profile: EmployeeProfile,
        option_key: str,
        catalogue: dict[str, CatalogueVehicle],
        occupation: OccupationPolicy | None,
        open_requests: Sequence[RepresentativeRequest],
    ) -> OptionPlan:
        facts = eligibility_facts(profile)
        rules = pinned.rules
        vehicle: CatalogueVehicle | None = None
        if option_key == WAIVER_OPTION_KEY:
            decision = evaluate_waiver_option(facts, occupation, rules)
        elif option_key.startswith(VEHICLE_PREFIX):
            vehicle = catalogue.get(option_key.removeprefix(VEHICLE_PREFIX))
            if vehicle is None:
                raise unknown_option(option_key)  # Q4: never an invented vehicle
            decision = evaluate_vehicle(vehicle, facts, occupation, rules)
        else:
            raise unknown_option(option_key)

        if decision.status is EligibilityStatus.INELIGIBLE:
            reasons = tuple(r.value for r in decision.reasons)
            return OptionPlan(
                option_key, decision, IneligibleSpec(option_key, reasons), (), vehicle
            )
        if decision.status is EligibilityStatus.NEEDS_INFORMATION:
            return OptionPlan(
                option_key, decision, None, _eligibility_issues(decision), vehicle, occupation
            )
        pending = tuple(
            Issue(
                code=IssueCode.PENDING_REPRESENTATIVE,
                option_key=option_key,
                request_id=r.request_id,
                reason=r.field_code.value,
            )
            for r in open_requests
            if r.option_key == option_key
        )
        if pending:
            return OptionPlan(option_key, decision, None, pending, vehicle, occupation)
        if vehicle is None:
            assert occupation is not None  # an eligible waiver has a table entry
            spec: AlternativeSpec = WaiverSpec(
                allowance=_usable(occupation.waiver_allowance, SourceType.CATALOGUE)
            )
        else:
            spec = await self._vehicle_spec(pinned, profile, vehicle)
        return OptionPlan(option_key, decision, spec, (), vehicle, occupation)

    async def _vehicle_spec(
        self, pinned: PinnedReference, profile: EmployeeProfile, vehicle: CatalogueVehicle
    ) -> AlternativeVehicleSpec:
        record = await self._reference_data.get_value_of_use(
            pinned.release.tax_dataset_version_id, vehicle.tax_key
        )
        value_of_use: FieldValue[Decimal] = (
            FieldValue() if record is None else _usable(record.value_of_use, SourceType.TAX_DATASET)
        )
        group = profile.entitlement_group
        budget: FieldValue[Decimal] = FieldValue()
        if group.is_usable and group.value is not None:
            budget = _usable(pinned.rules.budgets[group.value], SourceType.RULES)
        return AlternativeVehicleSpec(
            option_key=vehicle.option_key,
            catalogue_id=vehicle.catalogue_id,
            track=vehicle.track,
            propulsion=vehicle.propulsion,
            value_of_use=value_of_use,
            participation=_usable(vehicle.default_participation, SourceType.CATALOGUE),
            monthly_company_cost=vehicle.monthly_company_cost,
            group_budget=budget,
            participation_source=PARTICIPATION_SOURCE,
        )

    # --- current arrangement -----------------------------------------------------------

    async def _baseline(
        self,
        pinned: PinnedReference,
        profile: EmployeeProfile,
        catalogue: tuple[CatalogueVehicle, ...],
        confirmation_required: set[FieldPath],
        open_requests: Sequence[RepresentativeRequest],
    ) -> BaselinePlan:
        current = profile.current_arrangement
        blockers: list[Issue] = [
            Issue(
                code=IssueCode.PENDING_REPRESENTATIVE,
                request_id=r.request_id,
                field=FieldPath(r.field_code.value),
                reason=r.field_code.value,
            )
            for r in open_requests
            if r.option_key is None and r.field_code in BASELINE_REQUEST_FIELDS
        ]
        kind = current.kind
        if not kind.is_usable or kind.value is None:
            code = (
                IssueCode.AMBIGUOUS_INPUT
                if kind.status is FieldStatus.NEEDS_CLARIFICATION
                else IssueCode.MISSING_INPUT
            )
            blockers.append(
                Issue(code=code, field=FieldPath.CURRENT_KIND, reason=kind.status.value)
            )
            return BaselinePlan(None, tuple(blockers))
        material = {FieldPath.CURRENT_KIND}
        catalogue_id: str | None = None
        tax_key: VehicleTaxKey | None = None
        spec: BaselineSpec
        if kind.value is ArrangementKind.NO_CAR_NO_ALLOWANCE:
            spec = CurrentNoCarSpec()
        elif kind.value is ArrangementKind.ALLOWANCE:
            material.add(FieldPath.CURRENT_ALLOWANCE)
            spec = CurrentAllowanceSpec(allowance=current.allowance)
        else:
            material |= {FieldPath.CURRENT_VEHICLE, FieldPath.CURRENT_PARTICIPATION}
            spec, catalogue_id, tax_key, vehicle_issues, extra = await self._company_car(
                pinned, profile, catalogue
            )
            blockers.extend(vehicle_issues)
            material |= extra
        blockers.extend(
            Issue(code=IssueCode.AMBIGUOUS_INPUT, field=path, reason="confirmation_required")
            for path in FieldPath
            if path in material and path in confirmation_required
        )
        return BaselinePlan(spec, tuple(blockers), catalogue_id, tax_key)

    async def _company_car(
        self,
        pinned: PinnedReference,
        profile: EmployeeProfile,
        catalogue: tuple[CatalogueVehicle, ...],
    ) -> tuple[
        CurrentCompanyCarSpec, str | None, VehicleTaxKey | None, list[Issue], set[FieldPath]
    ]:
        current = profile.current_arrangement
        issues: list[Issue] = []
        extra: set[FieldPath] = set()
        reported: FieldValue[Propulsion] = current.propulsion
        value_of_use: FieldValue[Decimal] = FieldValue()
        track = Track.REGULAR
        propulsion = reported
        vehicle: CatalogueVehicle | None = None
        key: VehicleTaxKey | None = None
        ref = current.vehicle.value if current.vehicle.is_usable else None
        if ref is not None:
            if ref.catalogue_id is not None:
                vehicle = next((v for v in catalogue if v.catalogue_id == ref.catalogue_id), None)
                key = None if vehicle is None else vehicle.tax_key
            else:
                key = ref.tax_key
                vehicle = next((v for v in catalogue if v.tax_key == key), None)
            record = (
                None
                if key is None
                else await self._reference_data.get_value_of_use(
                    pinned.release.tax_dataset_version_id, key
                )
            )
            if record is None:
                issues.append(
                    Issue(code=IssueCode.VEHICLE_NOT_FOUND, field=FieldPath.CURRENT_VEHICLE)
                )
            else:
                value_of_use = _usable(record.value_of_use, SourceType.TAX_DATASET)
        if vehicle is not None:
            track = vehicle.track
            propulsion = _usable(vehicle.propulsion, SourceType.CATALOGUE)
            if reported.is_usable and reported.value is not vehicle.propulsion:
                issues.append(
                    Issue(
                        code=IssueCode.SOURCE_CONFLICT,
                        field=FieldPath.CURRENT_PROPULSION,
                        reason="catalogue_propulsion_differs",
                    )
                )
        else:
            extra.add(FieldPath.CURRENT_PROPULSION)  # reported, never inferred (D6)
            if ref is not None and current.family_deduction.is_usable:
                issues.append(
                    Issue(
                        code=IssueCode.AMBIGUOUS_INPUT,
                        field=FieldPath.CURRENT_FAMILY_DEDUCTION,
                        reason="family_deduction_outside_catalogue",
                    )
                )
        if track is Track.FAMILY:
            extra.add(FieldPath.CURRENT_FAMILY_DEDUCTION_INCLUDED)
            included = current.family_deduction_included_in_participation
            if included.is_usable and included.value is False:
                extra.add(FieldPath.CURRENT_FAMILY_DEDUCTION)  # deducted separately (T57)
        spec = CurrentCompanyCarSpec(
            track=track,
            propulsion=propulsion,
            value_of_use=value_of_use,
            participation=current.participation,
            family_deduction=current.family_deduction,
            family_deduction_included_in_participation=(
                current.family_deduction_included_in_participation
            ),
            catalogue_id=None if vehicle is None else vehicle.catalogue_id,
        )
        catalogue_id = None if vehicle is None else vehicle.catalogue_id
        return spec, catalogue_id, key, issues, extra


def as_json_key(key: VehicleTaxKey | None) -> dict[str, Any] | None:
    if key is None:
        return None
    return {
        "taxYear": key.tax_year,
        "registrationYear": key.registration_year,
        "vehicleTypeCode": key.vehicle_type_code,
        "manufacturerCode": key.manufacturer_code,
        "modelCode": key.model_code,
    }
