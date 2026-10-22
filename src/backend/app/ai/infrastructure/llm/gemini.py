"""Gemini Developer API client (google-genai SDK) for `QuotaGuardedLlm`.

Safety settings applied to every client:
- `vertexai=False` and the explicit AI Studio endpoint, so environment variables such as
  `GOOGLE_GENAI_USE_VERTEXAI` or a base-URL override can never route calls to a billed service;
- the API key is passed explicitly from the active profile (ambient `GOOGLE_API_KEY` /
  `GEMINI_API_KEY` are not used);
- SDK retries disabled (`attempts=1`): the SDK default would silently resend requests and burn
  free quota; retries are decided by `QuotaGuardedLlm` only;
- the lowest documented thinking level per model and a bounded `max_output_tokens`.

429 classification follows the `google.rpc.QuotaFailure` / `RetryInfo` details returned by the
API: per-day quota ids -> daily exhaustion; a quota value of 0 -> the model has no free quota for
this project (blocked, never retried); otherwise a temporary rate limit.
"""

import re
from collections.abc import Sequence
from typing import Any

from app.ai.application.ports.llm import LlmMessage
from app.ai.infrastructure.llm.free_tier import (
    GEMINI,
    GEMINI_ENDPOINT,
    GEMINI_THINKING_LEVEL,
    is_free_tier_model,
)
from app.ai.infrastructure.llm.provider import (
    FinishKind,
    ProviderError,
    ProviderErrorKind,
    ProviderResponse,
)
from app.ai.infrastructure.llm.settings import CredentialProfile

_RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")
_BLOCKED_FINISH = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE", "OTHER"}
)


def _error_body(details: Any) -> dict[str, Any]:
    if isinstance(details, dict):
        inner = details.get("error", details)
        return inner if isinstance(inner, dict) else {}
    return {}


def _retry_delay(body: dict[str, Any]) -> float | None:
    for detail in body.get("details") or []:
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("RetryInfo"):
            match = _RETRY_DELAY.match(str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def _quota_violations(body: dict[str, Any]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for detail in body.get("details") or []:
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("QuotaFailure"):
            violations.extend(v for v in detail.get("violations") or [] if isinstance(v, dict))
    return violations


def classify_gemini_error(code: int | None, details: Any) -> ProviderError:
    """Map a Gemini API error to a safe `ProviderError` (no payload text is kept)."""
    body = _error_body(details)
    status = str(body.get("status") or "")
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        violations = _quota_violations(body)
        if any(str(v.get("quotaValue", "")).strip() == "0" for v in violations):
            return ProviderError(ProviderErrorKind.NOT_FREE_TIER, "free_tier_quota_is_zero")
        if any("perday" in str(v.get("quotaId", "")).lower() for v in violations):
            return ProviderError(ProviderErrorKind.QUOTA_EXHAUSTED, "daily_quota_exhausted")
        return ProviderError(ProviderErrorKind.RATE_LIMITED, "per_minute_limit", _retry_delay(body))
    if code in (401, 403) or status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
        return ProviderError(ProviderErrorKind.AUTHENTICATION, "authentication_failed")
    if code == 400 and "API_KEY" in str(details):
        return ProviderError(ProviderErrorKind.AUTHENTICATION, "invalid_api_key")
    if code in (408, 504) or status == "DEADLINE_EXCEEDED":
        return ProviderError(ProviderErrorKind.TIMEOUT, "provider_timeout")
    if code is not None and 400 <= code < 500:
        return ProviderError(ProviderErrorKind.BAD_REQUEST, f"http_{code}")
    return ProviderError(ProviderErrorKind.UNAVAILABLE, f"http_{code or 'unknown'}")


def build_gemini_client(profile: CredentialProfile) -> Any:
    """An SDK client bound to one free-tier profile (the SDK is imported lazily)."""
    from google import genai
    from google.genai import types

    if profile.provider != GEMINI or not is_free_tier_model(GEMINI, profile.model):
        raise ValueError("the profile is not an approved free-tier Gemini profile")
    if profile.api_key is None:
        raise ValueError("a Gemini profile requires an API key")
    return genai.Client(
        api_key=profile.api_key.get_secret_value(),
        vertexai=False,
        http_options=types.HttpOptions(
            base_url=GEMINI_ENDPOINT,
            timeout=int(profile.timeout_seconds * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


class GeminiProviderClient:
    def __init__(self, profile: CredentialProfile, models: Any | None = None) -> None:
        if profile.provider != GEMINI or not is_free_tier_model(GEMINI, profile.model):
            raise ValueError("the profile is not an approved free-tier Gemini profile")
        self._model = profile.model
        self._sdk_client: Any | None = None
        if models is None:
            # Keep the SDK client alive for as long as its `models` service is used: when the
            # `genai.Client` is garbage-collected it closes its HTTP client, and every later
            # call fails with "Cannot send a request, as the client has been closed".
            self._sdk_client = build_gemini_client(profile)
            models = self._sdk_client.aio.models
        self._models = models

    async def generate_json(
        self,
        *,
        system_instruction: str,
        messages: Sequence[LlmMessage],
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> ProviderResponse:
        import httpx
        from google.genai import errors, types

        contents = [
            types.Content(
                role="model" if message.role == "assistant" else "user",
                parts=[types.Part(text=message.text)]
                + (
                    [
                        types.Part.from_bytes(
                            data=message.attachment,
                            mime_type=message.attachment_mime_type or "application/pdf",
                        )
                    ]
                    if message.attachment is not None
                    else []
                ),
            )
            for message in messages
        ]
        level = GEMINI_THINKING_LEVEL.get(self._model)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_json_schema=json_schema,
            max_output_tokens=max_output_tokens,
            candidate_count=1,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel(level.upper()))
            if level
            else None,
        )
        try:
            response = await self._models.generate_content(
                model=self._model, contents=contents, config=config
            )
        except errors.APIError as error:
            raise classify_gemini_error(error.code, error.details) from None
        except httpx.TimeoutException:
            raise ProviderError(ProviderErrorKind.TIMEOUT, "client_timeout") from None
        except httpx.TransportError:
            raise ProviderError(ProviderErrorKind.UNAVAILABLE, "transport_error") from None
        return _to_provider_response(response)


def _to_provider_response(response: Any) -> ProviderResponse:
    usage = getattr(response, "usage_metadata", None)
    input_tokens = getattr(usage, "prompt_token_count", None)
    output_tokens = getattr(usage, "candidates_token_count", None)
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        return ProviderResponse("", FinishKind.BLOCKED, input_tokens, output_tokens)
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return ProviderResponse("", FinishKind.BLOCKED, input_tokens, output_tokens)
    reason = getattr(candidates[0], "finish_reason", None)
    reason_name = str(getattr(reason, "name", reason or ""))
    if reason_name == "MAX_TOKENS":
        finish = FinishKind.TRUNCATED
    elif reason_name in _BLOCKED_FINISH:
        finish = FinishKind.BLOCKED
    else:
        finish = FinishKind.COMPLETE
    text = getattr(response, "text", None) or ""
    return ProviderResponse(text, finish, input_tokens, output_tokens)
