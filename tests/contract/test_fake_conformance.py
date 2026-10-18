"""The fake must satisfy the same provisional Protocol and schemas the real Application will."""

from typing import Any
from uuid import uuid4

import pytest

from app.application.ports.input_queue import (
    DurableInputQueue,
    EnqueueOutcome,
    InputPayloadMismatchError,
)
from app.application.ports.unit_of_work import UnitOfWork
from app.contracts.business_port import CANONICAL_TOOL_NAMES, BusinessToolsPort
from app.contracts.case_inputs import UpdateInputsRequest
from app.contracts.case_lifecycle import TransactionHandle
from app.contracts.catalogue import (
    EligibilityRequest,
    ListOptionsRequest,
    ResolveVehicleRequest,
    TaxValueRequest,
)
from app.contracts.common import Envelope, EnvelopeStatus, ErrorCode
from app.contracts.comparisons import CompareRequest, ComparisonDetailsRequest, ScenarioStatus
from app.contracts.readiness import ReadinessRequest
from app.contracts.representatives import CreateQuestionRequest, RepresentativeAnsweredEvent
from app.contracts.tool_context import ToolContext
from tests.fakes.fake_business_port import SCENARIOS, FakeBusinessPort
from tests.fakes.fake_input_queue import InMemoryInputQueue
from tests.fakes.fake_unit_of_work import InMemoryUnitOfWork

CTX = ToolContext.model_validate(
    {
        "actor": {"subjectId": str(uuid4()), "roles": ["employee"], "origin": "agent"},
        "conversationId": str(uuid4()),
        "caseId": str(uuid4()),
        "traceId": "test",
    }
)

REQUESTS: dict[str, Any] = {
    "get_conversation_context": None,
    "update_conversation_inputs": UpdateInputsRequest.model_validate(
        {
            "mode": "hypothetical",
            "expectedCaseVersion": 3,
            "operationId": str(uuid4()),
            "changes": [{"op": "set", "field": "payroll.bonusGross", "value": "1000.00"}],
        }
    ),
    "list_eligible_options": ListOptionsRequest(),
    "resolve_vehicle": ResolveVehicleRequest.model_validate(
        {"purpose": "alternative", "catalogueId": "CAR-10"}
    ),
    "get_vehicle_tax_value": TaxValueRequest.model_validate({"catalogueId": "CAR-10"}),
    "check_eligibility": EligibilityRequest.model_validate(
        {"expectedCaseVersion": 3, "optionKey": "vehicle:CAR-10"}
    ),
    "check_calculation_readiness": ReadinessRequest.model_validate(
        {"goal": "calculate_net", "expectedCaseVersion": 3}
    ),
    "compare_scenarios": CompareRequest.model_validate(
        {
            "selection": {"mode": "explicit", "optionKeys": ["vehicle:CAR-10"]},
            "includeCurrent": True,
            "kind": "regular",
            "expectedCaseVersion": 3,
            "expectedProfileVersion": 5,
            "operationId": str(uuid4()),
        }
    ),
    "create_representative_question": CreateQuestionRequest.model_validate(
        {
            "fieldCode": "alternative.participation",
            "optionKey": "vehicle:CAR-11",
            "questionText": "מה ההשתתפות החודשית של העובד ברכב EV3?",
            "idempotencyKey": str(uuid4()),
            "expectedCaseVersion": 3,
        }
    ),
    "get_comparison_details": ComparisonDetailsRequest.model_validate({"runId": str(uuid4())}),
}


async def _call(port: FakeBusinessPort, tool: str) -> Envelope[Any]:
    method = getattr(port, tool)
    request = REQUESTS[tool]
    result: Envelope[Any] = await (method(CTX) if request is None else method(CTX, request))
    return result


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_fake_satisfies_protocol(scenario: str) -> None:
    assert isinstance(FakeBusinessPort(scenario), BusinessToolsPort)


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("tool", CANONICAL_TOOL_NAMES)
async def test_every_tool_returns_a_valid_envelope(scenario: str, tool: str) -> None:
    port = FakeBusinessPort(scenario)
    envelope = await _call(port, tool)
    assert isinstance(envelope, Envelope)
    assert port.calls[-1][0] == tool


@pytest.mark.parametrize("tool", CANONICAL_TOOL_NAMES)
async def test_unauthorized_never_discloses_data(tool: str) -> None:
    envelope = await _call(FakeBusinessPort("unauthorized"), tool)
    assert envelope.status is EnvelopeStatus.ERROR
    assert envelope.error is not None and envelope.error.code is ErrorCode.NOT_FOUND
    assert envelope.data is None


async def test_missing_scenario_returns_no_amounts() -> None:
    envelope = await _call(FakeBusinessPort("missing"), "compare_scenarios")
    assert envelope.status is EnvelopeStatus.NEEDS_INFORMATION
    assert envelope.data is None


async def test_blocked_scenario_returns_partial_results() -> None:
    envelope = await _call(FakeBusinessPort("blocked"), "compare_scenarios")
    assert envelope.status is EnvelopeStatus.PARTIAL
    statuses = {a.option_key: a.status for a in envelope.data.alternatives}
    assert statuses == {
        "vehicle:CAR-10": ScenarioStatus.CALCULATED,
        "vehicle:CAR-14": ScenarioStatus.INELIGIBLE,
        "vehicle:CAR-11": ScenarioStatus.NEEDS_INFORMATION,
    }
    ready = next(a for a in envelope.data.alternatives if a.status is ScenarioStatus.CALCULATED)
    assert ready.net is not None and ready.delta is None  # baseline incomplete -> no delta


async def test_stale_scenario_reports_version_conflict() -> None:
    envelope = await _call(FakeBusinessPort("stale"), "update_conversation_inputs")
    assert envelope.error is not None and envelope.error.code is ErrorCode.VERSION_CONFLICT


async def test_success_delta_rounds_the_precise_difference() -> None:
    envelope = await _call(FakeBusinessPort("success"), "compare_scenarios")
    alt = envelope.data.alternatives[0]
    assert (alt.delta.raw, alt.delta.display) == ("660.998000", "661")


def _event(**overrides: object) -> RepresentativeAnsweredEvent:
    body: dict[str, object] = {
        "eventId": "c0000000-0000-4000-8000-000000000099",
        "occurredAt": "2026-10-15T09:00:00Z",
        "requestId": str(uuid4()),
        "caseId": "c0000000-0000-4000-8000-000000000001",
        "requestVersion": 2,
        "caseVersionAfter": 4,
    }
    body.update(overrides)
    return RepresentativeAnsweredEvent.model_validate(body)


def test_in_memory_fakes_satisfy_application_ports() -> None:
    uow = InMemoryUnitOfWork()
    assert isinstance(uow, UnitOfWork)
    assert isinstance(uow, TransactionHandle)
    assert isinstance(uow.input_queue, DurableInputQueue)


async def test_enqueue_is_atomic_with_the_unit_of_work() -> None:
    queue = InMemoryInputQueue()
    event = _event()
    async with InMemoryUnitOfWork(queue) as uow:
        await uow.input_queue.enqueue_representative_answered(event)
    assert queue.committed == {}  # no commit -> rolled back

    async with InMemoryUnitOfWork(queue) as uow:
        assert (
            await uow.input_queue.enqueue_representative_answered(event) is EnqueueOutcome.ENQUEUED
        )
        await uow.commit()
    assert event.event_id in queue.committed


async def test_duplicate_event_ids_are_deduplicated_or_rejected() -> None:
    queue = InMemoryInputQueue()
    event = _event()
    async with InMemoryUnitOfWork(queue) as uow:
        await uow.input_queue.enqueue_representative_answered(event)
        await uow.commit()
    async with InMemoryUnitOfWork(queue) as uow:
        assert (
            await uow.input_queue.enqueue_representative_answered(event) is EnqueueOutcome.DUPLICATE
        )
        with pytest.raises(InputPayloadMismatchError):
            await uow.input_queue.enqueue_representative_answered(_event(caseVersionAfter=5))
