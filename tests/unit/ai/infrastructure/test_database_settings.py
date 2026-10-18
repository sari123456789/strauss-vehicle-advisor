"""AI database settings: STRAUSS_AI_* variables, validation without leaking values."""

import pytest

from app.ai.infrastructure.persistence.settings import (
    DEFAULT_DATABASE_URL,
    AiDatabaseConfigurationError,
    load_database_settings,
)

SECRET_URL = "postgresql+psycopg://ai_runtime:s3cret-value@db:5432/strauss"


def test_defaults_point_at_the_local_compose_database() -> None:
    settings = load_database_settings({})
    assert settings.database_url.get_secret_value() == DEFAULT_DATABASE_URL
    assert settings.migration_database_url.get_secret_value() == DEFAULT_DATABASE_URL
    assert settings.idle_in_transaction_timeout_ms == 15_000


def test_runtime_and_migration_urls_are_separate() -> None:
    settings = load_database_settings(
        {
            "STRAUSS_AI_DATABASE_URL": SECRET_URL,
            "STRAUSS_AI_MIGRATION_DATABASE_URL": DEFAULT_DATABASE_URL,
            "STRAUSS_AI_IDLE_IN_TRANSACTION_TIMEOUT_MS": "5000",
        }
    )
    assert settings.database_url.get_secret_value() == SECRET_URL
    assert settings.migration_database_url.get_secret_value() == DEFAULT_DATABASE_URL
    assert settings.idle_in_transaction_timeout_ms == 5000
    assert "s3cret" not in repr(settings)


@pytest.mark.parametrize(
    "env",
    [
        {"STRAUSS_AI_DATABASE_URL": "mysql://u:s3cret-value@h/db"},
        {"STRAUSS_AI_DATABASE_URL": "postgresql+psycopg://u:s3cret-value@h"},
        {"STRAUSS_AI_DATABASE_URL": "not a url s3cret-value"},
        {"STRAUSS_AI_IDLE_IN_TRANSACTION_TIMEOUT_MS": "0"},
        {"STRAUSS_AI_IDLE_IN_TRANSACTION_TIMEOUT_MS": "abc"},
    ],
)
def test_invalid_configuration_names_the_variable_only(env: dict[str, str]) -> None:
    with pytest.raises(AiDatabaseConfigurationError) as error:
        load_database_settings(env)
    message = str(error.value)
    assert message.startswith("STRAUSS_AI_") and "s3cret" not in message
