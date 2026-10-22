"""LLM credential profiles loaded from environment variables only.

Several profiles may be configured, each on its own Google Cloud project. The active profile
(`STRAUSS_AI_LLM_ACTIVE_PROFILE`) is tried first; when its daily quota is exhausted or it is rate
limited the adapter moves to the next configured profile, in configuration order.

    STRAUSS_AI_LLM_PROFILES=dev,test
    STRAUSS_AI_LLM_ACTIVE_PROFILE=dev
    STRAUSS_AI_LLM_PROFILE_DEV_PROVIDER=gemini
    STRAUSS_AI_LLM_PROFILE_DEV_MODEL=gemini-3.5-flash-lite
    STRAUSS_AI_LLM_PROFILE_DEV_API_KEY=<secret>
    STRAUSS_AI_LLM_PROFILE_DEV_PROJECT_LABEL=strauss-dev
    STRAUSS_AI_LLM_PROFILE_DEV_RPM_LIMIT=10
    STRAUSS_AI_LLM_PROFILE_DEV_DAILY_REQUEST_LIMIT=200
    STRAUSS_AI_LLM_PROFILE_DEV_TIMEOUT_SECONDS=20
    STRAUSS_AI_LLM_PROFILE_DEV_FREE_TIER_ACK=true

Error messages name variables, never their values.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr

from app.ai.infrastructure.llm.free_tier import (
    APPROVED_PROVIDERS,
    DEFAULT_MODEL,
    DISABLED,
    is_free_tier_model,
)

PREFIX = "STRAUSS_AI_LLM_"
_PROFILE_NAME = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")
_PROJECT_LABEL = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

DEFAULT_RPM_LIMIT = 10
DEFAULT_DAILY_REQUEST_LIMIT = 200
DEFAULT_TIMEOUT_SECONDS = Decimal(20)
MAX_TIMEOUT_SECONDS = Decimal(60)
"""Architecture §10: model-call operation budget."""


class LlmConfigurationError(ValueError):
    """Invalid LLM configuration. Messages contain variable names only, never values."""


@dataclass(frozen=True)
class CredentialProfile:
    name: str
    provider: str
    model: str
    api_key: SecretStr | None
    project_label: str
    """Non-secret identifier of the provider project; quotas are tracked per project."""
    rpm_limit: int
    daily_request_limit: int
    timeout_seconds: Decimal
    free_tier_ack: bool

    @property
    def quota_key(self) -> tuple[str, str, str]:
        """Gemini quotas are per project and model; profiles sharing both share counters."""
        return (self.provider, self.project_label, self.model)


@dataclass(frozen=True)
class LlmSettings:
    active_profile: str
    profiles: Mapping[str, CredentialProfile]

    @property
    def active(self) -> CredentialProfile:
        return self.profiles[self.active_profile]

    def failover_chain(self) -> tuple[CredentialProfile, ...]:
        """The active profile first, then the others in configuration order."""
        others = tuple(p for name, p in self.profiles.items() if name != self.active_profile)
        return (self.active, *others)


def _var(name: str, profile: str | None = None) -> str:
    return f"{PREFIX}PROFILE_{profile}_{name}" if profile else f"{PREFIX}{name}"


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) < 1:
        raise LlmConfigurationError(f"{key} must be a positive integer")
    return int(raw)


def _timeout(env: Mapping[str, str], key: str) -> Decimal:
    raw = env.get(key, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise LlmConfigurationError(f"{key} must be a number of seconds") from None
    if not value.is_finite() or value <= 0 or value > MAX_TIMEOUT_SECONDS:
        raise LlmConfigurationError(f"{key} must be between 0 and {MAX_TIMEOUT_SECONDS} seconds")
    return value


def _flag(env: Mapping[str, str], key: str) -> bool:
    raw = env.get(key, "").strip().lower()
    if raw in ("", "false", "0", "no"):
        return False
    if raw in ("true", "1", "yes"):
        return True
    raise LlmConfigurationError(f"{key} must be true or false")


def _load_profile(env: Mapping[str, str], name: str) -> CredentialProfile:
    provider = env.get(_var("PROVIDER", name), "").strip().lower()
    if provider not in APPROVED_PROVIDERS:
        raise LlmConfigurationError(
            f"{_var('PROVIDER', name)} must be one of: {', '.join(sorted(APPROVED_PROVIDERS))}"
        )
    model = env.get(_var("MODEL", name), "").strip() or DEFAULT_MODEL[provider]
    if not is_free_tier_model(provider, model):
        raise LlmConfigurationError(
            f"{_var('MODEL', name)} is not an approved free-tier model for {provider}"
        )
    project_label = env.get(_var("PROJECT_LABEL", name), "").strip() or name.lower()
    if not _PROJECT_LABEL.match(project_label):
        raise LlmConfigurationError(f"{_var('PROJECT_LABEL', name)} has an invalid format")
    raw_key = env.get(_var("API_KEY", name), "").strip()
    free_tier_ack = _flag(env, _var("FREE_TIER_ACK", name))
    if provider != DISABLED:
        if not raw_key:
            raise LlmConfigurationError(f"{_var('API_KEY', name)} is required")
        if not free_tier_ack:
            raise LlmConfigurationError(
                f"{_var('FREE_TIER_ACK', name)} must be true: confirm the project has no "
                "billing account, so calls can only use the free tier"
            )
    return CredentialProfile(
        name=name,
        provider=provider,
        model=model,
        api_key=SecretStr(raw_key) if raw_key else None,
        project_label=project_label,
        rpm_limit=_positive_int(env, _var("RPM_LIMIT", name), DEFAULT_RPM_LIMIT),
        daily_request_limit=_positive_int(
            env, _var("DAILY_REQUEST_LIMIT", name), DEFAULT_DAILY_REQUEST_LIMIT
        ),
        timeout_seconds=_timeout(env, _var("TIMEOUT_SECONDS", name)),
        free_tier_ack=free_tier_ack,
    )


def read_environment(env_file: Path | None = Path(".env")) -> dict[str, str]:
    """Process environment over an optional `.env` file (as the shared settings do)."""
    values: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        values.update(
            {k: v for k, v in dotenv_values(env_file, encoding="utf-8").items() if v is not None}
        )
    values.update(os.environ)
    return values


def load_llm_settings(env: Mapping[str, str] | None = None) -> LlmSettings:
    """Read and validate all profiles; with no profiles configured the LLM is disabled."""
    env = read_environment() if env is None else env
    raw_names = env.get(_var("PROFILES"), "").strip()
    if not raw_names:
        disabled = CredentialProfile(
            name="DISABLED",
            provider=DISABLED,
            model=DEFAULT_MODEL[DISABLED],
            api_key=None,
            project_label="disabled",
            rpm_limit=DEFAULT_RPM_LIMIT,
            daily_request_limit=DEFAULT_DAILY_REQUEST_LIMIT,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            free_tier_ack=False,
        )
        return LlmSettings(active_profile="DISABLED", profiles={"DISABLED": disabled})
    names = [part.strip().upper() for part in raw_names.split(",") if part.strip()]
    if len(set(names)) != len(names):
        raise LlmConfigurationError(f"{_var('PROFILES')} lists a profile twice")
    for name in names:
        if not _PROFILE_NAME.match(name):
            raise LlmConfigurationError(
                f"{_var('PROFILES')} entries must be letters/digits, starting with a letter"
            )
    profiles = {name: _load_profile(env, name) for name in names}
    active = env.get(_var("ACTIVE_PROFILE"), "").strip().upper()
    if active not in profiles:
        raise LlmConfigurationError(
            f"{_var('ACTIVE_PROFILE')} must name one of the profiles in {_var('PROFILES')}"
        )
    return LlmSettings(active_profile=active, profiles=profiles)
