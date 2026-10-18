"""Clock port: Application timestamps come from here (testable, always timezone-aware UTC)."""

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...
