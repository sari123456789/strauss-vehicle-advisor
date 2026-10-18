"""Free-tier-only policy: approved providers, models and endpoints.

Only models that Google's pricing page listed as "Free of charge" on the Standard Free Tier are
allowed (https://ai.google.dev/gemini-api/docs/pricing, last updated 2026-10-16, reviewed
2026-10-18). Adding a model or provider is a reviewed code change, never a runtime fallback.

Limitation: whether a Gemini call is free is decided by the Google Cloud project (no billing
account attached), not by the endpoint or model. Code cannot verify that; every profile must
therefore carry an explicit operator acknowledgement (`FREE_TIER_ACK=true`) that its project
has billing disabled. The process never enables billing and never switches models.
"""

from collections.abc import Mapping

GEMINI = "gemini"
DISABLED = "disabled"
"""No LLM: every call returns `blocked_by_policy`; the workflow uses deterministic templates."""

APPROVED_PROVIDERS: frozenset[str] = frozenset({GEMINI, DISABLED})

FREE_TIER_MODELS: Mapping[str, frozenset[str]] = {
    GEMINI: frozenset(
        {
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.8-flash",
        }
    ),
    DISABLED: frozenset({"none"}),
}

DEFAULT_MODEL: Mapping[str, str] = {GEMINI: "gemini-3.5-flash-lite", DISABLED: "none"}

GEMINI_THINKING_LEVEL: Mapping[str, str | None] = {
    "gemini-3.5-flash-lite": "minimal",
    "gemini-3.6-flash": "minimal",
    "gemini-3.8-flash": "low",
    "gemini-3.1-flash-lite": None,
}
"""Lowest documented `thinking_level` per model (https://ai.google.dev/gemini-api/docs/thinking,
last updated 2026-10-14). `gemini-3.8-flash` rejects "minimal"; for a model absent from the
table (None) no thinking configuration is sent and the model default applies."""

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/"
"""Gemini Developer API (AI Studio keys). Vertex AI is never used: it is billed per project."""

FREE_TIER_POLICY_REVIEWED_ON = "2026-10-18"


def is_free_tier_model(provider: str, model: str) -> bool:
    return model in FREE_TIER_MODELS.get(provider, frozenset())
