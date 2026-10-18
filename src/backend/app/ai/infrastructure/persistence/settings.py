"""AI database settings, read from the same environment source as the LLM settings.

    STRAUSS_AI_DATABASE_URL=postgresql+psycopg://...         runtime identity
    STRAUSS_AI_MIGRATION_DATABASE_URL=postgresql+psycopg://... migration identity (DDL)
    STRAUSS_AI_IDLE_IN_TRANSACTION_TIMEOUT_MS=15000

The AI module owns only the `agent` schema. Both URLs default to the local Docker Compose
database; separate least-privilege roles (decision D8) are configured here once agreed.
Error messages name variables, never their values.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.ai.infrastructure.llm.settings import read_environment

PREFIX = "STRAUSS_AI_"
DEFAULT_DATABASE_URL = "postgresql+psycopg://strauss:strauss_dev_only@localhost:55432/strauss"
DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_MS = 15_000


class AiDatabaseConfigurationError(ValueError):
    """Invalid AI database configuration. Messages contain variable names only."""


@dataclass(frozen=True)
class AiDatabaseSettings:
    database_url: SecretStr
    migration_database_url: SecretStr
    idle_in_transaction_timeout_ms: int = DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_MS


def _url(env: Mapping[str, str], name: str) -> SecretStr:
    key = PREFIX + name
    value = env.get(key, "").strip() or DEFAULT_DATABASE_URL
    try:
        parsed = make_url(value)
    except ArgumentError:
        raise AiDatabaseConfigurationError(f"{key} is not a valid database URL") from None
    if parsed.drivername != "postgresql+psycopg" or not parsed.database:
        raise AiDatabaseConfigurationError(
            f"{key} must be a postgresql+psycopg URL with a database"
        )
    return SecretStr(value)


def load_database_settings(env: Mapping[str, str] | None = None) -> AiDatabaseSettings:
    env = read_environment() if env is None else env
    key = PREFIX + "IDLE_IN_TRANSACTION_TIMEOUT_MS"
    raw = env.get(key, "").strip()
    try:
        timeout = int(raw) if raw else DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_MS
    except ValueError:
        raise AiDatabaseConfigurationError(f"{key} must be a positive integer") from None
    if timeout <= 0:
        raise AiDatabaseConfigurationError(f"{key} must be a positive integer")
    return AiDatabaseSettings(
        database_url=_url(env, "DATABASE_URL"),
        migration_database_url=_url(env, "MIGRATION_DATABASE_URL"),
        idle_in_transaction_timeout_ms=timeout,
    )
