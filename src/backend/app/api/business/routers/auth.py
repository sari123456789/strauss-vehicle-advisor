"""`/auth`: sign-in, refresh, sign-out, current actor (architecture §22).

The access token is returned in the body (kept in memory by the client); the refresh token is
set only as an `HttpOnly; Secure` cookie scoped to `/api/v1/auth`, `SameSite=Strict` unless
`HttpConfig.cookie_samesite` says otherwise (`none` for a cross-site deployment). Refresh
and logout are cookie-authenticated and therefore CSRF-protected (`require_csrf`).
"""

from fastapi import APIRouter, Depends, Request, Response

from app.api.business.dependencies import Actor, Api, BusinessApi, as_problem, require_csrf
from app.api.business.route import BusinessRoute
from app.api.business.schemas import ActorView, LoginRequest, SessionResponse
from app.business.application.errors import BusinessError
from app.business.application.identity.auth_service import SessionTokens

router = APIRouter(prefix="/auth", tags=["auth"], route_class=BusinessRoute)


def _session(tokens: SessionTokens, api: BusinessApi, response: Response) -> SessionResponse:
    http = api.http
    response.set_cookie(
        http.refresh_cookie_name,
        tokens.refresh_token,
        expires=tokens.refresh_expires_at,
        path=http.refresh_cookie_path,
        secure=http.cookie_secure,
        httponly=True,
        samesite=http.cookie_samesite,
    )
    return SessionResponse(
        access_token=tokens.access_token,
        expires_at=tokens.access_expires_at,
        actor=ActorView(
            subject_id=tokens.actor.subject_id,
            roles=sorted(tokens.actor.roles),
            display_name=tokens.display_name,
        ),
    )


def _clear(api: BusinessApi, response: Response) -> None:
    http = api.http
    response.delete_cookie(
        http.refresh_cookie_name,
        path=http.refresh_cookie_path,
        secure=http.cookie_secure,
        httponly=True,
        samesite=http.cookie_samesite,
    )


@router.post("/login", response_model=SessionResponse)
async def login(body: LoginRequest, api: Api, response: Response) -> SessionResponse:
    try:
        tokens = await api.auth.login(body.login, body.password)
    except BusinessError as error:
        raise as_problem(error) from None
    return _session(tokens, api, response)


@router.post("/refresh", response_model=SessionResponse, dependencies=[Depends(require_csrf)])
async def refresh(request: Request, api: Api, response: Response) -> SessionResponse:
    token = request.cookies.get(api.http.refresh_cookie_name, "")
    try:
        tokens = await api.auth.refresh(token)
    except BusinessError as error:
        problem = as_problem(error)
        _clear(api, response)
        problem.headers["Set-Cookie"] = response.headers.get("set-cookie", "")
        raise problem from None
    return _session(tokens, api, response)


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf)])
async def logout(request: Request, api: Api) -> Response:
    await api.auth.logout(request.cookies.get(api.http.refresh_cookie_name))
    response = Response(status_code=204)
    _clear(api, response)
    return response


@router.get("/me", response_model=ActorView)
async def me(actor: Actor) -> ActorView:
    return ActorView(subject_id=actor.subject_id, roles=sorted(actor.roles))
