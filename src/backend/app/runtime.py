"""Shared event-loop helper for runtime entry points (CLI, migrations). No business or AI logic.

psycopg's async mode cannot use Windows' default Proactor event loop, so entry points run
their coroutine on a selector loop via `run_async`.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

__all__ = ["run_async"]


def run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
