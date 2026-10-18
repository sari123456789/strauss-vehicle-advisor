"""Environment-based settings. No business rules or secrets are hard-coded here."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STRAUSS_", env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://strauss:strauss_dev_only@localhost:55432/strauss"
    )
    migration_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://strauss:strauss_dev_only@localhost:55432/strauss"
    )
    """Separate migration identity (architecture §3). Same local dev user by default."""
    idle_in_transaction_timeout_ms: int = 15_000
    """PostgreSQL `idle_in_transaction_session_timeout` for runtime Business sessions (Q10)."""


@lru_cache
def get_settings() -> Settings:
    return Settings()
