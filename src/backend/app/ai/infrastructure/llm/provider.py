"""Low-level provider client contract used by `QuotaGuardedLlm`.

A provider client sends exactly one request (no hidden SDK retries) and translates provider
failures into `ProviderError` kinds. Quota policy, retries and validation live in
`QuotaGuardedLlm`, so they behave identically for every provider.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.ai.application.ports.llm import LlmMessage


class ProviderErrorKind(StrEnum):
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    NOT_FREE_TIER = "not_free_tier"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    AUTHENTICATION = "authentication"
    BAD_REQUEST = "bad_request"


class ProviderError(Exception):
    def __init__(
        self, kind: ProviderErrorKind, code: str, retry_after_seconds: float | None = None
    ) -> None:
        super().__init__(f"{kind.value}: {code}")
        self.kind = kind
        self.code = code
        """Safe diagnostic token; never a key, prompt or raw provider payload."""
        self.retry_after_seconds = retry_after_seconds


class FinishKind(StrEnum):
    COMPLETE = "complete"
    TRUNCATED = "truncated"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    finish: FinishKind
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderClient(Protocol):
    async def generate_json(
        self,
        *,
        system_instruction: str,
        messages: Sequence[LlmMessage],
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> ProviderResponse: ...
