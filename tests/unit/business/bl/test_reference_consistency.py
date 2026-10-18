"""Cross-source consistency (BL): the real set passes; any contradiction fails the whole set."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.business.bl.reference_data.consistency import (
    SourceConflictError,
    TaxEntry,
    check_reference_consistency,
)
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.rules import RulesConfig
from app.business.domain.vehicles import CatalogueVehicle, Propulsion, VehicleTaxKey
from tests.reference_sources import real_sources


def _inputs() -> tuple[
    RulesConfig, list[CatalogueVehicle], list[OccupationPolicy], dict[VehicleTaxKey, TaxEntry]
]:
    sources = real_sources()
    taxes = {t.record.key: TaxEntry(t.record, t.drive_code) for t in sources.tax_dataset.records}
    vehicles = [t.vehicle for t in sources.catalogue.vehicles]
    occupations: list[OccupationPolicy] = [t.occupation for t in sources.catalogue.occupations]
    return sources.rules.config, vehicles, occupations, taxes


def _check(
    rules: RulesConfig,
    vehicles: list[CatalogueVehicle],
    occupations: list[OccupationPolicy],
    taxes: dict[VehicleTaxKey, TaxEntry],
) -> None:
    check_reference_consistency(
        rules, vehicles, occupations, taxes, real_sources().rules.drive_field
    )


def _replace_vehicle(vehicles: list[CatalogueVehicle], cid: str, **changes: object) -> None:
    index = next(i for i, v in enumerate(vehicles) if v.catalogue_id == cid)
    vehicles[index] = replace(vehicles[index], **changes)  # type: ignore[arg-type]


def test_real_sources_are_consistent() -> None:
    rules, vehicles, occupations, taxes = _inputs()
    drive_field = real_sources().rules.drive_field
    report = check_reference_consistency(rules, vehicles, occupations, taxes, drive_field)
    assert report.as_dict() == {
        "regularVehicles": 18,
        "familyVehicles": 2,
        "occupations": 14,
        "taxRecords": 77_988,
        "checkedVehicleKeys": 20,
    }


def test_value_of_use_mismatch_is_a_source_conflict() -> None:
    rules, vehicles, occupations, taxes = _inputs()
    _replace_vehicle(vehicles, "CAR-10", catalogue_value_of_use=Decimal(6950))
    with pytest.raises(SourceConflictError, match="CAR-10.*value of use") as error:
        _check(rules, vehicles, occupations, taxes)
    assert error.value.code == "SOURCE_CONFLICT"


def test_propulsion_mismatch_is_a_source_conflict() -> None:
    """SRC-04: SPORTAGE HEV is plug-in hybrid in the supplied data, not inferred from its name."""
    rules, vehicles, occupations, taxes = _inputs()
    _replace_vehicle(vehicles, "CAR-16", propulsion=Propulsion.HYBRID)
    with pytest.raises(SourceConflictError, match="CAR-16.*propulsion"):
        _check(rules, vehicles, occupations, taxes)


def test_type2_key_for_atto3_is_a_source_conflict() -> None:
    """SRC-03: the type-2 record (TANG, 6,950) is a different vehicle."""
    rules, vehicles, occupations, taxes = _inputs()
    atto3 = next(v for v in vehicles if v.catalogue_id == "CAR-10")
    _replace_vehicle(vehicles, "CAR-10", tax_key=replace(atto3.tax_key, vehicle_type_code="2"))
    with pytest.raises(SourceConflictError, match="CAR-10.*value of use"):
        _check(rules, vehicles, occupations, taxes)


def test_missing_tax_key_is_a_source_conflict() -> None:
    rules, vehicles, occupations, taxes = _inputs()
    atto3 = next(v for v in vehicles if v.catalogue_id == "CAR-10")
    del taxes[atto3.tax_key]
    with pytest.raises(SourceConflictError, match="CAR-10.*not found"):
        _check(rules, vehicles, occupations, taxes)


def test_missing_family_track_car_is_a_source_conflict() -> None:
    rules, vehicles, occupations, taxes = _inputs()
    vehicles = [v for v in vehicles if v.catalogue_id != "CAR-19"]
    with pytest.raises(SourceConflictError, match="CAR-19 is missing"):
        _check(rules, vehicles, occupations, taxes)


def test_duplicate_catalogue_id_is_a_source_conflict() -> None:
    rules, vehicles, occupations, taxes = _inputs()
    vehicles.append(vehicles[0])
    with pytest.raises(SourceConflictError, match="duplicate catalogue id"):
        _check(rules, vehicles, occupations, taxes)
