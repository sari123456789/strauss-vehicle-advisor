"""Business API wiring: the injected services, HTTP settings and request dependencies.

`BusinessApi` is built by the composition root (bootstrap, after coordination) and installed
on the FastAPI app; routers read it from `app.state`. The actor is constructed only from a
verified bearer access token (`AuthService.authenticate`: roles from the store,
`Origin.HTTP`); a case route turns it into a trusted `ToolContext` through
`CaseService.context_for_case`, so HTTP passes the same checks as agent tools.
"""

from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.business.problem import ApiProblem, problem_from_envelope
from app.api.business.route import request_trace_id
from app.business.application.cases.case_service import CaseService
from app.business.application.errors import BusinessError
from app.business.application.identity.auth_service import AuthService
from app.business.application.profiles.profile_service import EmployeeProfileService
from app.business.application.representatives.answer_service import RepresentativeAnswerService
from app.contracts.business_port import BusinessToolsPort
from app.contracts.common import Envelope, EnvelopeStatus, ErrorCode
from app.contracts.tool_context import ActorRef, ToolContext

CSRF_HEADER = "X-CSRF-Protection"


@dataclass(frozen=True, slots=True)
class HttpConfig:
    refresh_cookie_name: str = "strauss_refresh"
    refresh_cookie_path: str = "/api/v1/auth"
    cookie_secure: bool = True
    """Must stay true outside local HTTP development."""
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    """Browser origins allowed to call the cookie-based auth routes (the Angular app)."""
    cookie_samesite: Literal["strict", "lax", "none"] = "strict"
    """`none` only when the Angular app and the API are on different sites (needs `Secure`)."""


@dataclass(frozen=True, slots=True)
class BusinessApi:
    auth: AuthService
    tools: BusinessToolsPort
    cases: CaseService
    profiles: EmployeeProfileService
    http: HttpConfig = field(default_factory=HttpConfig)
    representatives: RepresentativeAnswerService | None = None
    """Representative inbox and answers (B3b); the routes answer 503 when it is not wired."""


def business_api(request: Request) -> BusinessApi:
    api = getattr(request.app.state, "business_api", None)
    if not isinstance(api, BusinessApi):
        raise ApiProblem(ErrorCode.INFRA_UNAVAILABLE, details={"reason": "not_configured"})
    return api


Api = Annotated[BusinessApi, Depends(business_api)]
bearer = HTTPBearer(auto_error=False, description="Access token from /api/v1/auth/login")


def as_problem(error: BusinessError) -> ApiProblem:
    return ApiProblem(error.code, details=error.safe_details, issues=list(error.issues))


async def current_actor(
    api: Api,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> ActorRef:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiProblem(ErrorCode.UNAUTHENTICATED)
    try:
        return await api.auth.authenticate(credentials.credentials)
    except BusinessError:
        raise ApiProblem(ErrorCode.UNAUTHENTICATED) from None


Actor = Annotated[ActorRef, Depends(current_actor)]


async def case_context(
    request: Request,
    api: Api,
    actor: Actor,
    case_id: Annotated[UUID, Path(alias="caseId")],
) -> ToolContext:
    try:
        return await api.cases.context_for_case(actor, case_id, request_trace_id(request))
    except BusinessError as error:
        raise as_problem(error) from None


TrustedContext = Annotated[ToolContext, Depends(case_context)]


def require_csrf(request: Request, api: Api) -> None:
    """Cookie-authenticated routes: a custom header (forces a CORS preflight cross-site) and,
    when the browser sends one, an allowed `Origin`."""
    origin = request.headers.get("Origin")
    allowed = request.headers.get(CSRF_HEADER) == "1" and (
        origin is None or origin in api.http.allowed_origins
    )
    if not allowed:
        raise ApiProblem(ErrorCode.FORBIDDEN, details={"reason": "csrf"})


def respond[T](envelope: Envelope[T]) -> Envelope[T]:
    """Business outcomes (incl. missing information) are 200; errors become Problem Details."""
    if envelope.status is EnvelopeStatus.ERROR:
        raise problem_from_envelope(envelope)
    return envelope
