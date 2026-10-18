"""Business case routes: one thin route per canonical business tool (Step 4D).

Every route takes the trusted `ToolContext` from `case_context` (verified token + owned case)
and calls `BusinessToolsPort` - the same facade the agent uses, so there is one set of rules,
authorization and idempotency. Bodies are the shared contract request models, unchanged;
responses are the contract `Envelope` (200, including business outcomes such as missing
information or ineligibility) or Problem Details for errors. There is no route that creates
a case: cases are created with conversations by the AI-owned conversation API.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path

from app.api.business.dependencies import Api, TrustedContext, respond
from app.api.business.route import BusinessRoute
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
from app.contracts.common import Envelope
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

router = APIRouter(prefix="/cases/{caseId}", tags=["cases"], route_class=BusinessRoute)


@router.get("", response_model=Envelope[CaseContext])
async def get_case(ctx: TrustedContext, api: Api) -> Envelope[CaseContext]:
    return respond(await api.tools.get_conversation_context(ctx))


@router.patch("", response_model=Envelope[UpdateInputsResult])
async def update_case(
    body: UpdateInputsRequest, ctx: TrustedContext, api: Api
) -> Envelope[UpdateInputsResult]:
    return respond(await api.tools.update_conversation_inputs(ctx, body))


@router.post("/profile-confirmations", response_model=Envelope[ConfirmProfileFieldsResult])
async def confirm_fields(
    body: ConfirmProfileFieldsRequest, ctx: TrustedContext, api: Api
) -> Envelope[ConfirmProfileFieldsResult]:
    return respond(await api.tools.confirm_profile_fields(ctx, body))


@router.post("/option-lists", response_model=Envelope[ListOptionsResult])
async def list_options(
    body: ListOptionsRequest, ctx: TrustedContext, api: Api
) -> Envelope[ListOptionsResult]:
    return respond(await api.tools.list_eligible_options(ctx, body))


@router.post("/vehicle-resolutions", response_model=Envelope[ResolveVehicleResult])
async def resolve_vehicle(
    body: ResolveVehicleRequest, ctx: TrustedContext, api: Api
) -> Envelope[ResolveVehicleResult]:
    return respond(await api.tools.resolve_vehicle(ctx, body))


@router.post("/vehicle-tax-values", response_model=Envelope[TaxValueResult])
async def tax_value(
    body: TaxValueRequest, ctx: TrustedContext, api: Api
) -> Envelope[TaxValueResult]:
    return respond(await api.tools.get_vehicle_tax_value(ctx, body))


@router.post("/eligibility-checks", response_model=Envelope[EligibilityResult])
async def check_eligibility(
    body: EligibilityRequest, ctx: TrustedContext, api: Api
) -> Envelope[EligibilityResult]:
    return respond(await api.tools.check_eligibility(ctx, body))


@router.post("/occupation-resolutions", response_model=Envelope[ResolveOccupationResult])
async def resolve_occupation(
    body: ResolveOccupationRequest, ctx: TrustedContext, api: Api
) -> Envelope[ResolveOccupationResult]:
    return respond(await api.tools.resolve_occupation(ctx, body))


@router.post("/readiness", response_model=Envelope[ReadinessResult])
async def readiness(
    body: ReadinessRequest, ctx: TrustedContext, api: Api
) -> Envelope[ReadinessResult]:
    return respond(await api.tools.check_calculation_readiness(ctx, body))


@router.post("/comparisons", response_model=Envelope[ComparisonResult])
async def compare(
    body: CompareRequest, ctx: TrustedContext, api: Api
) -> Envelope[ComparisonResult]:
    return respond(await api.tools.compare_scenarios(ctx, body))


@router.get("/comparisons/{runId}", response_model=Envelope[ComparisonDetails])
async def comparison_details(
    run_id: Annotated[UUID, Path(alias="runId")], ctx: TrustedContext, api: Api
) -> Envelope[ComparisonDetails]:
    request = ComparisonDetailsRequest(run_id=run_id)
    return respond(await api.tools.get_comparison_details(ctx, request))


@router.post("/representative-requests", response_model=Envelope[CreateQuestionResult])
async def create_question(
    body: CreateQuestionRequest, ctx: TrustedContext, api: Api
) -> Envelope[CreateQuestionResult]:
    return respond(await api.tools.create_representative_question(ctx, body))


@router.get("/representative-requests/{requestId}", response_model=Envelope[RequestStatusView])
async def get_request(
    request_id: Annotated[UUID, Path(alias="requestId")], ctx: TrustedContext, api: Api
) -> Envelope[RequestStatusView]:
    lookup = RepresentativeRequestLookup(request_id=request_id)
    return respond(await api.tools.get_representative_request(ctx, lookup))
