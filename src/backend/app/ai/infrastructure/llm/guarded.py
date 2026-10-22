"""`LlmPort` implementation with free-tier guards, per-project quotas and bounded retries.

Behavior per call:
1. Refuse locally (no provider call) when the project is blocked, its daily quota or local
   budget is exhausted, it is cooling down after a 429, or the local RPM ceiling is reached.
2. Send one request. A short per-minute 429 (retry delay <= `max_short_wait_seconds`) is retried
   once on the **same** profile; anything else is returned to the workflow.
3. Validate the JSON against the task's output model; invalid output is never used.

There is no fallback to another profile, model or provider. Logs carry the profile name, the
non-secret project label and outcome codes only.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.ai.application.ports.llm import (
    LlmOutcome,
    LlmResult,
    LlmTask,
    LlmUsage,
    OutputT,
)
from app.ai.infrastructure.llm.provider import (
    FinishKind,
    ProviderClient,
    ProviderError,
    ProviderErrorKind,
)
from app.ai.infrastructure.llm.schema import provider_json_schema
from app.ai.infrastructure.llm.settings import CredentialProfile
from app.ai.infrastructure.llm.usage import Clock, DenialKind, UsageTracker

logger = logging.getLogger("app.ai.llm")

DEFAULT_MAX_SHORT_WAIT_SECONDS = 5.0
TRANSIENT_RETRY_SECONDS = 1.0
"""Pause before the single retry after a timeout or a temporary provider error."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


_DENIAL_OUTCOME = {
    DenialKind.RATE_LIMITED: LlmOutcome.RATE_LIMITED,
    DenialKind.QUOTA_EXHAUSTED: LlmOutcome.QUOTA_EXHAUSTED,
    DenialKind.NOT_FREE_TIER: LlmOutcome.BLOCKED_BY_POLICY,
}


class QuotaGuardedLlm:
    def __init__(
        self,
        profile: CredentialProfile,
        client: ProviderClient,
        tracker: UsageTracker,
        *,
        clock: Clock | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_short_wait_seconds: float = DEFAULT_MAX_SHORT_WAIT_SECONDS,
    ) -> None:
        self._profile = profile
        self._client = client
        self._tracker = tracker
        self._clock = clock or SystemClock()
        self._sleep = sleep
        self._max_short_wait = max_short_wait_seconds

    async def generate(self, task: LlmTask[OutputT]) -> LlmResult[OutputT]:
        schema = provider_json_schema(task.output_type)
        result: LlmResult[OutputT] | None = None
        for attempt in range(2):
            result, retry_wait = await self._attempt(task, schema)
            if retry_wait is None or attempt == 1:
                break
            await self._sleep(retry_wait)
        assert result is not None
        self._log(task, result)
        return result

    async def _attempt(
        self, task: LlmTask[OutputT], schema: dict[str, Any]
    ) -> tuple[LlmResult[OutputT], float | None]:
        """One guarded request; the second value is a short wait if one retry is allowed."""
        profile, key = self._profile, self._profile.quota_key
        now = self._clock.now()
        denial = self._tracker.check(
            key, rpm_limit=profile.rpm_limit, daily_limit=profile.daily_request_limit, now=now
        )
        if denial is not None:
            outcome = _DENIAL_OUTCOME[denial.kind]
            return LlmResult(
                outcome, retry_after_seconds=denial.retry_after_seconds, diagnostic_code=denial.code
            ), None
        self._tracker.record_attempt(key, now)
        try:
            response = await asyncio.wait_for(
                self._client.generate_json(
                    system_instruction=task.system_instruction,
                    messages=task.messages,
                    json_schema=schema,
                    max_output_tokens=task.max_output_tokens,
                ),
                timeout=float(profile.timeout_seconds),
            )
        except TimeoutError:
            self._tracker.record_error(key, self._clock.now(), "timeout")
            # A slow or overloaded provider often answers a second request: retry once.
            return (
                LlmResult(LlmOutcome.TIMEOUT, diagnostic_code="timeout"),
                TRANSIENT_RETRY_SECONDS,
            )
        except ProviderError as error:
            return self._provider_failure(error)
        except Exception as error:  # an unexpected SDK failure must not crash the turn
            code = f"unexpected_{type(error).__name__}"
            self._tracker.record_error(key, self._clock.now(), code)
            return LlmResult(LlmOutcome.UNAVAILABLE, diagnostic_code=code), None

        usage = LlmUsage(response.input_tokens, response.output_tokens)
        if response.finish is FinishKind.BLOCKED:
            return LlmResult(LlmOutcome.REFUSED, usage=usage, diagnostic_code="blocked"), None
        if response.finish is FinishKind.TRUNCATED:
            return LlmResult(
                LlmOutcome.INVALID_OUTPUT, usage=usage, diagnostic_code="truncated"
            ), None
        try:
            value = task.output_type.model_validate_json(response.text)
        except (ValidationError, json.JSONDecodeError, ValueError):
            return LlmResult(
                LlmOutcome.INVALID_OUTPUT, usage=usage, diagnostic_code="schema_validation_failed"
            ), None
        return LlmResult(LlmOutcome.OK, value=value, usage=usage), None

    def _provider_failure(self, error: ProviderError) -> tuple[LlmResult[OutputT], float | None]:
        key, now = self._profile.quota_key, self._clock.now()
        kind = error.kind
        if kind is ProviderErrorKind.RATE_LIMITED:
            self._tracker.record_rate_limited(key, now, error.retry_after_seconds)
            wait = error.retry_after_seconds
            short = wait is not None and wait <= self._max_short_wait
            return LlmResult(
                LlmOutcome.RATE_LIMITED, retry_after_seconds=wait, diagnostic_code=error.code
            ), (wait if short else None)
        if kind is ProviderErrorKind.QUOTA_EXHAUSTED:
            until = self._tracker.record_quota_exhausted(key, now)
            return LlmResult(
                LlmOutcome.QUOTA_EXHAUSTED,
                retry_after_seconds=(until - now).total_seconds(),
                diagnostic_code=error.code,
            ), None
        if kind is ProviderErrorKind.NOT_FREE_TIER:
            self._tracker.record_not_free_tier(key, now)
            return LlmResult(LlmOutcome.BLOCKED_BY_POLICY, diagnostic_code=error.code), None
        self._tracker.record_error(key, now, error.code)
        # Timeouts and temporary unavailability (e.g. HTTP 503) are retried once.
        if kind is ProviderErrorKind.TIMEOUT:
            return LlmResult(
                LlmOutcome.TIMEOUT, diagnostic_code=error.code
            ), TRANSIENT_RETRY_SECONDS
        return LlmResult(
            LlmOutcome.UNAVAILABLE, diagnostic_code=error.code
        ), TRANSIENT_RETRY_SECONDS

    def _log(self, task: LlmTask[Any], result: LlmResult[Any]) -> None:
        logger.info(
            "llm_call task=%s profile=%s project=%s model=%s outcome=%s code=%s",
            task.kind.value,
            self._profile.name,
            self._profile.project_label,
            self._profile.model,
            result.outcome.value,
            result.diagnostic_code,
        )


class DisabledLlm:
    """No provider configured: deterministic templates are used instead of model calls."""

    async def generate(self, task: LlmTask[OutputT]) -> LlmResult[OutputT]:
        return LlmResult(LlmOutcome.BLOCKED_BY_POLICY, diagnostic_code="llm_disabled")
