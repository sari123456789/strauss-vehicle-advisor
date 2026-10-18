"""Behavioral checks of the provisional shared contracts against the approved specifications."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.contracts.business_port import CANONICAL_TOOL_NAMES
from app.contracts.case_inputs import UpdateInputsRequest
from app.contracts.common import (
    CONTRACT_STATUS,
    Envelope,
    EnvelopeStatus,
    ErrorCode,
    ErrorInfo,
    FieldStatus,
    SourceType,
)
from app.contracts.comparisons import CompareRequest, ScenarioResult
from app.contracts.profile import ProfileField, VehicleRef, VehicleTaxKey
from app.contracts.readiness import ReadinessResult, Selection
from app.contracts.representatives import AnswerSubmit


def _update(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "mode": "actual_update",
        "expectedCaseVersion": 3,
        "expectedProfileVersion": 5,
        "operationId": str(uuid4()),
        "changes": [{"op": "set", "field": "payroll.baseGross", "value": "18500.00"}],
    }
    body.update(overrides)
    return body


def test_contracts_are_marked_provisional() -> None:
    assert CONTRACT_STATUS == "provisional"


def test_canonical_tool_names_match_agent_spec_section_7_and_g1_g3() -> None:
    assert set(CANONICAL_TOOL_NAMES) == {
        "get_representative_request",  # G1 (architecture §15)
        "resolve_occupation",  # G2
        "confirm_profile_fields",  # G3
        "get_conversation_context",
        "update_conversation_inputs",
        "list_eligible_options",
        "resolve_vehicle",
        "get_vehicle_tax_value",
        "check_eligibility",
        "check_calculation_readiness",
        "compare_scenarios",
        "create_representative_question",
        "get_comparison_details",
    }


def test_field_statuses_and_sources_are_canonical() -> None:
    assert {s.value for s in FieldStatus} == {"missing", "needs_clarification", "usable"}
    assert {s.value for s in SourceType} == {
        "employee",
        "catalogue",
        "rules",
        "tax_dataset",
        "representative",
    }


def test_architecture_example_update_request_is_valid() -> None:
    request = UpdateInputsRequest.model_validate(
        _update(
            changes=[
                {"op": "set", "field": "payroll.baseGross", "value": "18500.00"},
                {"op": "set", "field": "payroll.commissionsGross", "value": "0.00"},
            ]
        )
    )
    assert request.expected_case_version == 3


@pytest.mark.parametrize("bad", [18500, 18500.0, "18,500", "NaN", "1e3", "-5.00", ""])
def test_money_must_be_a_non_negative_decimal_string(bad: object) -> None:
    with pytest.raises(ValidationError):
        UpdateInputsRequest.model_validate(
            _update(changes=[{"op": "set", "field": "payroll.baseGross", "value": bad}])
        )


def test_set_null_is_invalid_and_clear_establishes_missing() -> None:
    with pytest.raises(ValidationError):
        UpdateInputsRequest.model_validate(
            _update(changes=[{"op": "set", "field": "payroll.bonusGross", "value": None}])
        )
    ok = UpdateInputsRequest.model_validate(
        _update(changes=[{"op": "clear", "field": "payroll.bonusGross"}])
    )
    assert ok.changes[0].op == "clear"


def test_zero_study_fund_rate_is_valid() -> None:
    UpdateInputsRequest.model_validate(
        _update(changes=[{"op": "set", "field": "payroll.studyFundEmployeeRate", "value": "0"}])
    )


@pytest.mark.parametrize("rate", ["6", "1.5", "0.06%", 0.06])
def test_rates_must_be_decimal_strings_between_zero_and_one(rate: object) -> None:
    with pytest.raises(ValidationError):
        UpdateInputsRequest.model_validate(
            _update(changes=[{"op": "set", "field": "payroll.pensionEmployeeRate", "value": rate}])
        )


def test_actual_update_requires_expected_profile_version() -> None:
    with pytest.raises(ValidationError):
        UpdateInputsRequest.model_validate(_update(expectedProfileVersion=None))
    UpdateInputsRequest.model_validate(_update(mode="hypothetical", expectedProfileVersion=None))


def test_unknown_fields_and_duplicate_changes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        UpdateInputsRequest.model_validate(_update(scope="per-field"))
    with pytest.raises(ValidationError):
        UpdateInputsRequest.model_validate(
            _update(
                changes=[
                    {"op": "set", "field": "payroll.bonusGross", "value": "0"},
                    {"op": "clear", "field": "payroll.bonusGross"},
                ]
            )
        )


@pytest.mark.parametrize("op", ["confirm", "reset", "acceptAssumption"])
def test_unapproved_operations_are_rejected(op: str) -> None:
    with pytest.raises(ValidationError):
        UpdateInputsRequest.model_validate(
            _update(changes=[{"op": op, "field": "payroll.baseGross"}])
        )


def test_children_count_is_a_strict_non_negative_integer() -> None:
    UpdateInputsRequest.model_validate(
        _update(changes=[{"op": "set", "field": "employee.qualifyingChildrenCount", "value": 4}])
    )
    for bad in ("4", -1, 3.5):
        with pytest.raises(ValidationError):
            UpdateInputsRequest.model_validate(
                _update(
                    changes=[
                        {"op": "set", "field": "employee.qualifyingChildrenCount", "value": bad}
                    ]
                )
            )


def test_tax_key_preserves_leading_zeros_and_rejects_numbers() -> None:
    key = VehicleTaxKey.model_validate(
        {
            "taxYear": "2026",
            "registrationYear": "2025",
            "vehicleTypeCode": "1",
            "manufacturerCode": "01014",
            "modelCode": "00003",
        }
    )
    assert (key.manufacturer_code, key.model_code) == ("01014", "00003")
    for bad in ({"manufacturerCode": "1014"}, {"modelCode": 3}):
        with pytest.raises(ValidationError):
            VehicleTaxKey.model_validate({**key.model_dump(by_alias=True), **bad})


def test_vehicle_ref_requires_exactly_one_identifier() -> None:
    with pytest.raises(ValidationError):
        VehicleRef.model_validate({})
    VehicleRef.model_validate({"catalogueId": "CAR-10"})


def test_missing_is_null_never_zero() -> None:
    with pytest.raises(ValidationError):
        ProfileField[str].model_validate({"value": "0", "status": "missing"})
    with pytest.raises(ValidationError):
        ProfileField[str].model_validate({"status": "usable"})
    assert ProfileField[str].model_validate({"status": "missing"}).value is None


def test_envelope_error_consistency() -> None:
    error = ErrorInfo(code=ErrorCode.NOT_FOUND, trace_id="t")
    Envelope[str](status=EnvelopeStatus.ERROR, error=error, trace_id="t")
    with pytest.raises(ValidationError):
        Envelope[str](status=EnvelopeStatus.ERROR, data="x", error=error, trace_id="t")
    with pytest.raises(ValidationError):
        Envelope[str](status=EnvelopeStatus.OK, data="x", error=error, trace_id="t")


def test_uncalculated_scenarios_carry_no_amounts() -> None:
    with pytest.raises(ValidationError):
        ScenarioResult.model_validate(
            {
                "optionKey": "vehicle:CAR-11",
                "status": "needs_information",
                "net": {"raw": "0", "display": "0"},
            }
        )


def test_regular_comparison_requires_profile_version() -> None:
    body = {
        "selection": {"mode": "explicit", "optionKeys": ["vehicle:CAR-10"]},
        "includeCurrent": True,
        "kind": "regular",
        "expectedCaseVersion": 3,
        "operationId": str(uuid4()),
    }
    with pytest.raises(ValidationError):
        CompareRequest.model_validate(body)
    CompareRequest.model_validate({**body, "kind": "hypothetical"})


def test_selection_modes() -> None:
    Selection.model_validate({"mode": "all_eligible", "categories": ["regular", "family"]})
    with pytest.raises(ValidationError):
        Selection.model_validate({"mode": "explicit"})
    with pytest.raises(ValidationError):
        Selection.model_validate({"mode": "explicit", "optionKeys": ["vehicle:CAR-10"] * 2})
    with pytest.raises(ValidationError):
        Selection.model_validate({"mode": "explicit", "optionKeys": ["Corolla"]})


def test_readiness_output_uses_agent_spec_field_names() -> None:
    aliases = {f.alias for f in ReadinessResult.model_fields.values()}
    assert {
        "readyScenarioIds",
        "missingEmployeeFields",
        "ambiguousFields",
        "pendingRepresentativeFields",
        "blockedScenarios",
        "baselineReady",
        "scopeComplete",
    } <= aliases


def test_representative_answer_rules() -> None:
    base = {"operationId": str(uuid4()), "expectedRequestVersion": 1}
    AnswerSubmit.model_validate({**base, "outcome": "answered", "value": "350.00", "unit": "ILS"})
    AnswerSubmit.model_validate({**base, "outcome": "cannot_determine"})
    with pytest.raises(ValidationError):
        AnswerSubmit.model_validate({**base, "outcome": "answered"})
    with pytest.raises(ValidationError):
        AnswerSubmit.model_validate({**base, "outcome": "cannot_determine", "value": "1"})
    with pytest.raises(ValidationError):
        AnswerSubmit.model_validate(
            {
                **base,
                "outcome": "answered",
                "value": "350.00",
                "effectiveFrom": "2026-02-01",
                "effectiveTo": "2026-01-01",
            }
        )
