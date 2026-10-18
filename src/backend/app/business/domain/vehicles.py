"""Clean vehicle concepts: official tax key, catalogue vehicle, tax record. No CSV columns."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.business.domain.errors import InvalidValueError
from app.business.domain.rules import EntitlementGroup
from app.business.domain.values import require_non_negative

_YEAR = re.compile(r"^\d{4}$")
_TYPE = re.compile(r"^\d$")
_CODE5 = re.compile(r"^\d{5}$")
_CATALOGUE_ID = re.compile(r"^[A-Z]{2,5}-\d{2,4}$")


class Propulsion(StrEnum):
    PETROL = "petrol"
    HYBRID = "hybrid"
    PLUG_IN_HYBRID = "plug_in_hybrid"
    ELECTRIC = "electric"


class Track(StrEnum):
    REGULAR = "regular"
    FAMILY = "family"


@dataclass(frozen=True, slots=True)
class VehicleTaxKey:
    """(שנת מס, שנת רישום, קוד סוג רכב, קוד תוצר, קוד דגם) as strings with leading zeros kept."""

    tax_year: str
    registration_year: str
    vehicle_type_code: str
    manufacturer_code: str
    model_code: str

    def __post_init__(self) -> None:
        checks = (
            (_YEAR, self.tax_year, "taxYear"),
            (_YEAR, self.registration_year, "registrationYear"),
            (_TYPE, self.vehicle_type_code, "vehicleTypeCode"),
            (_CODE5, self.manufacturer_code, "manufacturerCode"),
            (_CODE5, self.model_code, "modelCode"),
        )
        for pattern, value, name in checks:
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise InvalidValueError(f"taxKey.{name}: invalid code {value!r}", field=name)


@dataclass(frozen=True, slots=True)
class VehicleRef:
    """Exactly one of a catalogue identifier or a complete official tax key."""

    catalogue_id: str | None = None
    tax_key: VehicleTaxKey | None = None

    def __post_init__(self) -> None:
        if (self.catalogue_id is None) == (self.tax_key is None):
            raise InvalidValueError("provide exactly one of catalogueId or taxKey")
        if self.catalogue_id is not None and not _CATALOGUE_ID.fullmatch(self.catalogue_id):
            raise InvalidValueError(f"invalid catalogue id {self.catalogue_id!r}")


@dataclass(frozen=True, slots=True)
class CatalogueVehicle:
    catalogue_id: str
    track: Track
    group: EntitlementGroup | None
    """Entitlement group for regular vehicles; None for family-track vehicles."""
    manufacturer_name: str
    commercial_name: str
    model_description: str
    tax_key: VehicleTaxKey
    propulsion: Propulsion
    list_price: Decimal
    catalogue_value_of_use: Decimal
    monthly_company_cost: Decimal
    default_participation: Decimal

    def __post_init__(self) -> None:
        if not _CATALOGUE_ID.fullmatch(self.catalogue_id):
            raise InvalidValueError(f"invalid catalogue id {self.catalogue_id!r}")
        if (self.track is Track.REGULAR) != (self.group is not None):
            raise InvalidValueError("regular vehicles need a group; family vehicles have none")
        for name in (
            "list_price",
            "catalogue_value_of_use",
            "monthly_company_cost",
            "default_participation",
        ):
            require_non_negative(getattr(self, name), name)

    @property
    def option_key(self) -> str:
        return f"vehicle:{self.catalogue_id}"


@dataclass(frozen=True, slots=True)
class TaxRecord:
    key: VehicleTaxKey
    value_of_use: Decimal
    effective_date: date | None = None

    def __post_init__(self) -> None:
        require_non_negative(self.value_of_use, "valueOfUse")
