"""Local Domain invariants (no persistence, no frameworks)."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.comparisons import ScenarioOutcome, ScenarioStatus
from app.domain.errors import InvalidValueError
from app.domain.occupations import OccupationPolicy
from app.domain.payroll import ArrangementComponents, PayrollFacts
from app.domain.provenance import FieldStatus, FieldValue, SourceType
from app.domain.rules import EntitlementGroup, TaxBracket
from app.domain.vehicles import CatalogueVehicle, Propulsion, Track, VehicleRef, VehicleTaxKey
from tests.unit.business.helpers import rules

KEY = VehicleTaxKey("2026", "2025", "1", "01014", "00003")


def test_field_value_status_rules() -> None:
    with pytest.raises(InvalidValueError):
        FieldValue(value=Decimal(0), status=FieldStatus.MISSING)
    with pytest.raises(InvalidValueError):
        FieldValue(status=FieldStatus.USABLE)
    assert FieldValue.usable(Decimal(0), SourceType.EMPLOYEE).is_usable


@pytest.mark.parametrize(
    "bad",
    [
        {"manufacturer_code": "1014"},
        {"model_code": "3"},
        {"vehicle_type_code": "01"},
        {"tax_year": "26"},
    ],
)
def test_tax_key_requires_full_codes_with_leading_zeros(bad: dict[str, str]) -> None:
    with pytest.raises(InvalidValueError):
        replace(KEY, **bad)


def test_tax_key_keeps_leading_zeros() -> None:
    assert (KEY.manufacturer_code, KEY.model_code) == ("01014", "00003")


def test_vehicle_ref_requires_exactly_one_identifier() -> None:
    with pytest.raises(InvalidValueError):
        VehicleRef()
    with pytest.raises(InvalidValueError):
        VehicleRef(catalogue_id="CAR-10", tax_key=KEY)
    VehicleRef(tax_key=KEY)


def _vehicle(**overrides: object) -> CatalogueVehicle:
    values: dict[str, object] = {
        "catalogue_id": "CAR-10",
        "track": Track.REGULAR,
        "group": EntitlementGroup.GIMEL,
        "manufacturer_name": "בי ווי די",
        "commercial_name": "ATTO 3",
        "model_description": "SC2E-SC2E01",
        "tax_key": KEY,
        "propulsion": Propulsion.ELECTRIC,
        "list_price": Decimal(177500),
        "catalogue_value_of_use": Decimal(3130),
        "monthly_company_cost": Decimal(4100),
        "default_participation": Decimal(300),
    }
    values.update(overrides)
    return CatalogueVehicle(**values)  # type: ignore[arg-type]


def test_catalogue_vehicle_track_and_group_consistency() -> None:
    assert _vehicle().option_key == "vehicle:CAR-10"
    with pytest.raises(InvalidValueError):
        _vehicle(track=Track.FAMILY)
    _vehicle(catalogue_id="CAR-19", track=Track.FAMILY, group=None)
    with pytest.raises(InvalidValueError):
        _vehicle(default_participation=Decimal(-1))


def test_occupation_policy_invariants() -> None:
    with pytest.raises(InvalidValueError):
        OccupationPolicy(
            "OCC-FIELD-SALES-REP", "נציג/ת מכירות שטח", EntitlementGroup.BET, True, True, Decimal(0)
        )
    with pytest.raises(InvalidValueError):
        OccupationPolicy("field-sales", "x", EntitlementGroup.BET, True, False, Decimal(0))


def test_payroll_facts_invariants() -> None:
    with pytest.raises(InvalidValueError):
        PayrollFacts(Decimal(1), Decimal(0), Decimal(0), Decimal("1.01"), Decimal(0), Decimal(0))
    with pytest.raises(InvalidValueError):
        ArrangementComponents(ordinary_participation=Decimal(-1))


def test_rules_brackets_must_increase_and_end_unbounded() -> None:
    config = rules()
    with pytest.raises(InvalidValueError):
        replace(config, brackets=(TaxBracket(Decimal(10), Decimal("0.1")),))
    with pytest.raises(InvalidValueError):
        replace(
            config,
            brackets=(
                TaxBracket(Decimal(10), Decimal("0.1")),
                TaxBracket(Decimal(5), Decimal("0.2")),
                TaxBracket(None, Decimal("0.3")),
            ),
        )


def test_scenario_outcome_amounts_only_when_calculated() -> None:
    with pytest.raises(InvalidValueError):
        ScenarioOutcome("vehicle:CAR-10", ScenarioStatus.CALCULATED)
    with pytest.raises(InvalidValueError):
        ScenarioOutcome("vehicle:CAR-10", ScenarioStatus.NEEDS_INFORMATION, delta=Decimal(1))
