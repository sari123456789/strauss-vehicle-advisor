"""Budgeted adapter from the workflow to the injected `BusinessToolsPort`.

- The `ToolContext` is fixed at construction by trusted server code; the workflow cannot pass
  or alter identity, conversation or case references.
- At most `max_calls` tool calls per turn (architecture §15); exceeding it raises
  `ToolBudgetExceeded`, which the workflow turns into an explicit outcome.
- Expected business outcomes are returned by the port as `Envelope` values. An unexpected
  exception is converted to an error envelope (`INFRA_UNAVAILABLE`) and never to a value.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.contracts.business_port import BusinessToolsPort
from app.contracts.case_inputs import (
    ConfirmProfileFieldsRequest,
    ConfirmProfileFieldsResult,
    UpdateInputsRequest,
    UpdateInputsResult,
)
from app.contracts.cases import CaseContext
from app.contracts.catalogue import (
    EligibilityRequest,
    EligibilityResult,
    ListOptionsRequest,
    ListOptionsResult,
    ResolveOccupationRequest,
    ResolveOccupationResult,
    ResolveVehicleRequest,
    ResolveVehicleResult,
    TaxValueRequest,
    TaxValueResult,
)
from app.contracts.common import Envelope, EnvelopeStatus, ErrorCode, ErrorInfo
from app.contracts.comparisons import (
    CompareRequest,
    ComparisonDetails,
    ComparisonDetailsRequest,
    ComparisonResult,
)
from app.contracts.readiness import ReadinessRequest, ReadinessResult
from app.contracts.representatives import (
    CreateQuestionRequest,
    CreateQuestionResult,
    RepresentativeRequestLookup,
    RequestStatusView,
)
from app.contracts.tool_context import ToolContext

logger = logging.getLogger("app.ai.tools")

DEFAULT_MAX_TOOL_CALLS = 12
T = TypeVar("T")


class ToolBudgetExceeded(Exception):
    """The per-turn tool-call budget is used up."""


class BusinessToolGateway:
    def __init__(
        self, port: BusinessToolsPort, ctx: ToolContext, max_calls: int = DEFAULT_MAX_TOOL_CALLS
    ) -> None:
        self._port = port
        self._ctx = ctx
        self._max_calls = max_calls
        self.calls: list[str] = []

    @property
    def trace_id(self) -> str:
        return self._ctx.trace_id

    async def _invoke(self, name: str, call: Callable[[], Awaitable[Envelope[T]]]) -> Envelope[T]:
        if len(self.calls) >= self._max_calls:
            raise ToolBudgetExceeded(name)
        self.calls.append(name)
        try:
            return await call()
        except Exception as error:  # never let a tool failure become a value
            logger.warning(
                "tool_failure tool=%s error=%s trace=%s",
                name,
                type(error).__name__,
                self._ctx.trace_id,
            )
            return Envelope[Any](
                status=EnvelopeStatus.ERROR,
                error=ErrorInfo(code=ErrorCode.INFRA_UNAVAILABLE, trace_id=self._ctx.trace_id),
                trace_id=self._ctx.trace_id,
            )

    async def get_conversation_context(self) -> Envelope[CaseContext]:
        return await self._invoke(
            "get_conversation_context", lambda: self._port.get_conversation_context(self._ctx)
        )

    async def update_conversation_inputs(
        self, request: UpdateInputsRequest
    ) -> Envelope[UpdateInputsResult]:
        return await self._invoke(
            "update_conversation_inputs",
            lambda: self._port.update_conversation_inputs(self._ctx, request),
        )

    async def list_eligible_options(
        self, request: ListOptionsRequest
    ) -> Envelope[ListOptionsResult]:
        return await self._invoke(
            "list_eligible_options", lambda: self._port.list_eligible_options(self._ctx, request)
        )

    async def resolve_vehicle(
        self, request: ResolveVehicleRequest
    ) -> Envelope[ResolveVehicleResult]:
        return await self._invoke(
            "resolve_vehicle", lambda: self._port.resolve_vehicle(self._ctx, request)
        )

    async def get_vehicle_tax_value(self, request: TaxValueRequest) -> Envelope[TaxValueResult]:
        return await self._invoke(
            "get_vehicle_tax_value", lambda: self._port.get_vehicle_tax_value(self._ctx, request)
        )

    async def check_eligibility(self, request: EligibilityRequest) -> Envelope[EligibilityResult]:
        return await self._invoke(
            "check_eligibility", lambda: self._port.check_eligibility(self._ctx, request)
        )

    async def check_calculation_readiness(
        self, request: ReadinessRequest
    ) -> Envelope[ReadinessResult]:
        return await self._invoke(
            "check_calculation_readiness",
            lambda: self._port.check_calculation_readiness(self._ctx, request),
        )

    async def compare_scenarios(self, request: CompareRequest) -> Envelope[ComparisonResult]:
        return await self._invoke(
            "compare_scenarios", lambda: self._port.compare_scenarios(self._ctx, request)
        )

    async def create_representative_question(
        self, request: CreateQuestionRequest
    ) -> Envelope[CreateQuestionResult]:
        return await self._invoke(
            "create_representative_question",
            lambda: self._port.create_representative_question(self._ctx, request),
        )

    async def get_comparison_details(
        self, request: ComparisonDetailsRequest
    ) -> Envelope[ComparisonDetails]:
        return await self._invoke(
            "get_comparison_details",
            lambda: self._port.get_comparison_details(self._ctx, request),
        )

    async def get_representative_request(
        self, request: RepresentativeRequestLookup
    ) -> Envelope[RequestStatusView]:
        return await self._invoke(
            "get_representative_request",
            lambda: self._port.get_representative_request(self._ctx, request),
        )

    async def resolve_occupation(
        self, request: ResolveOccupationRequest
    ) -> Envelope[ResolveOccupationResult]:
        return await self._invoke(
            "resolve_occupation", lambda: self._port.resolve_occupation(self._ctx, request)
        )

    async def confirm_profile_fields(
        self, request: ConfirmProfileFieldsRequest
    ) -> Envelope[ConfirmProfileFieldsResult]:
        return await self._invoke(
            "confirm_profile_fields",
            lambda: self._port.confirm_profile_fields(self._ctx, request),
        )
