"""Per-project LLM usage, rate-limit and quota tracking (in-memory; P0/P1).

Counters are keyed by `CredentialProfile.quota_key` (provider, project, model), because Gemini
quotas apply per project and model. Two kinds of limits are kept apart:

- *Temporary rate limiting* (per-minute): a short cooldown, after which calls may resume.
- *Daily quota exhaustion*: blocked until the provider's daily reset (midnight Pacific time for
  Gemini). The local daily budget is enforced the same way, before the provider is called.

A blocked project stays blocked; the tracker never suggests another project or credential.
Persisting counters across restarts is a later stage (P2); a restart therefore resets only the
local view, never the provider's quota.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

QUOTA_RESET_TZ = ZoneInfo("America/Los_Angeles")
"""Gemini daily quotas reset at midnight Pacific time."""

QuotaKey = tuple[str, str, str]


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware current time."""
        ...


class DenialKind(StrEnum):
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    NOT_FREE_TIER = "not_free_tier"


@dataclass(frozen=True)
class Denial:
    kind: DenialKind
    retry_after_seconds: float | None
    code: str


@dataclass(frozen=True)
class UsageSnapshot:
    """Safe diagnostics for one project (no secrets)."""

    requests_today: int
    requests_last_minute: int
    rate_limited_until: datetime | None
    quota_exhausted_until: datetime | None
    blocked_not_free_tier: bool
    last_error_code: str | None


@dataclass
class _ProjectUsage:
    day: str = ""
    requests_today: int = 0
    recent: deque[datetime] = field(default_factory=deque)
    rate_limited_until: datetime | None = None
    quota_exhausted_until: datetime | None = None
    blocked_not_free_tier: bool = False
    last_error_code: str | None = None


def next_daily_reset(now: datetime) -> datetime:
    local = now.astimezone(QUOTA_RESET_TZ)
    tomorrow = local.date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0), tzinfo=QUOTA_RESET_TZ)


def _quota_day(now: datetime) -> str:
    return now.astimezone(QUOTA_RESET_TZ).date().isoformat()


class UsageTracker:
    def __init__(self) -> None:
        self._projects: dict[QuotaKey, _ProjectUsage] = {}

    def _usage(self, key: QuotaKey, now: datetime) -> _ProjectUsage:
        usage = self._projects.setdefault(key, _ProjectUsage())
        day = _quota_day(now)
        if usage.day != day:
            usage.day = day
            usage.requests_today = 0
            if usage.quota_exhausted_until is not None and now >= usage.quota_exhausted_until:
                usage.quota_exhausted_until = None
        while usage.recent and now - usage.recent[0] >= timedelta(minutes=1):
            usage.recent.popleft()
        return usage

    def check(
        self, key: QuotaKey, *, rpm_limit: int, daily_limit: int, now: datetime
    ) -> Denial | None:
        """Why a call must not be made now, or None when it may proceed."""
        usage = self._usage(key, now)
        if usage.blocked_not_free_tier:
            return Denial(DenialKind.NOT_FREE_TIER, None, "model_not_in_free_tier")
        if usage.quota_exhausted_until is not None:
            if now < usage.quota_exhausted_until:
                wait = (usage.quota_exhausted_until - now).total_seconds()
                return Denial(DenialKind.QUOTA_EXHAUSTED, wait, "daily_quota_exhausted")
            usage.quota_exhausted_until = None
        if usage.requests_today >= daily_limit:
            wait = (next_daily_reset(now) - now).total_seconds()
            return Denial(DenialKind.QUOTA_EXHAUSTED, wait, "local_daily_budget_exhausted")
        if usage.rate_limited_until is not None and now < usage.rate_limited_until:
            wait = (usage.rate_limited_until - now).total_seconds()
            return Denial(DenialKind.RATE_LIMITED, wait, "provider_rate_limit_cooldown")
        if len(usage.recent) >= rpm_limit:
            wait = 60 - (now - usage.recent[0]).total_seconds()
            return Denial(DenialKind.RATE_LIMITED, max(wait, 0.0), "local_rpm_limit")
        return None

    def record_attempt(self, key: QuotaKey, now: datetime) -> None:
        """Count a request before it is sent: failed requests still consume quota."""
        usage = self._usage(key, now)
        usage.requests_today += 1
        usage.recent.append(now)

    def record_rate_limited(
        self, key: QuotaKey, now: datetime, retry_after_seconds: float | None
    ) -> None:
        usage = self._usage(key, now)
        usage.rate_limited_until = now + timedelta(seconds=retry_after_seconds or 60.0)
        usage.last_error_code = "rate_limited"

    def record_quota_exhausted(self, key: QuotaKey, now: datetime) -> datetime:
        usage = self._usage(key, now)
        usage.quota_exhausted_until = next_daily_reset(now)
        usage.last_error_code = "quota_exhausted"
        return usage.quota_exhausted_until

    def record_not_free_tier(self, key: QuotaKey, now: datetime) -> None:
        """The provider reported no free quota for this model: block it for this process."""
        usage = self._usage(key, now)
        usage.blocked_not_free_tier = True
        usage.last_error_code = "not_free_tier"

    def record_error(self, key: QuotaKey, now: datetime, code: str) -> None:
        self._usage(key, now).last_error_code = code

    def snapshot(self, key: QuotaKey, now: datetime) -> UsageSnapshot:
        usage = self._usage(key, now)
        return UsageSnapshot(
            requests_today=usage.requests_today,
            requests_last_minute=len(usage.recent),
            rate_limited_until=usage.rate_limited_until,
            quota_exhausted_until=usage.quota_exhausted_until,
            blocked_not_free_tier=usage.blocked_not_free_tier,
            last_error_code=usage.last_error_code,
        )
