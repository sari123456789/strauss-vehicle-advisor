"""Liveness (`/health`) and readiness (`/health/ready`).

Readiness runs the probe the composition root stores as `app.state.readiness_probe`: the
databases are reachable, a reference release is published and, when enabled, the in-process
worker is alive. The response carries booleans and counters only, never connection details.
Not ready is `503` with a Problem Details body (`code=INFRA_UNAVAILABLE`).
"""

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(tags=["health"])

PROBLEM_JSON = "application/problem+json"


class HealthResponse(BaseModel):
    status: Literal["ok"]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", responses={503: {"description": "Not ready (Problem Details)"}})
async def ready(request: Request) -> JSONResponse:
    probe: Callable[[], Awaitable[dict[str, Any]]] | None = getattr(
        request.app.state, "readiness_probe", None
    )
    if probe is None:
        return _not_ready({"configured": False}, {})
    result = await probe()
    if not result["ready"]:
        return _not_ready(result["checks"], result.get("info", {}))
    return JSONResponse(
        {"status": "ready", "checks": result["checks"], "info": result.get("info", {})}
    )


def _not_ready(checks: dict[str, Any], info: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        {
            "type": "about:blank",
            "title": "Service not ready",
            "status": 503,
            "code": "INFRA_UNAVAILABLE",
            "checks": checks,
            "info": info,
        },
        status_code=503,
        media_type=PROBLEM_JSON,
    )
