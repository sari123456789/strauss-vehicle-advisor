"""M1: shared settings validation (auth secret, cookie policy, origins, worker flag)."""

import pytest
from pydantic import ValidationError

from app.settings import Settings

SECRET = "s" * 32


def test_production_requires_a_token_secret_and_never_echoes_values() -> None:
    with pytest.raises(ValidationError, match="STRAUSS_AUTH_TOKEN_SECRET is required"):
        Settings(environment="production", auth_token_secret=None)
    assert Settings(environment="production", auth_token_secret=SECRET).auth_token_secret


def test_short_secret_is_rejected_without_showing_it() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(auth_token_secret="tooshort")
    assert "at least 32 bytes" in str(error.value)


@pytest.mark.parametrize("environment", ["development", "test"])
def test_local_environments_may_run_without_a_secret(environment: str) -> None:
    assert Settings(environment=environment, auth_token_secret=None).is_local


def test_samesite_none_requires_secure_cookies() -> None:
    with pytest.raises(ValidationError, match="requires STRAUSS_AUTH_COOKIE_SECURE"):
        Settings(auth_cookie_samesite="none", auth_cookie_secure=False)
    assert Settings(auth_cookie_samesite="none").auth_cookie_secure


def test_origins_are_read_from_a_comma_separated_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRAUSS_AUTH_ALLOWED_ORIGINS", "https://a.example, https://b.example ,")
    monkeypatch.setenv("STRAUSS_WORKER_IN_PROCESS", "true")
    settings = Settings()
    assert settings.auth_allowed_origins == ["https://a.example", "https://b.example"]
    assert settings.worker_in_process is True


def test_worker_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRAUSS_WORKER_IN_PROCESS", raising=False)
    assert Settings(_env_file=None).worker_in_process is False  # type: ignore[call-arg]
