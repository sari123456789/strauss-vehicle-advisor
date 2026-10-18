"""Cross-source consistency of one reference-data set (rules, catalogue, occupations, tax dataset).

Pure and deterministic. Every catalogue vehicle must resolve through its complete official key,
carry the same value of use as the tax dataset, and have the propulsion category the rules JSON
assigns to the record's drive code (catalogue vehicles only - SRC-04). Any mismatch fails the
whole set; nothing is repaired or guessed.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.business.domain.errors import DomainError
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.rules import RulesConfig
from app.business.domain.vehicles import (
    CatalogueVehicle,
    Propulsion,
    TaxRecord,
    Track,
    VehicleTaxKey,
)


class SourceConflictError(DomainError):
    """Two supplied sources contradict each other."""

    code = "SOURCE_CONFLICT"


@dataclass(frozen=True, slots=True)
class TaxEntry:
    record: TaxRecord
    drive_code: str


@dataclass(frozen=True, slots=True)
class ConsistencyReport:
    regular_vehicles: int
    family_vehicles: int
    occupations: int
    tax_records: int
    checked_vehicle_keys: int

    def as_dict(self) -> dict[str, int]:
        return {
            "regularVehicles": self.regular_vehicles,
            "familyVehicles": self.family_vehicles,
            "occupations": self.occupations,
            "taxRecords": self.tax_records,
            "checkedVehicleKeys": self.checked_vehicle_keys,
        }


def check_reference_consistency(
    rules: RulesConfig,
    vehicles: Sequence[CatalogueVehicle],
    occupations: Sequence[OccupationPolicy],
    tax_records: Mapping[VehicleTaxKey, TaxEntry],
    drive_field: Mapping[str, Propulsion],
) -> ConsistencyReport:
    problems: list[str] = []

    for name, ids in (
        ("catalogue id", [v.catalogue_id for v in vehicles]),
        ("occupation id", [o.occupation_id for o in occupations]),
        ("occupation name", [o.name_he for o in occupations]),
    ):
        duplicates = sorted(k for k, n in Counter(ids).items() if n > 1)
        if duplicates:
            problems.append(f"duplicate {name}: {', '.join(duplicates)}")

    groups = set(rules.group_order)
    for vehicle in vehicles:
        cid = vehicle.catalogue_id
        if vehicle.group is not None and vehicle.group not in groups:
            problems.append(f"{cid}: group {vehicle.group} is not in the rules groupOrder")
        entry = tax_records.get(vehicle.tax_key)
        if entry is None:
            problems.append(f"{cid}: complete tax key not found in the tax dataset")
            continue
        if entry.record.value_of_use != vehicle.catalogue_value_of_use:
            problems.append(
                f"{cid}: catalogue value of use {vehicle.catalogue_value_of_use} differs from "
                f"tax dataset value {entry.record.value_of_use}"
            )
        expected = drive_field.get(entry.drive_code)
        if expected is None:
            problems.append(f"{cid}: drive code {entry.drive_code!r} is not in rules driveField")
        elif expected is not vehicle.propulsion:
            problems.append(
                f"{cid}: catalogue propulsion {vehicle.propulsion} differs from rules driveField "
                f"{expected} for drive code {entry.drive_code!r}"
            )

    by_id = {v.catalogue_id: v for v in vehicles}
    family_ids = set(rules.family_track.catalogue_ids)
    for cid in sorted(family_ids):
        family_vehicle = by_id.get(cid)
        if family_vehicle is None:
            problems.append(f"rules familyTrack car {cid} is missing from the catalogue")
        elif family_vehicle.track is not Track.FAMILY:
            problems.append(f"rules familyTrack car {cid} is not a family-track catalogue vehicle")
    for vehicle in vehicles:
        if vehicle.track is Track.FAMILY and vehicle.catalogue_id not in family_ids:
            problems.append(
                f"{vehicle.catalogue_id}: family-track vehicle not in rules familyTrack"
            )

    for occupation in occupations:
        if occupation.group not in groups:
            problems.append(
                f"{occupation.occupation_id}: group {occupation.group} not in groupOrder"
            )

    if not vehicles:
        problems.append("the catalogue is empty")
    if not occupations:
        problems.append("the occupation table is empty")
    if problems:
        raise SourceConflictError("reference sources are inconsistent: " + "; ".join(problems))

    family = sum(1 for v in vehicles if v.track is Track.FAMILY)
    return ConsistencyReport(
        regular_vehicles=len(vehicles) - family,
        family_vehicles=family,
        occupations=len(occupations),
        tax_records=len(tax_records),
        checked_vehicle_keys=len(vehicles),
    )
