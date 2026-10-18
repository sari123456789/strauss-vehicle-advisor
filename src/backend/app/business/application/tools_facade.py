"""`BusinessToolsFacade`: the Business implementation of `BusinessToolsPort` (Step 4C).

A thin adapter over the Business Application services: no business logic lives here.
Each tool call receives the server-built `ToolContext` (identity is never a tool argument);
services authorize, version-check and apply idempotency themselves. Every outcome is a
contract `Envelope` (the port never raises for business outcomes):

- success -> `ok` / `partial` / `needs_information`, with issues, versions and sources;
- `BusinessError` -> `status=error` with its stable code, safe details and issues;
- anything unexpected -> `status=error`, `INFRA_UNAVAILABLE`, no details (no stack trace or
  payload ever leaves this boundary). It is logged with the tool name and trace ID only - no
  arguments, so no salary data reaches the logs. The contracts have no generic internal-error
  code, so `INFRA_UNAVAILABLE` is used (reported for joint review).

`traceId` is the tool context's trace ID, so one conversation turn is traceable end to end.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.business.application.cases.case_service import CaseService
from app.business.application.comparisons.comparison_service import ComparisonService
from app.business.application.comparisons.readiness_service import ReadinessService
from app.business.application.errors import (
    BusinessError,
    InfrastructureUnavailable,
    error_envelope,
)
from app.business.application.occupations.occupation_service import OccupationService
from app.business.application.outcomes import Outcome
from app.business.application.representatives.question_service import (
    RepresentativeQuestionService,
)
from app.business.application.representatives.request_service import (
    RepresentativeRequestService,
)
from app.business.application.vehicles.catalogue_service import CatalogueService
from app.business.application.vehicles.valuation_service import ValuationService
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
from app.contracts.common import Envelope, EnvelopeStatus
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

logger = logging.getLogger(__name__)


class BusinessToolsFacade:
    def __init__(
        self,
        *,
        cases: CaseService,
        catalogue: CatalogueService,
        valuation: ValuationService,
        occupations: OccupationService,
        readiness: ReadinessService,
        comparisons: ComparisonService,
        questions: RepresentativeQuestionService,
        requests: RepresentativeRequestService,
    ) -> None:
        self._cases = cases
        self._catalogue = catalogue
        self._valuation = valuation
        self._occupations = occupations
        self._readiness = readiness
        self._comparisons = comparisons
        self._questions = questions
        self._requests = requests

    async def _call[T](
        self,
        tool: str,
        ctx: ToolContext,
        result_type: type[T],
        call: Callable[[], Awaitable[Outcome[T]]],
    ) -> Envelope[T]:
        try:
            outcome = await call()
        except BusinessError as error:
            return _typed(result_type, error_envelope(error, ctx.trace_id))
        except Exception as error:  # noqa: BLE001 - nothing unexpected crosses the boundary
            logger.error(
                "tool %s failed unexpectedly (%s), trace %s",
                tool,
                type(error).__name__,
                ctx.trace_id,
            )
            unavailable = InfrastructureUnavailable("the service is temporarily unavailable")
            return _typed(result_type, error_envelope(unavailable, ctx.trace_id))
        envelope: Envelope[T] = Envelope[result_type](  # type: ignore[valid-type]
            status=outcome.status,
            data=outcome.data,
            issues=list(outcome.issues),
            case_version=outcome.case_version,
            profile_version=outcome.profile_version,
            source_versions=outcome.source_versions,
            trace_id=ctx.trace_id,
        )
        return envelope

    # --- case context and inputs -------------------------------------------------------

    async def get_conversation_context(self, ctx: ToolContext) -> Envelope[CaseContext]:
        async def call() -> Outcome[CaseContext]:
            context = await self._cases.get_context(ctx)
            return Outcome(
                context,
                case_version=context.case_version,
                profile_version=context.latest_profile_version,
            )

        return await self._call("get_conversation_context", ctx, CaseContext, call)

    async def update_conversation_inputs(
        self, ctx: ToolContext, request: UpdateInputsRequest
    ) -> Envelope[UpdateInputsResult]:
        async def call() -> Outcome[UpdateInputsResult]:
            result = await self._cases.update_inputs(ctx, request)
            return Outcome(
                result,
                issues=tuple(result.conflicts + result.missing_information),
                case_version=result.new_case_version,
                profile_version=result.new_profile_version,
            )

        return await self._call("update_conversation_inputs", ctx, UpdateInputsResult, call)

    async def confirm_profile_fields(
        self, ctx: ToolContext, request: ConfirmProfileFieldsRequest
    ) -> Envelope[ConfirmProfileFieldsResult]:
        async def call() -> Outcome[ConfirmProfileFieldsResult]:
            result = await self._cases.confirm_profile_fields(ctx, request)
            if not result.not_confirmable:
                status = EnvelopeStatus.OK
            elif result.confirmed_fields:
                status = EnvelopeStatus.PARTIAL
            else:
                status = EnvelopeStatus.NEEDS_INFORMATION
            return Outcome(
                result,
                status=status,
                issues=tuple(result.not_confirmable),
                case_version=result.new_case_version,
                profile_version=result.new_profile_version,
            )

        return await self._call("confirm_profile_fields", ctx, ConfirmProfileFieldsResult, call)

    # --- catalogue, eligibility, valuation ---------------------------------------------

    async def list_eligible_options(
        self, ctx: ToolContext, request: ListOptionsRequest
    ) -> Envelope[ListOptionsResult]:
        return await self._call(
            "list_eligible_options",
            ctx,
            ListOptionsResult,
            lambda: self._catalogue.list_eligible_options(ctx, request),
        )

    async def check_eligibility(
        self, ctx: ToolContext, request: EligibilityRequest
    ) -> Envelope[EligibilityResult]:
        return await self._call(
            "check_eligibility",
            ctx,
            EligibilityResult,
            lambda: self._catalogue.check_eligibility(ctx, request),
        )

    async def resolve_vehicle(
        self, ctx: ToolContext, request: ResolveVehicleRequest
    ) -> Envelope[ResolveVehicleResult]:
        return await self._call(
            "resolve_vehicle",
            ctx,
            ResolveVehicleResult,
            lambda: self._valuation.resolve_vehicle(ctx, request),
        )

    async def get_vehicle_tax_value(
        self, ctx: ToolContext, request: TaxValueRequest
    ) -> Envelope[TaxValueResult]:
        return await self._call(
            "get_vehicle_tax_value",
            ctx,
            TaxValueResult,
            lambda: self._valuation.get_vehicle_tax_value(ctx, request),
        )

    async def resolve_occupation(
        self, ctx: ToolContext, request: ResolveOccupationRequest
    ) -> Envelope[ResolveOccupationResult]:
        return await self._call(
            "resolve_occupation",
            ctx,
            ResolveOccupationResult,
            lambda: self._occupations.resolve_occupation(ctx, request),
        )

    # --- readiness and comparisons -----------------------------------------------------

    async def check_calculation_readiness(
        self, ctx: ToolContext, request: ReadinessRequest
    ) -> Envelope[ReadinessResult]:
        return await self._call(
            "check_calculation_readiness",
            ctx,
            ReadinessResult,
            lambda: self._readiness.check_calculation_readiness(ctx, request),
        )

    async def compare_scenarios(
        self, ctx: ToolContext, request: CompareRequest
    ) -> Envelope[ComparisonResult]:
        return await self._call(
            "compare_scenarios",
            ctx,
            ComparisonResult,
            lambda: self._comparisons.compare_scenarios(ctx, request),
        )

    async def get_comparison_details(
        self, ctx: ToolContext, request: ComparisonDetailsRequest
    ) -> Envelope[ComparisonDetails]:
        return await self._call(
            "get_comparison_details",
            ctx,
            ComparisonDetails,
            lambda: self._comparisons.get_comparison_details(ctx, request),
        )

    # --- representative requests -------------------------------------------------------

    async def create_representative_question(
        self, ctx: ToolContext, request: CreateQuestionRequest
    ) -> Envelope[CreateQuestionResult]:
        return await self._call(
            "create_representative_question",
            ctx,
            CreateQuestionResult,
            lambda: self._questions.create_representative_question(ctx, request),
        )

    async def get_representative_request(
        self, ctx: ToolContext, request: RepresentativeRequestLookup
    ) -> Envelope[RequestStatusView]:
        return await self._call(
            "get_representative_request",
            ctx,
            RequestStatusView,
            lambda: self._requests.get_representative_request(ctx, request),
        )


def _typed[T](result_type: type[T], envelope: Envelope[Any]) -> Envelope[T]:
    typed: Envelope[T] = Envelope[result_type](  # type: ignore[valid-type]
        **envelope.model_dump()
    )
    return typed
