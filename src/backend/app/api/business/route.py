"""`BusinessRoute`: per-route error handling and response hardening for the Business API.

Scoped to the Business routers only (no application-wide handlers, so AI-owned routes are
unaffected):
- a trace ID per request (`X-Request-ID` if well-formed, else generated) echoed in the response;
- request validation errors -> 400 `MALFORMED_REQUEST` listing only field locations and error
  types - submitted values are never echoed (they may be salary data);
- `ApiProblem` -> its Problem Details; anything unexpected -> 500 with no details, logged by
  exception type and trace ID only;
- `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
"""

import logging
import re
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.api.business.problem import INTERNAL_REASON, ApiProblem, problem_response
from app.contracts.common import ErrorCode

logger = logging.getLogger(__name__)
TRACE_HEADER = "X-Request-ID"
_TRACE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
MAX_LOCATIONS = 20


def request_trace_id(request: Request) -> str:
    existing = getattr(request.state, "trace_id", None)
    if isinstance(existing, str):
        return existing
    presented = request.headers.get(TRACE_HEADER, "")
    trace_id = presented if _TRACE.fullmatch(presented) else uuid.uuid4().hex
    request.state.trace_id = trace_id
    return trace_id


def _locations(error: RequestValidationError) -> str:
    seen: list[str] = []
    for item in error.errors()[:MAX_LOCATIONS]:
        location = ".".join(str(part) for part in item.get("loc", ()))
        entry = f"{location}:{item.get('type', 'invalid')}"
        if entry not in seen:
            seen.append(entry)
    return ",".join(seen)


class BusinessRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            trace_id = request_trace_id(request)
            try:
                response = await handler(request)
            except RequestValidationError as error:
                invalid = ApiProblem(
                    ErrorCode.MALFORMED_REQUEST, details={"fields": _locations(error)}
                )
                response = problem_response(invalid, trace_id)
            except ApiProblem as problem:
                response = problem_response(problem, trace_id)
            except Exception as error:  # noqa: BLE001 - nothing unexpected leaves the API
                logger.error(
                    "business route %s failed (%s), trace %s",
                    self.path,
                    type(error).__name__,
                    trace_id,
                )
                internal = ApiProblem(
                    ErrorCode.INFRA_UNAVAILABLE, details={"reason": INTERNAL_REASON}, status=500
                )
                response = problem_response(internal, trace_id)
            response.headers[TRACE_HEADER] = trace_id
            for name, value in SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
            return response

        return guarded
