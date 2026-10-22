"""`build_llm` logs the failover order of the profiles, never a key."""

import logging

import pytest
from app.bootstrap import build_llm

SECRETS = ("AIzaFAKE-first-key-444444444444444", "AIzaFAKE-second-key-55555555555555")


def _env() -> dict[str, str]:
    env = {
        "STRAUSS_AI_LLM_PROFILES": "dev,p2,p3",
        "STRAUSS_AI_LLM_ACTIVE_PROFILE": "p2",
    }
    for name, secret in zip(
        ("DEV", "P2", "P3"), (*SECRETS, SECRETS[0] + "x"), strict=True
    ):
        env.update(
            {
                f"STRAUSS_AI_LLM_PROFILE_{name}_PROVIDER": "gemini",
                f"STRAUSS_AI_LLM_PROFILE_{name}_API_KEY": secret,
                f"STRAUSS_AI_LLM_PROFILE_{name}_PROJECT_LABEL": f"strauss-{name.lower()}",
                f"STRAUSS_AI_LLM_PROFILE_{name}_FREE_TIER_ACK": "true",
            }
        )
    return env


def test_startup_log_lists_the_failover_order_without_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.bootstrap")
    build_llm(_env())
    assert "llm_configured profile=P2" in caplog.text
    assert (
        "chain=P2,DEV,P3" in caplog.text
    )  # the active profile first, then configuration order
    assert not any(secret in caplog.text for secret in SECRETS)
