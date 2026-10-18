"""Credential profiles: explicit selection, free-tier-only validation, secrets never echoed."""

import re
from pathlib import Path

import pytest

from app.ai.infrastructure.llm.settings import (
    LlmConfigurationError,
    load_llm_settings,
    read_environment,
)
from tests.paths import REPO_ROOT

SECRET_DEV = "AIzaFAKE-dev-key-0000000000000000000"
SECRET_TEST = "AIzaFAKE-test-key-111111111111111111"


def env(**overrides: str) -> dict[str, str]:
    base = {
        "STRAUSS_AI_LLM_PROFILES": "dev,test",
        "STRAUSS_AI_LLM_ACTIVE_PROFILE": "dev",
        "STRAUSS_AI_LLM_PROFILE_DEV_PROVIDER": "gemini",
        "STRAUSS_AI_LLM_PROFILE_DEV_API_KEY": SECRET_DEV,
        "STRAUSS_AI_LLM_PROFILE_DEV_PROJECT_LABEL": "strauss-dev",
        "STRAUSS_AI_LLM_PROFILE_DEV_FREE_TIER_ACK": "true",
        "STRAUSS_AI_LLM_PROFILE_TEST_PROVIDER": "gemini",
        "STRAUSS_AI_LLM_PROFILE_TEST_MODEL": "gemini-3.1-flash-lite",
        "STRAUSS_AI_LLM_PROFILE_TEST_API_KEY": SECRET_TEST,
        "STRAUSS_AI_LLM_PROFILE_TEST_PROJECT_LABEL": "strauss-test",
        "STRAUSS_AI_LLM_PROFILE_TEST_RPM_LIMIT": "5",
        "STRAUSS_AI_LLM_PROFILE_TEST_DAILY_REQUEST_LIMIT": "50",
        "STRAUSS_AI_LLM_PROFILE_TEST_FREE_TIER_ACK": "true",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v != "<unset>"}


def test_no_profiles_means_llm_disabled() -> None:
    settings = load_llm_settings({})
    assert settings.active.provider == "disabled"
    assert settings.active.api_key is None


def test_active_profile_is_selected_explicitly() -> None:
    settings = load_llm_settings(env())
    assert settings.active.name == "DEV"
    assert settings.active.model == "gemini-3.5-flash-lite"  # default free-tier model
    assert set(settings.profiles) == {"DEV", "TEST"}

    switched = load_llm_settings(env(STRAUSS_AI_LLM_ACTIVE_PROFILE="test"))
    assert switched.active.name == "TEST"
    assert switched.active.project_label == "strauss-test"
    assert switched.active.rpm_limit == 5
    assert switched.active.daily_request_limit == 50


def test_each_profile_keeps_its_own_credential() -> None:
    settings = load_llm_settings(env())
    dev, test = settings.profiles["DEV"], settings.profiles["TEST"]
    assert dev.api_key is not None and dev.api_key.get_secret_value() == SECRET_DEV
    assert test.api_key is not None and test.api_key.get_secret_value() == SECRET_TEST
    assert dev.quota_key != test.quota_key


def test_secrets_are_not_in_repr() -> None:
    settings = load_llm_settings(env())
    text = repr(settings) + str(settings.profiles)
    assert SECRET_DEV not in text and SECRET_TEST not in text


@pytest.mark.parametrize(
    ("overrides", "variable"),
    [
        ({"STRAUSS_AI_LLM_PROFILE_DEV_FREE_TIER_ACK": "<unset>"}, "DEV_FREE_TIER_ACK"),
        ({"STRAUSS_AI_LLM_PROFILE_DEV_API_KEY": "<unset>"}, "DEV_API_KEY"),
        ({"STRAUSS_AI_LLM_PROFILE_DEV_MODEL": "gemini-3.1-pro-preview"}, "DEV_MODEL"),
        ({"STRAUSS_AI_LLM_PROFILE_DEV_MODEL": "gemini-omni-flash"}, "DEV_MODEL"),
        ({"STRAUSS_AI_LLM_PROFILE_DEV_PROVIDER": "openai"}, "DEV_PROVIDER"),
        ({"STRAUSS_AI_LLM_PROFILE_DEV_PROVIDER": "vertex"}, "DEV_PROVIDER"),
        ({"STRAUSS_AI_LLM_ACTIVE_PROFILE": "prod"}, "ACTIVE_PROFILE"),
        ({"STRAUSS_AI_LLM_ACTIVE_PROFILE": "<unset>"}, "ACTIVE_PROFILE"),
        ({"STRAUSS_AI_LLM_PROFILE_DEV_RPM_LIMIT": "0"}, "DEV_RPM_LIMIT"),
        ({"STRAUSS_AI_LLM_PROFILE_DEV_TIMEOUT_SECONDS": "600"}, "DEV_TIMEOUT_SECONDS"),
        ({"STRAUSS_AI_LLM_PROFILES": "dev,dev"}, "PROFILES"),
        ({"STRAUSS_AI_LLM_PROFILE_DEV_FREE_TIER_ACK": "maybe"}, "DEV_FREE_TIER_ACK"),
    ],
)
def test_invalid_configuration_fails_without_leaking_secrets(
    overrides: dict[str, str], variable: str
) -> None:
    with pytest.raises(LlmConfigurationError) as raised:
        load_llm_settings(env(**overrides))
    message = str(raised.value)
    assert variable in message
    assert SECRET_DEV not in message and SECRET_TEST not in message


def test_invalid_inactive_profile_also_fails_at_startup() -> None:
    with pytest.raises(LlmConfigurationError, match="TEST_MODEL"):
        load_llm_settings(env(STRAUSS_AI_LLM_PROFILE_TEST_MODEL="gemini-3.1-pro-preview"))


def test_env_file_is_read_and_process_environment_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "STRAUSS_AI_LLM_ACTIVE_PROFILE=dev\nSTRAUSS_AI_LLM_PROFILES=dev\n", encoding="utf-8"
    )
    monkeypatch.setenv("STRAUSS_AI_LLM_ACTIVE_PROFILE", "test")
    values = read_environment(env_file)
    assert values["STRAUSS_AI_LLM_PROFILES"] == "dev"
    assert values["STRAUSS_AI_LLM_ACTIVE_PROFILE"] == "test"


def test_copied_env_example_starts_with_the_llm_disabled() -> None:
    from dotenv import dotenv_values

    values = {k: v for k, v in dotenv_values(REPO_ROOT / ".env.example").items() if v is not None}
    assert load_llm_settings(values).active.provider == "disabled"


def test_env_example_documents_profiles_with_placeholders_only() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "STRAUSS_AI_LLM_ACTIVE_PROFILE" in text
    keys = re.findall(r"^STRAUSS_AI_LLM_PROFILE_\w+_API_KEY=(.*)$", text, re.MULTILINE)
    assert keys, "profile API key variables must be documented"
    for value in keys:
        assert value.strip() in ("", "replace-with-your-free-tier-key")
    assert not re.search(r"AIza[0-9A-Za-z_-]{20,}", text)
