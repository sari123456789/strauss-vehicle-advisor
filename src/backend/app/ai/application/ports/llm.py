"""Provider-independent LLM port (AI-internal). STATUS: PROVISIONAL.

The workflow sees only tasks and results. It never learns which provider, project, model or
credential served a call, and no secret ever passes through this interface. Expected failures
(rate limits, quota, timeouts, invalid output) are returned as `LlmResult` values, never raised,
so the workflow can always fall back to deterministic Hebrew templates.

The LLM interprets language and phrases explanations only. It never calculates payroll, decides
eligibility or produces amounts: those come exclusively from the business tools.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

OutputT = TypeVar("OutputT", bound=BaseModel)


class LlmTaskKind(StrEnum):
    INTERPRET = "interpret"
    """Structured interpretation of one employee message."""
    COMPOSE = "compose"
    """Short Hebrew wording around results that code renders itself."""


@dataclass(frozen=True)
class LlmMessage:
    role: Literal["user", "assistant"]
    text: str
    attachment: bytes | None = None
    """Optional document sent with the text (e.g. a scanned payslip PDF); in memory only."""
    attachment_mime_type: str | None = None


@dataclass(frozen=True)
class LlmTask(Generic[OutputT]):
    kind: LlmTaskKind
    system_instruction: str
    messages: tuple[LlmMessage, ...]
    output_type: type[OutputT]
    """The provider must return JSON that validates against this model."""
    max_output_tokens: int = 1024


class LlmOutcome(StrEnum):
    OK = "ok"
    RATE_LIMITED = "rate_limited"
    """Temporary (per-minute) limit; retry later."""
    QUOTA_EXHAUSTED = "quota_exhausted"
    """Daily quota or local daily budget used up; no retry before the reset."""
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_OUTPUT = "invalid_output"
    REFUSED = "refused"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    """Refused locally: not free-tier, turn budget exhausted, or the LLM is disabled."""


@dataclass(frozen=True)
class LlmUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class LlmResult(Generic[OutputT]):
    outcome: LlmOutcome
    value: OutputT | None = None
    retry_after_seconds: float | None = None
    usage: LlmUsage | None = None
    diagnostic_code: str | None = None
    """Safe machine-readable reason (e.g. "per_minute_limit"); never a secret or prompt text."""

    @property
    def ok(self) -> bool:
        return self.outcome is LlmOutcome.OK and self.value is not None


@runtime_checkable
class LlmPort(Protocol):
    async def generate(self, task: LlmTask[OutputT]) -> LlmResult[OutputT]: ...
