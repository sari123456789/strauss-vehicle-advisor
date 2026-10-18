"""Business Application errors with stable `ErrorCode`s (never shown raw to employees).

Services raise only `BusinessError` subclasses. `translate_errors()` maps Domain and port
exceptions in one place. Expected outcomes are returned (not raised) at the contract boundary:
`error_envelope` turns a `BusinessError` into an `Envelope` with `status=error`. `NOT_FOUND`
carries no details, so another employee's resources are never disclosed.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.business.application.ports.repositories import (
    OperationPayloadMismatchError,
    VersionConflictError,
)
from app.business.domain.errors import DomainError, OutOfSupportedRangeError
from app.business.domain.representatives import InvalidStateTransitionError
from app.contracts.common import Envelope, EnvelopeStatus, ErrorCode, ErrorInfo


class BusinessError(Exception):
    code: ErrorCode = ErrorCode.INVALID_BUSINESS_INPUT

    def __init__(self, message: str, *, safe_details: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.safe_details = dict(safe_details or {})


class UnauthenticatedError(BusinessError):
    code = ErrorCode.UNAUTHENTICATED


class ForbiddenError(BusinessError):
    code = ErrorCode.FORBIDDEN


class NotFoundError(BusinessError):
    """Absent or not visible to this actor; deliberately indistinguishable."""

    code = ErrorCode.NOT_FOUND

    def __init__(self, message: str = "resource not found") -> None:
        super().__init__(message)


class VersionConflict(BusinessError):
    code = ErrorCode.VERSION_CONFLICT


class PayloadMismatch(BusinessError):
    code = ErrorCode.OPERATION_PAYLOAD_MISMATCH


class InvalidInput(BusinessError):
    code = ErrorCode.INVALID_BUSINESS_INPUT


class OutOfSupportedRange(BusinessError):
    code = ErrorCode.INPUT_OUT_OF_SUPPORTED_RANGE


class InvalidStateTransition(BusinessError):
    code = ErrorCode.INVALID_STATE_TRANSITION


class InfrastructureUnavailable(BusinessError):
    code = ErrorCode.INFRA_UNAVAILABLE


def to_business_error(error: Exception) -> BusinessError | None:
    """Translate a known Domain/port exception; `None` for anything unexpected."""
    if isinstance(error, BusinessError):
        return error
    if isinstance(error, VersionConflictError):
        return VersionConflict("the data changed in the meantime; reload before saving")
    if isinstance(error, OperationPayloadMismatchError):
        return PayloadMismatch("this operation ID was already used with a different request")
    if isinstance(error, InvalidStateTransitionError):
        return InvalidStateTransition(str(error))
    if isinstance(error, OutOfSupportedRangeError):
        field = {"field": error.field} if error.field else None
        return OutOfSupportedRange(str(error), safe_details=field)
    if isinstance(error, DomainError):
        field = {"field": error.field} if error.field else None
        return InvalidInput(str(error), safe_details=field)
    return None


@contextmanager
def translate_errors() -> Iterator[None]:
    try:
        yield
    except Exception as error:
        translated = to_business_error(error)
        if translated is None or translated is error:
            raise
        raise translated from error


def error_envelope(error: BusinessError, trace_id: str) -> Envelope[Any]:
    return Envelope[Any](
        status=EnvelopeStatus.ERROR,
        error=ErrorInfo(code=error.code, trace_id=trace_id, safe_details=error.safe_details),
        trace_id=trace_id,
    )
