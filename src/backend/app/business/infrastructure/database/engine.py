"""Async engines and session factory for Business persistence.

Entry points run coroutines through `app.runtime.run_async` (selector event loop on Windows).

JSONB handling (all engines): serialization rejects NaN/Infinity and any non-JSON type
(Decimal must already be a string); deserialization rejects non-integer JSON numbers and the
NaN/Infinity constants, so no value is ever read back through a binary float.

Runtime engine (`create_runtime_engine`): each connection sets the PostgreSQL parameter
`idle_in_transaction_session_timeout` (decision Q10). PostgreSQL then terminates a session
that stays *idle inside an open transaction* longer than the limit (for example a transaction
held open across an LLM call); the transaction is rolled back and nothing is committed.
Time spent *executing* statements does not count, so long-running active statements are
not interrupted. No `statement_timeout` is set. Migration and import engines do not use it.
"""

import json
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

__all__ = [
    "DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_MS",
    "AsyncEngine",
    "AsyncSession",
    "create_engine",
    "create_runtime_engine",
    "create_session_factory",
]

DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_MS = 15_000


class JsonFloatError(ValueError):
    """A JSONB value contained a binary float or a non-finite constant."""


def _reject_float(text: str) -> Any:
    raise JsonFloatError(f"non-integer JSON number {text!r} is not allowed (use decimal strings)")


def _reject_constant(text: str) -> Any:
    raise JsonFloatError(f"JSON constant {text!r} is not allowed")


def _reject_type(value: object) -> Any:
    raise TypeError(f"{type(value).__name__} is not JSON-serializable here (convert explicitly)")


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=_reject_type)


def json_loads(text: str | bytes) -> Any:
    return json.loads(text, parse_float=_reject_float, parse_constant=_reject_constant)


def create_engine(url: str, *, connect_args: dict[str, Any] | None = None) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_pre_ping=True,
        json_serializer=json_dumps,
        json_deserializer=json_loads,
        connect_args=connect_args or {},
    )


def create_runtime_engine(
    url: str, *, idle_in_transaction_timeout_ms: int = DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_MS
) -> AsyncEngine:
    if idle_in_transaction_timeout_ms <= 0:
        raise ValueError("idle_in_transaction_timeout_ms must be positive")
    options = f"-c idle_in_transaction_session_timeout={idle_in_transaction_timeout_ms}"
    return create_engine(url, connect_args={"options": options})


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
