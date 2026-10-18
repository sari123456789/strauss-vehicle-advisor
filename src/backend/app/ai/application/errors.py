"""AI Application errors. Each carries a stable `ErrorCode` for the shared API to map to a
natural Hebrew message; raw codes, statuses and internals are never shown to employees."""

from app.contracts.common import ErrorCode


class ConversationError(Exception):
    code: ErrorCode = ErrorCode.INFRA_UNAVAILABLE
    retryable: bool = False

    def __init__(self, detail: str = "", *, trace_id: str | None = None) -> None:
        super().__init__(detail or type(self).__name__)
        self.trace_id = trace_id


class ConversationAccessError(ConversationError):
    """Unknown conversation, or one the actor may not access (indistinguishable)."""

    code = ErrorCode.NOT_FOUND


class ConversationRequestError(ConversationError):
    """Malformed request (e.g. empty or over-long message)."""

    code = ErrorCode.MALFORMED_REQUEST


class ConversationConflictError(ConversationError):
    """An idempotency key was reused with a different payload."""

    code = ErrorCode.OPERATION_PAYLOAD_MISMATCH


class BusinessUnavailableError(ConversationError):
    """The Business case could not be created right now. The conversation stays
    `initializing`; repeating the same request resumes it with the same operation ID."""

    code = ErrorCode.INFRA_UNAVAILABLE
    retryable = True


class ConversationInitializationError(ConversationError):
    """Business rejected case creation for a non-transient reason, or the binding is
    inconsistent. The conversation stays `initializing` and needs attention."""

    code = ErrorCode.INVALID_STATE_TRANSITION
