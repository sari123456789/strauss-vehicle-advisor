"""Async engine and session factory for AI persistence (psycopg 3).

JSONB never round-trips through a binary float: serialization rejects floats/NaN/non-JSON
types and deserialization rejects non-integer numbers. Runtime connections set
`idle_in_transaction_session_timeout`, so a transaction accidentally held open across an LLM
call is terminated and rolled back by PostgreSQL. On Windows, callers run the event loop as a
selector loop (psycopg async does not support the Proactor loop).
"""

import json
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class JsonFloatError(ValueError):
    """A JSON value contained a binary float or a non-finite constant."""


def _reject_type(value: object) -> Any:
    raise TypeError(f"{type(value).__name__} is not JSON-serializable here (convert explicitly)")


def _reject_float(text: str) -> Any:
    raise JsonFloatError(f"non-integer JSON number {text!r} is not allowed (use decimal strings)")


def _reject_constant(text: str) -> Any:
    raise JsonFloatError(f"JSON constant {text!r} is not allowed")


def _check_no_float(value: object) -> None:
    if isinstance(value, float):
        raise JsonFloatError("binary floats are not allowed in AI JSON payloads")
    if isinstance(value, dict):
        for item in value.values():
            _check_no_float(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _check_no_float(item)


def json_dumps(value: object) -> str:
    _check_no_float(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=_reject_type)


def canonical_json(value: object) -> str:
    """Stable text used for payload hashing (sorted keys, no whitespace)."""
    _check_no_float(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_reject_type,
    )


def json_loads(text: str | bytes) -> Any:
    return json.loads(text, parse_float=_reject_float, parse_constant=_reject_constant)


def create_ai_engine(url: str, *, idle_in_transaction_timeout_ms: int | None = None) -> AsyncEngine:
    connect_args: dict[str, Any] = {}
    if idle_in_transaction_timeout_ms is not None:
        if idle_in_transaction_timeout_ms <= 0:
            raise ValueError("idle_in_transaction_timeout_ms must be positive")
        connect_args["options"] = (
            f"-c idle_in_transaction_session_timeout={idle_in_transaction_timeout_ms}"
        )
    return create_async_engine(
        url,
        pool_pre_ping=True,
        json_serializer=json_dumps,
        json_deserializer=json_loads,
        connect_args=connect_args,
    )


def create_ai_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
