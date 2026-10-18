"""Safe HTTP errors for the Business API: Problem Details (RFC 9457) - PROVISIONAL HTTP DTO.

Fields follow architecture §10 (`type`, `title`, `status`, `code`, `traceId`, safe details)
plus the business `issues` (e.g. `VEHICLE_NOT_FOUND`). Titles are fixed English diagnostics;
the employee-facing Hebrew text is chosen by the client from `code` (never raw HTTP codes,
stack traces, exception messages or another employee's data). Shape awaits joint review.
"""

from typing import Any

from fastapi.responses import JSONResponse
from pydantic import Field

from app.contracts.common import ContractModel, Envelope, ErrorCode, Issue

PROBLEM_MEDIA_TYPE = "application/problem+json"

STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.MALFORMED_REQUEST: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.OPERATION_PAYLOAD_MISMATCH: 409,
    ErrorCode.INVALID_STATE_TRANSITION: 409,
    ErrorCode.INVALID_BUSINESS_INPUT: 422,
    ErrorCode.INPUT_OUT_OF_SUPPORTED_RANGE: 422,
    ErrorCode.GROSS_UP_NOT_CONVERGED: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INFRA_UNAVAILABLE: 503,
}
TITLES: dict[int, str] = {
    400: "Malformed request",
    401: "Authentication required",
    403: "Not allowed",
    404: "Not found",
    409: "Conflict",
    422: "Invalid business input",
    429: "Too many requests",
    500: "Internal error",
    503: "Service unavailable",
}
INTERNAL_REASON = "internal_error"


class ProblemDetails(ContractModel):
    type: str = "about:blank"
    title: str
    status: int = Field(ge=400, le=599)
    code: ErrorCode
    trace_id: str
    details: dict[str, str] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list)


class ApiProblem(Exception):
    """Raised by Business API dependencies; rendered by `BusinessRoute`."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        details: dict[str, str] | None = None,
        issues: list[Issue] | None = None,
        headers: dict[str, str] | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.details = dict(details or {})
        self.issues = list(issues or [])
        self.headers = dict(headers or {})
        self.status = status or STATUS_BY_CODE[code]


def http_status(code: ErrorCode, details: dict[str, str]) -> int:
    """`INFRA_UNAVAILABLE` with `reason=internal_error` is a server fault (500), not 503."""
    if code is ErrorCode.INFRA_UNAVAILABLE and details.get("reason") == INTERNAL_REASON:
        return 500
    return STATUS_BY_CODE[code]


def problem_response(problem: ApiProblem, trace_id: str) -> JSONResponse:
    body = ProblemDetails(
        title=TITLES[problem.status],
        status=problem.status,
        code=problem.code,
        trace_id=trace_id,
        details=problem.details,
        issues=problem.issues,
    )
    headers = dict(problem.headers)
    if problem.code is ErrorCode.RATE_LIMITED and "retryAfterSeconds" in problem.details:
        headers["Retry-After"] = problem.details["retryAfterSeconds"]
    if problem.code is ErrorCode.UNAUTHENTICATED:
        headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        body.model_dump(mode="json", by_alias=True),
        status_code=problem.status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


def problem_from_envelope(envelope: Envelope[Any]) -> ApiProblem:
    assert envelope.error is not None
    details = dict(envelope.error.safe_details)
    return ApiProblem(
        envelope.error.code,
        details=details,
        issues=list(envelope.issues),
        status=http_status(envelope.error.code, details),
    )
