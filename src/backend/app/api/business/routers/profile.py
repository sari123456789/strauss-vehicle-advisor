"""`GET /me/profile`: the employee's own actual EmployeeProfile for the side panel.

Read-only; no blanket confirmation. Another subject's profile is never addressable (the
subject is always the authenticated actor). Route name is provisional (open review item).
"""

from fastapi import APIRouter, Request

from app.api.business.dependencies import Actor, Api, as_problem
from app.api.business.route import BusinessRoute, request_trace_id
from app.business.application.errors import BusinessError
from app.contracts.common import Envelope, EnvelopeStatus
from app.contracts.profile import EmployeeProfileView
from app.contracts.representatives import MyRequestView

router = APIRouter(prefix="/me", tags=["profile"], route_class=BusinessRoute)


@router.get("/profile", response_model=Envelope[EmployeeProfileView])
async def my_profile(request: Request, actor: Actor, api: Api) -> Envelope[EmployeeProfileView]:
    try:
        view = await api.profiles.get_profile(actor)
    except BusinessError as error:
        raise as_problem(error) from None
    return Envelope[EmployeeProfileView](
        status=EnvelopeStatus.OK,
        data=view,
        profile_version=view.profile_version,
        trace_id=request_trace_id(request),
    )


@router.get("/representative-requests", response_model=Envelope[list[MyRequestView]])
async def my_representative_requests(
    request: Request, actor: Actor, api: Api
) -> Envelope[list[MyRequestView]]:
    """The employee's own requests to a representative (chat and form), with their answers."""
    if api.requests is None:
        raise as_problem(BusinessError("representative requests are not configured"))
    try:
        views = await api.requests.list_mine(actor)
    except BusinessError as error:
        raise as_problem(error) from None
    return Envelope[list[MyRequestView]](
        status=EnvelopeStatus.OK, data=views, trace_id=request_trace_id(request)
    )
