"""Fixture-backed fake of `BusinessToolsPort` for AI Engine development and contract tests.

It performs NO calculation or eligibility logic: every response is a canned, schema-validated
envelope from `fixtures/`. Scenario files override `success.json` per tool; the `"*"` key
applies to every tool not explicitly overridden.

Usage (AI developer):
    port = FakeBusinessPort("blocked")
    envelope = await port.compare_scenarios(ctx, request)
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.contracts.business_port import CANONICAL_TOOL_NAMES
from app.contracts.case_inputs import UpdateInputsRequest, UpdateInputsResult
from app.contracts.cases import CaseContext
from app.contracts.catalogue import (
    EligibilityRequest,
    EligibilityResult,
    ListOptionsRequest,
    ListOptionsResult,
    ResolveVehicleRequest,
    ResolveVehicleResult,
    TaxValueRequest,
    TaxValueResult,
)
from app.contracts.common import Envelope
from app.contracts.comparisons import (
    CompareRequest,
    ComparisonDetails,
    ComparisonDetailsRequest,
    ComparisonResult,
)
from app.contracts.readiness import ReadinessRequest, ReadinessResult
from app.contracts.representatives import CreateQuestionRequest, CreateQuestionResult
from app.contracts.tool_context import ToolContext

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCENARIOS: tuple[str, ...] = ("success", "missing", "blocked", "stale", "unauthorized")

RESULT_TYPES: dict[str, type[BaseModel]] = {
    "get_conversation_context": CaseContext,
    "update_conversation_inputs": UpdateInputsResult,
    "list_eligible_options": ListOptionsResult,
    "resolve_vehicle": ResolveVehicleResult,
    "get_vehicle_tax_value": TaxValueResult,
    "check_eligibility": EligibilityResult,
    "check_calculation_readiness": ReadinessResult,
    "compare_scenarios": ComparisonResult,
    "create_representative_question": CreateQuestionResult,
    "get_comparison_details": ComparisonDetails,
}


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    data.pop("_meta", None)
    return data


class FakeBusinessPort:
    def __init__(self, scenario: str = "success") -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}")
        self.scenario = scenario
        base = _load("success")
        overrides = {} if scenario == "success" else _load(scenario)
        wildcard = overrides.pop("*", None)
        self._responses: dict[str, Envelope[Any]] = {}
        for tool in CANONICAL_TOOL_NAMES:
            raw = overrides.get(tool) or wildcard or base[tool]
            self._responses[tool] = Envelope[RESULT_TYPES[tool]].model_validate(raw)  # type: ignore[valid-type]
        self.calls: list[tuple[str, ToolContext, BaseModel | None]] = []

    def _respond(self, tool: str, ctx: ToolContext, request: BaseModel | None) -> Any:
        self.calls.append((tool, ctx, request))
        return self._responses[tool]

    async def get_conversation_context(self, ctx: ToolContext) -> Envelope[CaseContext]:
        return self._respond("get_conversation_context", ctx, None)

    async def update_conversation_inputs(
        self, ctx: ToolContext, request: UpdateInputsRequest
    ) -> Envelope[UpdateInputsResult]:
        return self._respond("update_conversation_inputs", ctx, request)

    async def list_eligible_options(
        self, ctx: ToolContext, request: ListOptionsRequest
    ) -> Envelope[ListOptionsResult]:
        return self._respond("list_eligible_options", ctx, request)

    async def resolve_vehicle(
        self, ctx: ToolContext, request: ResolveVehicleRequest
    ) -> Envelope[ResolveVehicleResult]:
        return self._respond("resolve_vehicle", ctx, request)

    async def get_vehicle_tax_value(
        self, ctx: ToolContext, request: TaxValueRequest
    ) -> Envelope[TaxValueResult]:
        return self._respond("get_vehicle_tax_value", ctx, request)

    async def check_eligibility(
        self, ctx: ToolContext, request: EligibilityRequest
    ) -> Envelope[EligibilityResult]:
        return self._respond("check_eligibility", ctx, request)

    async def check_calculation_readiness(
        self, ctx: ToolContext, request: ReadinessRequest
    ) -> Envelope[ReadinessResult]:
        return self._respond("check_calculation_readiness", ctx, request)

    async def compare_scenarios(
        self, ctx: ToolContext, request: CompareRequest
    ) -> Envelope[ComparisonResult]:
        return self._respond("compare_scenarios", ctx, request)

    async def create_representative_question(
        self, ctx: ToolContext, request: CreateQuestionRequest
    ) -> Envelope[CreateQuestionResult]:
        return self._respond("create_representative_question", ctx, request)

    async def get_comparison_details(
        self, ctx: ToolContext, request: ComparisonDetailsRequest
    ) -> Envelope[ComparisonDetails]:
        return self._respond("get_comparison_details", ctx, request)
