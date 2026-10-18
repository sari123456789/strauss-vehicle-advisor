"""Per-project usage: RPM window, daily budget and reset, cooldown vs daily exhaustion."""

from datetime import UTC, datetime, timedelta

from app.ai.infrastructure.llm.usage import DenialKind, UsageTracker, next_daily_reset

DEV = ("gemini", "strauss-dev", "gemini-3.5-flash-lite")
TEST = ("gemini", "strauss-test", "gemini-3.5-flash-lite")
NOON = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def test_rpm_limit_is_a_rolling_minute() -> None:
    tracker = UsageTracker()
    for i in range(3):
        tracker.record_attempt(DEV, NOON + timedelta(seconds=i))
    denial = tracker.check(DEV, rpm_limit=3, daily_limit=100, now=NOON + timedelta(seconds=10))
    assert denial is not None and denial.kind is DenialKind.RATE_LIMITED
    assert denial.code == "local_rpm_limit"
    assert (
        tracker.check(DEV, rpm_limit=3, daily_limit=100, now=NOON + timedelta(seconds=61)) is None
    )


def test_daily_budget_blocks_until_pacific_midnight() -> None:
    tracker = UsageTracker()
    tracker.record_attempt(DEV, NOON)
    tracker.record_attempt(DEV, NOON)
    denial = tracker.check(DEV, rpm_limit=100, daily_limit=2, now=NOON)
    assert denial is not None and denial.kind is DenialKind.QUOTA_EXHAUSTED
    reset = next_daily_reset(NOON)
    assert reset.utcoffset() is not None
    assert tracker.check(DEV, rpm_limit=100, daily_limit=2, now=reset - timedelta(seconds=1))
    assert (
        tracker.check(DEV, rpm_limit=100, daily_limit=2, now=reset + timedelta(seconds=1)) is None
    )


def test_reset_is_midnight_pacific_time() -> None:
    reset = next_daily_reset(datetime(2026, 10, 18, 6, 59, tzinfo=UTC))  # 23:59 PDT on 17 Oct
    assert reset.astimezone(UTC) == datetime(2026, 10, 18, 7, 0, tzinfo=UTC)


def test_rate_limit_cooldown_is_temporary() -> None:
    tracker = UsageTracker()
    tracker.record_rate_limited(DEV, NOON, 30)
    denial = tracker.check(DEV, rpm_limit=100, daily_limit=100, now=NOON + timedelta(seconds=10))
    assert denial is not None and denial.kind is DenialKind.RATE_LIMITED
    assert denial.retry_after_seconds == 20
    assert (
        tracker.check(DEV, rpm_limit=100, daily_limit=100, now=NOON + timedelta(seconds=31)) is None
    )


def test_quota_exhaustion_lasts_until_the_daily_reset() -> None:
    tracker = UsageTracker()
    until = tracker.record_quota_exhausted(DEV, NOON)
    later = NOON + timedelta(hours=3)
    denial = tracker.check(DEV, rpm_limit=100, daily_limit=100, now=later)
    assert denial is not None and denial.kind is DenialKind.QUOTA_EXHAUSTED
    assert (
        tracker.check(DEV, rpm_limit=100, daily_limit=100, now=until + timedelta(seconds=1)) is None
    )


def test_projects_are_tracked_independently() -> None:
    tracker = UsageTracker()
    tracker.record_quota_exhausted(DEV, NOON)
    assert tracker.check(DEV, rpm_limit=10, daily_limit=10, now=NOON) is not None
    assert tracker.check(TEST, rpm_limit=10, daily_limit=10, now=NOON) is None
    assert tracker.snapshot(TEST, NOON).requests_today == 0


def test_not_free_tier_block_is_permanent_for_the_process() -> None:
    tracker = UsageTracker()
    tracker.record_not_free_tier(DEV, NOON)
    denial = tracker.check(DEV, rpm_limit=10, daily_limit=10, now=NOON + timedelta(days=2))
    assert denial is not None and denial.kind is DenialKind.NOT_FREE_TIER
