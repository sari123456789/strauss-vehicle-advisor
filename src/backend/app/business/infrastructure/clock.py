"""System clock implementing the Application `Clock` port (UTC, timezone-aware)."""

from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
