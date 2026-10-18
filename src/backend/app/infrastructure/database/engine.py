"""Async engine/session factory and an event-loop helper for runtime entry points.

psycopg's async mode cannot use Windows' default Proactor event loop, so entry points run
their coroutine on a selector loop via `run_async`.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

__all__ = ["AsyncEngine", "AsyncSession", "create_engine", "create_session_factory", "run_async"]


def create_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
