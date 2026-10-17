"""`BusinessToolsPort`: the canonical model-facing business tools. STATUS: PROVISIONAL.

Implemented by the Business Application (`BusinessToolsFacade`, later phases) and by the
contract-compliant fake in `tests/fakes/fake_business_port.py`. The AI Engine's tool adapter
calls these methods in-process (no internal HTTP). `ToolContext` is injected by server code.

Expected business outcomes and rejections are returned as `Envelope` values (status=error with
`ErrorInfo`), not raised. `get_conversation_messages` is owned by the AI Engine and is not here.
"""

from typing import Protocol, runtime_checkable

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

CANONICAL_TOOL_NAMES: tuple[str, ...] = (
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
)


@runtime_checkable
class BusinessToolsPort(Protocol):
    async def get_conversation_context(self, ctx: ToolContext) -> Envelope[CaseContext]: ...

    async def update_conversation_inputs(
        self, ctx: ToolContext, request: UpdateInputsRequest
    ) -> Envelope[UpdateInputsResult]: ...

    async def list_eligible_options(
        self, ctx: ToolContext, request: ListOptionsRequest
    ) -> Envelope[ListOptionsResult]: ...

    async def resolve_vehicle(
        self, ctx: ToolContext, request: ResolveVehicleRequest
    ) -> Envelope[ResolveVehicleResult]: ...

    async def get_vehicle_tax_value(
        self, ctx: ToolContext, request: TaxValueRequest
    ) -> Envelope[TaxValueResult]: ...

    async def check_eligibility(
        self, ctx: ToolContext, request: EligibilityRequest
    ) -> Envelope[EligibilityResult]: ...

    async def check_calculation_readiness(
        self, ctx: ToolContext, request: ReadinessRequest
    ) -> Envelope[ReadinessResult]: ...

    async def compare_scenarios(
        self, ctx: ToolContext, request: CompareRequest
    ) -> Envelope[ComparisonResult]: ...

    async def create_representative_question(
        self, ctx: ToolContext, request: CreateQuestionRequest
    ) -> Envelope[CreateQuestionResult]: ...

    async def get_comparison_details(
        self, ctx: ToolContext, request: ComparisonDetailsRequest
    ) -> Envelope[ComparisonDetails]: ...
