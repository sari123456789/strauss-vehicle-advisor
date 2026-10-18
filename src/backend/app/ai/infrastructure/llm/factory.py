"""Build the `LlmPort` for the explicitly active credential profile.

Only the active profile is ever used. Other configured profiles are validated (so a mistake is
found at startup) but no client is created for them, and nothing here switches between them.
"""

from collections.abc import Callable, Mapping

from app.ai.application.ports.llm import LlmPort
from app.ai.infrastructure.llm.free_tier import DISABLED, GEMINI, is_free_tier_model
from app.ai.infrastructure.llm.gemini import GeminiProviderClient
from app.ai.infrastructure.llm.guarded import DisabledLlm, QuotaGuardedLlm
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
    profile = settings.active
    if profile.provider == DISABLED:
        return DisabledLlm()
    if not is_free_tier_model(profile.provider, profile.model) or not profile.free_tier_ack:
        raise ValueError("the active profile is not an approved free-tier profile")
    factories = DEFAULT_CLIENT_FACTORIES if client_factories is None else client_factories
    factory = factories.get(profile.provider)
    if factory is None:
        raise ValueError("no client is registered for the active provider")
    return QuotaGuardedLlm(profile, factory(profile), tracker or UsageTracker(), clock=clock)
