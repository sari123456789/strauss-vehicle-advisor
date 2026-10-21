"""`/representative-requests`: representative inbox, claim/release and answers (B3b + claiming).

Role `representative` only: all services revalidate the actor on every call.
Views expose only minimal question context (never employee salary, profile or identity).

Route order matters: `/summary` must be declared BEFORE `/{requestId}` so FastAPI does not
match the literal string "summary" as a UUID path parameter.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request

from app.api.business.dependencies import Actor, Api, BusinessApi, as_problem
from app.api.business.problem import ApiProblem
from app.api.business.route import BusinessRoute, request_trace_id
from app.business.application.errors import BusinessError
from app.business.application.representatives.answer_service import (
    MAX_LIST,
    RepresentativeAnswerService,
)
from app.business.application.representatives.assignment_service import AssignmentService
from app.contracts.common import Envelope, EnvelopeStatus, ErrorCode
from app.contracts.representatives import (
    AnswerSubmit,
    AnswerView,
    AssignmentFilter,
    ClaimRequest,
    ClaimResult,
    ReleaseRequest,
    ReleaseResult,
    RepresentativeFieldCode,
    RepresentativeRequestView,
    RepresentativeSummary,
    RequestStatus,
)

router = APIRouter(
    prefix="/representative-requests", tags=["representatives"], route_class=BusinessRoute
)

RequestId = Annotated[UUID, Path(alias="requestId")]


def _service(api: BusinessApi) -> RepresentativeAnswerService:
    if api.representatives is None:
        raise ApiProblem(ErrorCode.INFRA_UNAVAILABLE, details={"reason": "not_configured"})
    return api.representatives


def _assignment_service(api: BusinessApi) -> AssignmentService:
    if api.assignments is None:
        raise ApiProblem(ErrorCode.INFRA_UNAVAILABLE, details={"reason": "not_configured"})
    return api.assignments


# ── Summary (MUST be before /{requestId}) ────────────────────────────────────────────────


@router.get("/summary", response_model=Envelope[RepresentativeSummary])
async def get_summary(
    request: Request,
    actor: Actor,
    api: Api,
) -> Envelope[RepresentativeSummary]:
    """Accurate counts over the entire queue, independent of pagination."""
    try:
        summary = await _assignment_service(api).get_summary(actor)
    except BusinessError as error:
        raise as_problem(error) from None
    return Envelope[RepresentativeSummary](
        status=EnvelopeStatus.OK, data=summary, trace_id=request_trace_id(request)
    )


# ── List ─────────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=Envelope[list[RepresentativeRequestView]])
async def list_requests(
    request: Request,
    actor: Actor,
    api: Api,
    status: Annotated[list[RequestStatus] | None, Query()] = None,
    assignment: Annotated[AssignmentFilter | None, Query()] = None,
    field_code: Annotated[RepresentativeFieldCode | None, Query(alias="fieldCode")] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = MAX_LIST,
) -> Envelope[list[RepresentativeRequestView]]:
    """Queue list with optional filters. Oldest first, stable by (createdAt, requestId).

    `status`: business status filter (repeatable, default open).
    `assignment`: all (default) | unclaimed | mine | others.
    `fieldCode`: filter by representative field code.
    `search`: case-insensitive substring of the question text or the request ID.
    `after`: next page = requests after this request ID (the last ID of the previous page).
    `limit`: page size (max 200).
    """
    try:
        views = await _service(api).list_requests(
            actor,
            statuses=status or [RequestStatus.OPEN],
            assignment_filter=assignment,
            field_code=field_code,
            search=search,
            after_id=after,
            limit=limit,
        )
    except BusinessError as error:
        raise as_problem(error) from None
    return Envelope[list[RepresentativeRequestView]](
        status=EnvelopeStatus.OK, data=views, trace_id=request_trace_id(request)
    )


# ── Single request ────────────────────────────────────────────────────────────────────────


@router.get("/{requestId}", response_model=Envelope[RepresentativeRequestView])
async def get_request(
    request: Request, actor: Actor, api: Api, request_id: RequestId
) -> Envelope[RepresentativeRequestView]:
    try:
        view = await _service(api).get_request(actor, request_id)
    except BusinessError as error:
        raise as_problem(error) from None
    return Envelope[RepresentativeRequestView](
        status=EnvelopeStatus.OK, data=view, trace_id=request_trace_id(request)
    )


# ── Claim ─────────────────────────────────────────────────────────────────────────────────


@router.post("/{requestId}/claim", response_model=Envelope[ClaimResult])
async def claim_request(
    request: Request, actor: Actor, api: Api, request_id: RequestId, body: ClaimRequest
) -> Envelope[ClaimResult]:
    """Claim an open, unclaimed request.  Idempotent by operationId."""
    try:
        result = await _assignment_service(api).claim_request(actor, request_id, body)
    except BusinessError as error:
        raise as_problem(error) from None
    return Envelope[ClaimResult](
        status=EnvelopeStatus.OK, data=result, trace_id=request_trace_id(request)
    )


# ── Release ───────────────────────────────────────────────────────────────────────────────


@router.post("/{requestId}/release", response_model=Envelope[ReleaseResult])
async def release_request(
    request: Request, actor: Actor, api: Api, request_id: RequestId, body: ReleaseRequest
) -> Envelope[ReleaseResult]:
    """Release a claimed request (handler only).  Idempotent by operationId."""
    try:
        result = await _assignment_service(api).release_request(actor, request_id, body)
    except BusinessError as error:
        raise as_problem(error) from None
    return Envelope[ReleaseResult](
        status=EnvelopeStatus.OK, data=result, trace_id=request_trace_id(request)
    )


# ── Answer ────────────────────────────────────────────────────────────────────────────────


@router.post("/{requestId}/answers", response_model=Envelope[AnswerView])
async def submit_answer(
    request: Request, actor: Actor, api: Api, request_id: RequestId, body: AnswerSubmit
) -> Envelope[AnswerView]:
    """Submit an answer (handler only — must claim first)."""
    try:
        answer = await _service(api).submit_answer(actor, request_id, body)
    except BusinessError as error:
        raise as_problem(error) from None
    return Envelope[AnswerView](
        status=EnvelopeStatus.OK, data=answer, trace_id=request_trace_id(request)
    )
