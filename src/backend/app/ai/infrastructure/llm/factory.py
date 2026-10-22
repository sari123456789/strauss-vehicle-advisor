"""Build the `LlmPort` from the failover chain of credential profiles.

The active profile is tried first; the others are used in configuration order, and only when a
profile's daily quota is exhausted or it is rate limited (see `FailoverLlm`). Every profile is
checked here, so a mistake is found at startup and never on the first call that needs the
fallback.
"""

from collections.abc import Callable, Mapping

from app.ai.application.ports.llm import LlmPort
from app.ai.infrastructure.llm.free_tier import DISABLED, GEMINI, is_free_tier_model
from app.ai.infrastructure.llm.gemini import GeminiProviderClient
from app.ai.infrastructure.llm.guarded import DisabledLlm, FailoverLlm, QuotaGuardedLlm
from app.ai.infrastructure.llm.provider import ProviderClient
from app.ai.infrastructure.llm.settings import CredentialProfile, LlmSettings
from app.ai.infrastructure.llm.usage import Clock, UsageTracker

ProviderClientFactory = Callable[[CredentialProfile], ProviderClient]

DEFAULT_CLIENT_FACTORIES: Mapping[str, ProviderClientFactory] = {GEMINI: GeminiProviderClient}


def build_llm_port(
    settings: LlmSettings,
    *,
    tracker: UsageTracker | None = None,
    clock: Clock | None = None,
    client_factories: Mapping[str, ProviderClientFactory] | None = None,
) -> LlmPort:
    if settings.active.provider == DISABLED:
        return DisabledLlm()
    factories = DEFAULT_CLIENT_FACTORIES if client_factories is None else client_factories
    shared_tracker = tracker or UsageTracker()
    guards = [
        _guard(profile, factories, shared_tracker, clock)
        for profile in settings.failover_chain()
        if profile.provider != DISABLED
    ]
    return guards[0] if len(guards) == 1 else FailoverLlm(guards)


def _guard(
    profile: CredentialProfile,
    factories: Mapping[str, ProviderClientFactory],
    tracker: UsageTracker,
    clock: Clock | None,
) -> QuotaGuardedLlm:
    if not is_free_tier_model(profile.provider, profile.model) or not profile.free_tier_ack:
        raise ValueError(f"profile {profile.name} is not an approved free-tier profile")
    factory = factories.get(profile.provider)
    if factory is None:
        raise ValueError("no client is registered for the configured provider")
    return QuotaGuardedLlm(profile, factory(profile), tracker, clock=clock)
