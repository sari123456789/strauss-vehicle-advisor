"""Shared actual EmployeeProfile view. STATUS: PROVISIONAL.

Values are the employee's last-reported facts, not employer-verified data. A missing value is
`value=null, status=missing` - never zero.
"""

from datetime import datetime
from typing import Annotated, Generic, Self, TypeVar
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.contracts.common import (
    ArrangementKind,
    CatalogueIdStr,
    ContractModel,
    CreditPointsStr,
    EntitlementGroup,
    FieldStatus,
    NonNegativeMoneyStr,
    OccupationIdStr,
    Propulsion,
    RateStr,
    SourceType,
)

V = TypeVar("V")


class ProfileField(ContractModel, Generic[V]):
    value: V | None = None
    status: FieldStatus
    source: SourceType | None = None
    source_message_id: UUID | None = None
    updated_at: datetime | None = None
    revision: int = Field(default=0, ge=0)
    last_confirmed_at: datetime | None = None

    @model_validator(mode="after")
    def _value_matches_status(self) -> Self:
        if self.status is FieldStatus.MISSING and self.value is not None:
            raise ValueError("status=missing requires value=null")
        if self.status is FieldStatus.USABLE and self.value is None:
            raise ValueError("status=usable requires a value")
        return self


class VehicleTaxKey(ContractModel):
    """Official five-part key: (שנת מס, שנת רישום, קוד סוג רכב, קוד תוצר, קוד דגם).

    Codes are strings and keep leading zeros exactly as in the source dataset.
    """

    tax_year: Annotated[str, StringConstraints(pattern=r"^\d{4}$")]
    registration_year: Annotated[str, StringConstraints(pattern=r"^\d{4}$")]
    vehicle_type_code: Annotated[str, StringConstraints(pattern=r"^\d$")]
    manufacturer_code: Annotated[str, StringConstraints(pattern=r"^\d{5}$")]
    model_code: Annotated[str, StringConstraints(pattern=r"^\d{5}$")]


class VehicleRef(ContractModel):
    """Exactly one of a catalogue identifier or a complete official tax key."""

    catalogue_id: CatalogueIdStr | None = None
    tax_key: VehicleTaxKey | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.catalogue_id is None) == (self.tax_key is None):
            raise ValueError("provide exactly one of catalogueId or taxKey")
        return self


class CurrentArrangementView(ContractModel):
    """Last-reported current arrangement; each element carries its own status."""

    kind: ProfileField[ArrangementKind]
    vehicle: ProfileField[VehicleRef]
    propulsion: ProfileField[Propulsion]
    participation: ProfileField[NonNegativeMoneyStr]
    family_deduction: ProfileField[NonNegativeMoneyStr]
    family_deduction_included_in_participation: ProfileField[bool]
    allowance: ProfileField[NonNegativeMoneyStr]


class EmployeeProfileView(ContractModel):
    profile_version: int = Field(ge=0)
    base_gross: ProfileField[NonNegativeMoneyStr]
    commissions_gross: ProfileField[NonNegativeMoneyStr]
    bonus_gross: ProfileField[NonNegativeMoneyStr]
    pension_employee_rate: ProfileField[RateStr]
    study_fund_employee_rate: ProfileField[RateStr]
    credit_points: ProfileField[CreditPointsStr]
    occupation_id: ProfileField[OccupationIdStr]
    entitlement_group: ProfileField[EntitlementGroup]
    qualifying_children_count: ProfileField[Annotated[int, Field(ge=0, le=100)]]
    current_arrangement: CurrentArrangementView
