"""Domain -> catalogue contract DTOs. Money becomes exact decimal strings (never floats)."""

from decimal import Decimal

from app.business.application.ports.reference_data import TaxRecordDetails
from app.business.domain.arrangements import WAIVER_OPTION_KEY
from app.business.domain.eligibility import EligibilityDecision
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.rules import RulesConfig
from app.business.domain.vehicles import CatalogueVehicle, TaxRecord, Track, VehicleTaxKey
from app.contracts.catalogue import (
    EligibilityReason,
    EligibilityStatus,
    OccupationCandidate,
    OptionItem,
    VehicleCandidate,
)
from app.contracts.common import EntitlementGroup, OptionCategory, Propulsion
from app.contracts.common import Track as ContractTrack
from app.contracts.profile import VehicleTaxKey as ContractTaxKey


def money(value: Decimal) -> str:
    return format(value, "f")


def contract_key(key: VehicleTaxKey) -> ContractTaxKey:
    return ContractTaxKey(
        tax_year=key.tax_year,
        registration_year=key.registration_year,
        vehicle_type_code=key.vehicle_type_code,
        manufacturer_code=key.manufacturer_code,
        model_code=key.model_code,
    )


def domain_key(key: ContractTaxKey) -> VehicleTaxKey:
    return VehicleTaxKey(
        key.tax_year,
        key.registration_year,
        key.vehicle_type_code,
        key.manufacturer_code,
        key.model_code,
    )


def _eligibility(
    decision: EligibilityDecision,
) -> tuple[EligibilityStatus, list[EligibilityReason]]:
    return (
        EligibilityStatus(decision.status.value),
        [EligibilityReason(r.value) for r in decision.reasons],
    )


def vehicle_category(vehicle: CatalogueVehicle) -> OptionCategory:
    return OptionCategory.FAMILY if vehicle.track is Track.FAMILY else OptionCategory.REGULAR


def vehicle_item(
    vehicle: CatalogueVehicle, decision: EligibilityDecision, rules: RulesConfig
) -> OptionItem:
    status, reasons = _eligibility(decision)
    group = vehicle.group
    return OptionItem(
        option_key=vehicle.option_key,
        category=vehicle_category(vehicle),
        catalogue_id=vehicle.catalogue_id,
        track=ContractTrack(vehicle.track.value),
        group=None if group is None else EntitlementGroup(group.value),
        manufacturer_name=vehicle.manufacturer_name,
        commercial_name=vehicle.commercial_name,
        registration_year=vehicle.tax_key.registration_year,
        propulsion=Propulsion(vehicle.propulsion.value),
        list_price=money(vehicle.list_price),
        value_of_use=money(vehicle.catalogue_value_of_use),
        monthly_company_cost=money(vehicle.monthly_company_cost),
        group_budget=None if group is None else money(rules.budgets[group]),
        default_participation=money(vehicle.default_participation),
        eligibility=status,
        reasons=reasons,
    )


def waiver_item(decision: EligibilityDecision, occupation: OccupationPolicy | None) -> OptionItem:
    """The allowance comes from the occupation table, and only when the waiver is eligible."""
    status, reasons = _eligibility(decision)
    allowance = (
        money(occupation.waiver_allowance)
        if occupation is not None and status is EligibilityStatus.ELIGIBLE
        else None
    )
    return OptionItem(
        option_key=WAIVER_OPTION_KEY,
        category=OptionCategory.WAIVER,
        waiver_allowance=allowance,
        eligibility=status,
        reasons=reasons,
    )


def catalogue_candidate(vehicle: CatalogueVehicle) -> VehicleCandidate:
    return VehicleCandidate(
        tax_key=contract_key(vehicle.tax_key),
        manufacturer_name=vehicle.manufacturer_name,
        commercial_name=vehicle.commercial_name,
        model_description=vehicle.model_description,
        value_of_use=money(vehicle.catalogue_value_of_use),
        catalogue_id=vehicle.catalogue_id,
    )


def tax_candidate(details: TaxRecordDetails, catalogue_id: str | None) -> VehicleCandidate:
    return VehicleCandidate(
        tax_key=contract_key(details.record.key),
        manufacturer_name=details.manufacturer_name,
        commercial_name=details.commercial_name,
        model_description=details.model_description,
        value_of_use=money(details.record.value_of_use),
        catalogue_id=catalogue_id,
    )


def occupation_candidate(occupation: OccupationPolicy) -> OccupationCandidate:
    return OccupationCandidate(occupation_id=occupation.occupation_id, name_he=occupation.name_he)


def tax_record_value(record: TaxRecord) -> str:
    return money(record.value_of_use)
