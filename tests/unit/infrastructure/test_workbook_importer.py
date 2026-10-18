"""Workbook import contract: catalogue + occupations by exact Hebrew names, explicit mappings."""

import io
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import openpyxl
import pytest

from app.domain.rules import EntitlementGroup
from app.domain.vehicles import Propulsion, Track, VehicleTaxKey
from app.infrastructure.imports.workbook_importer import WorkbookFormatError, read_catalogue
from tests.reference_sources import WORKBOOK_PATH, document, policy_content, real_sources

Mutation = Callable[[Any], None]


def _read_mutated(mutate: Mutation, policy: dict[str, Any] | None = None) -> None:
    workbook = openpyxl.load_workbook(WORKBOOK_PATH)
    mutate(workbook)
    buffer = io.BytesIO()
    workbook.save(buffer)
    read_catalogue(document(buffer.getvalue()), policy or policy_content(), "0" * 64)


def test_real_catalogue_has_actual_row_counts_and_src02_warning() -> None:
    catalogue = real_sources().catalogue
    tracks = [t.vehicle.track for t in catalogue.vehicles]
    assert tracks.count(Track.REGULAR) == 18 and tracks.count(Track.FAMILY) == 2
    assert len(catalogue.occupations) == 14
    assert any(w.startswith("SRC-02") for w in catalogue.report.warnings)


def test_catalogue_vehicle_gets_explicit_complete_tax_key() -> None:
    vehicles = {t.vehicle.catalogue_id: t for t in real_sources().catalogue.vehicles}
    atto3 = vehicles["CAR-10"].vehicle
    assert atto3.tax_key == VehicleTaxKey("2026", "2025", "1", "01014", "00003")
    assert atto3.propulsion is Propulsion.ELECTRIC
    assert atto3.group is EntitlementGroup.GIMEL
    assert atto3.catalogue_value_of_use == Decimal(3130)
    assert vehicles["CAR-16"].vehicle.propulsion is Propulsion.PLUG_IN_HYBRID
    carnival = vehicles["CAR-19"].vehicle
    assert carnival.track is Track.FAMILY and carnival.group is None
    assert carnival.monthly_company_cost == Decimal(6000)
    assert vehicles["CAR-10"].source.row_number == 11
    assert vehicles["CAR-10"].source.values[3] == "01014"


def test_occupations_get_stable_ids_from_the_policy() -> None:
    traced = real_sources().catalogue.occupations
    occupations = {t.occupation.occupation_id: t.occupation for t in traced}
    developer = occupations["OCC-SOFTWARE-DEV"]
    assert developer.name_he == "מפתח/ת תוכנה"
    assert developer.waiver_allowed and developer.waiver_allowance == Decimal(1500)
    field_rep = occupations["OCC-FIELD-SALES-REP"]
    assert field_rep.mandatory_car and not field_rep.waiver_allowed


def test_numeric_code_cell_fails_because_leading_zeros_are_lost() -> None:
    def mutate(workbook: Any) -> None:
        workbook["קטלוג רכבים"]["D11"] = 1014

    with pytest.raises(WorkbookFormatError, match="text cell"):
        _read_mutated(mutate)


def test_float_money_cell_fails() -> None:
    def mutate(workbook: Any) -> None:
        workbook["קטלוג רכבים"]["K11"] = 3130.5

    with pytest.raises(WorkbookFormatError, match="unsupported cell value 3130.5"):
        _read_mutated(mutate)


def test_unknown_occupation_fails() -> None:
    def mutate(workbook: Any) -> None:
        workbook["עיסוקים"]["A9"] = "מפתח/ת תוכנה בכיר/ה"

    with pytest.raises(WorkbookFormatError, match="no ID"):
        _read_mutated(mutate)


def test_vehicle_without_type_mapping_fails() -> None:
    policy = policy_content()
    del policy["catalogueTaxKeys"]["vehicleTypeCodeByCatalogueId"]["CAR-10"]
    with pytest.raises(WorkbookFormatError, match="SRC-03"):
        _read_mutated(lambda _: None, policy)


def test_mapping_for_unknown_vehicle_fails() -> None:
    policy = policy_content()
    policy["catalogueTaxKeys"]["vehicleTypeCodeByCatalogueId"]["CAR-99"] = "1"
    with pytest.raises(WorkbookFormatError, match="CAR-99"):
        _read_mutated(lambda _: None, policy)


def test_renamed_column_fails() -> None:
    def mutate(workbook: Any) -> None:
        workbook["קטלוג רכבים"]["I1"] = "סוג הנעה"

    with pytest.raises(WorkbookFormatError, match="header"):
        _read_mutated(mutate)


def test_unknown_group_and_yes_no_values_fail() -> None:
    def bad_group(workbook: Any) -> None:
        workbook["קטלוג רכבים"]["B2"] = "ה"

    def bad_flag(workbook: Any) -> None:
        workbook["עיסוקים"]["C2"] = "אולי"

    with pytest.raises(WorkbookFormatError, match="group"):
        _read_mutated(bad_group)
    with pytest.raises(WorkbookFormatError, match="כן/לא"):
        _read_mutated(bad_flag)


def test_missing_sheet_fails() -> None:
    def mutate(workbook: Any) -> None:
        workbook["עיסוקים"].title = "תפקידים"

    with pytest.raises(WorkbookFormatError, match="עיסוקים"):
        _read_mutated(mutate)
