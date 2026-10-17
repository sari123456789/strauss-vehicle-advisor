"""Framework-independent business failures carrying stable machine-readable codes."""


class DomainError(Exception):
    code = "INVALID_BUSINESS_INPUT"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class InvalidValueError(DomainError):
    """A value is malformed or violates a business invariant (e.g. negative, NaN, rate > 1)."""

    code = "INVALID_BUSINESS_INPUT"


class OutOfSupportedRangeError(DomainError):
    """A value exceeds a configured demonstration support limit; never silently clamped."""

    code = "INPUT_OUT_OF_SUPPORTED_RANGE"


class IncompleteInputError(DomainError):
    """A complete calculation input was requested while required facts are missing."""

    code = "INVALID_BUSINESS_INPUT"


class UnsupportedPolicyError(DomainError):
    """Rules contain a policy value that the deterministic engine does not implement."""

    code = "UNSUPPORTED_POLICY"


class GrossUpNotConvergedError(DomainError):
    code = "GROSS_UP_NOT_CONVERGED"
