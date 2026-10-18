"""Strict JSONB codec: exact keys, decimal strings, missing is explicit, no floats."""

import copy
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.business.domain.arrangements import ArrangementKind
from app.business.domain.cases import FactPath, HypotheticalOverride
from app.business.domain.employee_profiles import (
    CurrentArrangementFacts,
    EmployeeProfile,
    SalaryFacts,
)
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.business.domain.rules import EntitlementGroup
from app.business.domain.vehicles import Propulsion, VehicleRef, VehicleTaxKey
from app.business.infrastructure.database.engine import JsonFloatError, json_dumps, json_loads
from app.business.infrastructure.database.repositories.codec import (
    PersistenceFormatError,
    decode_facts,
    decode_field,
    decode_overrides,
    encode_facts,
    encode_overrides,
)

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
EMPLOYEE = uuid4()


def _usable(value: object, source: SourceType = SourceType.EMPLOYEE) -> FieldValue[Any]:
    return FieldValue(
        value=value, status=FieldStatus.USABLE, source=source, updated_at=NOW, revision=2
    )


def _profile() -> EmployeeProfile:
    return EmployeeProfile(
        employee_id=EMPLOYEE,
        version=7,
        salary=SalaryFacts(
            base_gross=_usable(Decimal("18500.123456789")),
            commissions_gross=_usable(Decimal("0")),
            pension_employee_rate=_usable(Decimal("0.065")),
            credit_points=FieldValue(
                value=Decimal("2.75"), status=FieldStatus.NEEDS_CLARIFICATION, revision=1
            ),
        ),
        occupation_id=_usable("OCC-PRODUCT-MGR"),
        entitlement_group=_usable(EntitlementGroup.GIMEL),
        qualifying_children_count=_usable(0),
        current_arrangement=CurrentArrangementFacts(
            kind=_usable(ArrangementKind.COMPANY_CAR),
            vehicle=_usable(
                VehicleRef(tax_key=VehicleTaxKey("2026", "2025", "1", "01014", "00003"))
            ),
            propulsion=_usable(Propulsion.ELECTRIC, SourceType.CATALOGUE),
            family_deduction_included_in_participation=_usable(False),
        ),
    )


def test_facts_round_trip_exactly_through_json_text() -> None:
    profile = _profile()
    document = encode_facts(profile)
    text = json_dumps(document)
    assert "18500.123456789" in text and '"01014"' in text and '"00003"' in text
    decoded = decode_facts(json_loads(text), employee_id=EMPLOYEE, version=7)
    assert decoded == profile


def test_missing_fields_are_explicit_and_never_zero() -> None:
    document = encode_facts(profile := _profile())
    assert set(document) == {"schemaVersion", *(p.value for p in FactPath)}
    bonus = document["payroll.bonusGross"]
    assert bonus == {
        "value": None,
        "status": "missing",
        "source": None,
        "sourceMessageId": None,
        "updatedAt": None,
        "revision": 0,
        "lastConfirmedAt": None,
    }
    assert profile.salary.bonus_gross.value is None
    assert document["payroll.commissionsGross"]["value"] == "0"


def test_decimal_without_exponent_and_utc_timestamps() -> None:
    profile = EmployeeProfile(
        employee_id=EMPLOYEE,
        version=0,
        salary=SalaryFacts(base_gross=_usable(Decimal("1E+2"))),
    )
    field = encode_facts(profile)["payroll.baseGross"]
    assert field["value"] == "100"
    assert field["updatedAt"] == "2026-10-18T09:00:00Z"


def _mutated(change: Any) -> dict[str, Any]:
    document = copy.deepcopy(encode_facts(_profile()))
    change(document)
    return document


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.pop("payroll.bonusGross"),
        lambda d: d.update(extra=1),
        lambda d: d.update(schemaVersion=2),
        lambda d: d["payroll.baseGross"].update(value=18500.5),
        lambda d: d["payroll.baseGross"].update(value="1.5e3"),
        lambda d: d["payroll.baseGross"].pop("revision"),
        lambda d: d["payroll.baseGross"].update(note="x"),
        lambda d: d["payroll.bonusGross"].update(value="1"),
        lambda d: d["payroll.baseGross"].update(value=None),
        lambda d: d["employee.entitlementGroup"].update(value="ה"),
        lambda d: d["employee.qualifyingChildrenCount"].update(value=True),
        lambda d: d["employee.qualifyingChildrenCount"].update(value="3"),
        lambda d: d["currentArrangement.vehicle"].update(
            value={"catalogueId": "CAR-09", "taxKey": {}}
        ),
        lambda d: d["currentArrangement.vehicle"]["value"]["taxKey"].update(modelCode=3),
        lambda d: d["payroll.baseGross"].update(status="known"),
        lambda d: d["payroll.pensionEmployeeRate"].update(value="1.2"),
    ],
)
def test_decode_rejects_every_format_violation(change: Any) -> None:
    with pytest.raises(PersistenceFormatError):
        decode_facts(_mutated(change), employee_id=EMPLOYEE, version=7)


def test_needs_clarification_may_carry_a_value() -> None:
    field = decode_field(
        FactPath.CREDIT_POINTS,
        encode_facts(_profile())["payroll.creditPoints"],
    )
    assert field.status is FieldStatus.NEEDS_CLARIFICATION and field.value == Decimal("2.75")


def test_overrides_round_trip_and_reject_bad_entries() -> None:
    overrides = (
        HypotheticalOverride(FactPath.BASE_GROSS, Decimal("20000.00"), NOW, uuid4()),
        HypotheticalOverride(FactPath.CURRENT_VEHICLE, VehicleRef(catalogue_id="CAR-10"), NOW),
    )
    document = encode_overrides(overrides)
    assert decode_overrides(json_loads(json_dumps(document))) == overrides
    for bad in (
        [{**document[0], "path": "payroll.salary"}],
        [{**document[0], "value": None}],
        [{**document[0], "extra": 1}],
        {"not": "a list"},
    ):
        with pytest.raises(PersistenceFormatError):
            decode_overrides(bad)


def test_engine_json_rejects_floats_and_non_finite_values() -> None:
    with pytest.raises(JsonFloatError):
        json_loads('{"amount": 1.5}')
    with pytest.raises(JsonFloatError):
        json_loads('{"amount": NaN}')
    with pytest.raises(ValueError):
        json_dumps({"amount": float("nan")})
    with pytest.raises(TypeError):
        json_dumps({"amount": Decimal("1.5")})
    assert json_loads(json.dumps({"n": 3, "s": "שלום"})) == {"n": 3, "s": "שלום"}
