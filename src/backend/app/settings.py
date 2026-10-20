"""Environment-based settings. No business rules or secrets are hard-coded here.

Error messages name variables, never their values.
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEVELOPMENT = "development"
LOCAL_ENVIRONMENTS = frozenset({DEVELOPMENT, "test"})
"""Environments that may run without a configured token secret (a random one is generated)."""
MIN_TOKEN_SECRET_BYTES = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STRAUSS_", env_file=".env", extra="ignore")

    environment: str = DEVELOPMENT
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://strauss:strauss_dev_only@localhost:55432/strauss"
    )
    migration_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://strauss:strauss_dev_only@localhost:55432/strauss"
    )
    """Separate migration identity (architecture §3). Same local dev user by default."""
    idle_in_transaction_timeout_ms: int = 15_000
    """PostgreSQL `idle_in_transaction_session_timeout` for runtime Business sessions (Q10)."""

    auth_token_secret: SecretStr | None = None
    """HS256 signing secret for access tokens (at least 32 bytes). Required outside
    development/test; there a random per-process secret is generated when it is missing."""
    auth_allowed_origins: Annotated[list[str], NoDecode] = []
    """Browser origins of the Angular app (comma-separated). Empty: no CORS middleware."""
    auth_cookie_secure: bool = True
    auth_cookie_samesite: Literal["strict", "lax", "none"] = "strict"
    """`none` only for a cross-site deployment (requires `auth_cookie_secure`)."""
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    """Level of the application loggers (`app.*`). They log identifiers, codes and durations
    only - never credentials, tokens, salary values or message text."""
    worker_in_process: bool = False
    """Run the AI turn worker and the Business event relay inside the API process (decision W).
    Enable it on exactly one instance."""

    @model_validator(mode="before")
    @classmethod
    def _split_origins(cls, data: object) -> object:
        if isinstance(data, dict):
            raw = data.get("auth_allowed_origins")
            if isinstance(raw, str):
                data = {
                    **data,
                    "auth_allowed_origins": [o.strip() for o in raw.split(",") if o.strip()],
                }
        return data

    @model_validator(mode="after")
    def _check_security(self) -> "Settings":
        if self.auth_cookie_samesite == "none" and not self.auth_cookie_secure:
            raise ValueError(
                "STRAUSS_AUTH_COOKIE_SAMESITE=none requires STRAUSS_AUTH_COOKIE_SECURE"
            )
        secret = self.auth_token_secret
        if secret is not None and len(secret.get_secret_value().encode()) < MIN_TOKEN_SECRET_BYTES:
            raise ValueError(
                f"STRAUSS_AUTH_TOKEN_SECRET must be at least {MIN_TOKEN_SECRET_BYTES} bytes"
            )
        if secret is None and not self.is_local:
            raise ValueError("STRAUSS_AUTH_TOKEN_SECRET is required outside development")
        return self

    @property
    def is_local(self) -> bool:
        return self.environment in LOCAL_ENVIRONMENTS


@lru_cache
def get_settings() -> Settings:
    return Settings()
