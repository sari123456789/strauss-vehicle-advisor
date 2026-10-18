"""Catalogue, vehicle resolution, tax value and eligibility contracts. STATUS: PROVISIONAL.

Eligibility is decided by deterministic server code only. Vehicles outside the catalogue are
never selectable alternatives; a partial tax key never auto-selects a record.
"""

from datetime import date
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.contracts.common import (
    CatalogueIdStr,
    ContractModel,
    EntitlementGroup,
    Issue,
    NonNegativeMoneyStr,
    OccupationIdStr,
    OptionCategory,
    OptionKeyStr,
    Propulsion,
    Track,
)
from app.contracts.profile import VehicleTaxKey


class EligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_INFORMATION = "needs_information"


class EligibilityReason(StrEnum):
    GROUP_OWN = "group_own"
    GROUP_LOWER = "group_lower"
    GROUP_HIGHER = "group_higher_than_entitlement"
    GROUP_UNKNOWN = "group_unknown"
    FAMILY_CHILDREN_MET = "family_children_met"
    FAMILY_CHILDREN_NOT_MET = "family_children_not_met"
    FAMILY_CHILDREN_UNKNOWN = "family_children_unknown"
    WAIVER_ALLOWED = "waiver_allowed_by_occupation"
    WAIVER_MANDATORY_CAR = "waiver_not_allowed_mandatory_car"
    OCCUPATION_UNKNOWN = "occupation_unknown"
    OCCUPATION_NOT_IN_TABLE = "occupation_not_in_table"
    OCCUPATION_GROUP_CONFLICT = "occupation_group_conflict"


class OptionFilters(ContractModel):
    propulsion: list[Propulsion] | None = None
    groups: list[EntitlementGroup] | None = None


class ListOptionsRequest(ContractModel):
    categories: list[OptionCategory] = Field(
        default_factory=lambda: [OptionCategory.REGULAR, OptionCategory.FAMILY], min_length=1
    )
    """Waiver is included only when explicitly requested (non-car arrangements)."""
    filters: OptionFilters = Field(default_factory=OptionFilters)
    expected_case_version: int | None = Field(default=None, ge=0)


class OptionItem(ContractModel):
    option_key: OptionKeyStr
    category: OptionCategory
    catalogue_id: CatalogueIdStr | None = None
    track: Track | None = None
    group: EntitlementGroup | None = None
    manufacturer_name: str | None = None
    commercial_name: str | None = None
    registration_year: str | None = None
    propulsion: Propulsion | None = None
    list_price: NonNegativeMoneyStr | None = None
    value_of_use: NonNegativeMoneyStr | None = None
    monthly_company_cost: NonNegativeMoneyStr | None = None
    group_budget: NonNegativeMoneyStr | None = None
    default_participation: NonNegativeMoneyStr | None = None
    waiver_allowance: NonNegativeMoneyStr | None = None
    eligibility: EligibilityStatus
    reasons: list[EligibilityReason] = Field(default_factory=list)


class ListOptionsResult(ContractModel):
    result_set_id: UUID
    items: list[OptionItem]
    """Ordered exactly as displayed; ordinal references resolve against this order."""
    unchecked_categories: list[OptionCategory] = Field(default_factory=list)
    scope_complete: bool
    missing_information: list[Issue] = Field(default_factory=list)


class ResolvePurpose(StrEnum):
    CURRENT = "current"
    ALTERNATIVE = "alternative"


class ResolveStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class VehicleQuery(ContractModel):
    commercial_name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    registration_year: Annotated[str, StringConstraints(pattern=r"^\d{4}$")] | None = None
    manufacturer_code: Annotated[str, StringConstraints(pattern=r"^\d{5}$")] | None = None
    model_code: Annotated[str, StringConstraints(pattern=r"^\d{5}$")] | None = None


class ResolveVehicleRequest(ContractModel):
    purpose: ResolvePurpose
    catalogue_id: CatalogueIdStr | None = None
    tax_key: VehicleTaxKey | None = None
    query: VehicleQuery | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        given = [x for x in (self.catalogue_id, self.tax_key, self.query) if x is not None]
        if len(given) != 1:
            raise ValueError("provide exactly one of catalogueId, taxKey or query")
        return self


class VehicleCandidate(ContractModel):
    tax_key: VehicleTaxKey
    manufacturer_name: str
    commercial_name: str
    model_description: str
    value_of_use: NonNegativeMoneyStr
    catalogue_id: CatalogueIdStr | None = None


class ResolveVehicleResult(ContractModel):
    status: ResolveStatus
    candidates: list[VehicleCandidate] = Field(default_factory=list, max_length=20)
    truncated: bool = False


class TaxValueRequest(ContractModel):
    tax_key: VehicleTaxKey | None = None
    catalogue_id: CatalogueIdStr | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.tax_key is None) == (self.catalogue_id is None):
            raise ValueError("provide exactly one of taxKey or catalogueId")
        return self


class TaxValueResult(ContractModel):
    value_of_use: NonNegativeMoneyStr
    tax_key: VehicleTaxKey
    tax_dataset_version_id: UUID
    effective_date: date | None = None
    catalogue_id: CatalogueIdStr | None = None


class EligibilityRequest(ContractModel):
    expected_case_version: int = Field(ge=0)
    option_key: OptionKeyStr


class EligibilityResult(ContractModel):
    option_key: OptionKeyStr
    status: EligibilityStatus
    reasons: list[EligibilityReason] = Field(default_factory=list)
    missing_information: list[Issue] = Field(default_factory=list)


class ResolveOccupationRequest(ContractModel):
    """`resolve_occupation` (G2). Without a query, every official occupation is returned."""

    query: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None


class OccupationCandidate(ContractModel):
    occupation_id: OccupationIdStr
    name_he: str
    """Official Hebrew occupation name from the occupation table."""


class ResolveOccupationResult(ContractModel):
    """`resolved` only for an official ID or an exact normalized official name; otherwise
    candidates for clarification (`ambiguous`) or `not_found`. Never a guessed or new ID."""

    status: ResolveStatus
    candidates: list[OccupationCandidate] = Field(default_factory=list, max_length=20)
