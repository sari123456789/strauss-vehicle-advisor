"""`/representative-requests`: the representative inbox and answers (B3b, architecture §9).

Role `representative` only: `RepresentativeAnswerService` revalidates the actor on every call
(`FORBIDDEN` otherwise). The views are the minimal `RepresentativeRequestView` (never the
employee's identity, salary, case or conversation). Answers use the shared `AnswerSubmit`
contract unchanged: version-checked (`expectedRequestVersion`) and idempotent by
`operationId` (the same ID and payload returns the stored answer; a second answer to the
request is `409`). An applied answer commits its outbox event in the same transaction; the AI
relay then resumes the employee's conversation.
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
from app.contracts.common import Envelope, EnvelopeStatus, ErrorCode
from app.contracts.representatives import (
    AnswerSubmit,
    AnswerView,
    RepresentativeRequestView,
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


@router.get("", response_model=Envelope[list[RepresentativeRequestView]])
async def list_requests(
    request: Request,
    actor: Actor,
    api: Api,
    status: Annotated[list[RequestStatus] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = MAX_LIST,
) -> Envelope[list[RepresentativeRequestView]]:
    """Oldest first; `status` may repeat (default `open`)."""
    try:
        views = await _service(api).list_requests(
            actor, statuses=status or [RequestStatus.OPEN], limit=limit
        )
    except BusinessError as error:
        raise as_problem(error) from None
    return Envelope[list[RepresentativeRequestView]](
        status=EnvelopeStatus.OK, data=views, trace_id=request_trace_id(request)
    )


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


@router.post("/{requestId}/answers", response_model=Envelope[AnswerView])
async def submit_answer(
    request: Request, actor: Actor, api: Api, request_id: RequestId, body: AnswerSubmit
) -> Envelope[AnswerView]:
    try:
        answer = await _service(api).submit_answer(actor, request_id, body)
    except BusinessError as error:
        raise as_problem(error) from None
    return Envelope[AnswerView](
        status=EnvelopeStatus.OK, data=answer, trace_id=request_trace_id(request)
    )
