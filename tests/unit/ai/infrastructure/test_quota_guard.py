"""`QuotaGuardedLlm`: 429 vs daily quota, bounded retries, no profile/model/provider switching."""

import logging
from decimal import Decimal

import pytest
from pydantic import BaseModel, SecretStr

from app.ai.application.ports.llm import (
    LlmMessage,
    LlmOutcome,
    LlmTask,
    LlmTaskKind,
)
from app.ai.infrastructure.llm.factory import build_llm_port
from app.ai.infrastructure.llm.guarded import DisabledLlm, QuotaGuardedLlm
from app.ai.infrastructure.llm.provider import (
    FinishKind,
    ProviderClient,
    ProviderError,
    ProviderErrorKind,
    ProviderResponse,
)
from app.ai.infrastructure.llm.settings import CredentialProfile, load_llm_settings
from app.ai.infrastructure.llm.usage import UsageTracker
from tests.fakes.ai.fake_llm import FakeClock, FakeSleep, ScriptedProviderClient, json_response

SECRET = "AIzaFAKE-guard-key-22222222222222222"


class Answer(BaseModel):
    word: str


TASK = LlmTask(
    kind=LlmTaskKind.INTERPRET,
    system_instruction="system",
    messages=(LlmMessage("user", "שלום"),),
    output_type=Answer,
)


def profile(
    name: str = "DEV", project: str = "strauss-dev", **changes: object
) -> CredentialProfile:
    values: dict[str, object] = {
        "name": name,
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "api_key": SecretStr(SECRET),
        "project_label": project,
        "rpm_limit": 10,
        "daily_request_limit": 100,
        "timeout_seconds": Decimal("0.05"),
        "free_tier_ack": True,
    }
    values.update(changes)
    return CredentialProfile(**values)  # type: ignore[arg-type]


def guarded(
    client: ScriptedProviderClient, **profile_changes: object
) -> tuple[QuotaGuardedLlm, FakeClock, FakeSleep, UsageTracker]:
    clock = FakeClock()
    sleep = FakeSleep(clock)
    tracker = UsageTracker()
    llm = QuotaGuardedLlm(profile(**profile_changes), client, tracker, clock=clock, sleep=sleep)
    return llm, clock, sleep, tracker


def rate_limited(seconds: float | None) -> ProviderError:
    return ProviderError(ProviderErrorKind.RATE_LIMITED, "per_minute_limit", seconds)


async def test_valid_output_is_parsed_and_counted() -> None:
    client = ScriptedProviderClient([json_response(Answer(word="כן"))])
    llm, clock, _, tracker = guarded(client)
    result = await llm.generate(TASK)
    assert result.ok and result.value == Answer(word="כן")
    assert tracker.snapshot(profile().quota_key, clock.now()).requests_today == 1
    assert "$defs" not in client.requests[0]["json_schema"]


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (ProviderResponse('{"wrong": 1}', FinishKind.COMPLETE), "schema_validation_failed"),
        (ProviderResponse("not json", FinishKind.COMPLETE), "schema_validation_failed"),
        (ProviderResponse('{"word": "x"', FinishKind.TRUNCATED), "truncated"),
    ],
)
async def test_invalid_output_is_never_used(response: ProviderResponse, code: str) -> None:
    llm, *_ = guarded(ScriptedProviderClient([response]))
    result = await llm.generate(TASK)
    assert result.outcome is LlmOutcome.INVALID_OUTPUT and result.value is None
    assert result.diagnostic_code == code


async def test_blocked_response_is_a_refusal() -> None:
    llm, *_ = guarded(ScriptedProviderClient([ProviderResponse("", FinishKind.BLOCKED)]))
    assert (await llm.generate(TASK)).outcome is LlmOutcome.REFUSED


async def test_short_rate_limit_is_retried_once_on_the_same_profile() -> None:
    client = ScriptedProviderClient([rate_limited(2.0), json_response(Answer(word="ok"))])
    llm, clock, sleep, tracker = guarded(client)
    result = await llm.generate(TASK)
    assert result.ok
    assert sleep.calls == [2.0]
    assert len(client.requests) == 2
    assert tracker.snapshot(profile().quota_key, clock.now()).requests_today == 2


async def test_repeated_short_rate_limit_is_not_retried_again() -> None:
    client = ScriptedProviderClient([rate_limited(1.0), rate_limited(1.0)])
    llm, _, sleep, _ = guarded(client)
    result = await llm.generate(TASK)
    assert result.outcome is LlmOutcome.RATE_LIMITED
    assert len(client.requests) == 2 and sleep.calls == [1.0]


async def test_long_rate_limit_returns_and_cools_down_without_calling_the_provider() -> None:
    client = ScriptedProviderClient([rate_limited(40.0)])
    llm, clock, sleep, _ = guarded(client)
    first = await llm.generate(TASK)
    assert first.outcome is LlmOutcome.RATE_LIMITED and first.retry_after_seconds == 40.0
    assert sleep.calls == []
    clock.advance(10)
    second = await llm.generate(TASK)
    assert second.outcome is LlmOutcome.RATE_LIMITED
    assert second.diagnostic_code == "provider_rate_limit_cooldown"
    assert len(client.requests) == 1  # no request was sent during the cooldown
    clock.advance(31)
    client.push(json_response(Answer(word="ok")))
    assert (await llm.generate(TASK)).ok


async def test_daily_quota_exhaustion_blocks_until_reset() -> None:
    client = ScriptedProviderClient(
        [ProviderError(ProviderErrorKind.QUOTA_EXHAUSTED, "daily_quota_exhausted")]
    )
    llm, clock, sleep, _ = guarded(client)
    first = await llm.generate(TASK)
    assert first.outcome is LlmOutcome.QUOTA_EXHAUSTED
    assert first.retry_after_seconds is not None and first.retry_after_seconds > 3600
    assert sleep.calls == []
    clock.advance(3600)
    again = await llm.generate(TASK)
    assert again.outcome is LlmOutcome.QUOTA_EXHAUSTED
    assert len(client.requests) == 1
    clock.advance(first.retry_after_seconds)
    client.push(json_response(Answer(word="ok")))
    assert (await llm.generate(TASK)).ok


async def test_local_daily_budget_is_enforced_before_the_provider() -> None:
    client = ScriptedProviderClient([json_response(Answer(word="a"))])
    llm, *_ = guarded(client, daily_request_limit=1)
    assert (await llm.generate(TASK)).ok
    second = await llm.generate(TASK)
    assert second.outcome is LlmOutcome.QUOTA_EXHAUSTED
    assert second.diagnostic_code == "local_daily_budget_exhausted"
    assert len(client.requests) == 1


async def test_local_rpm_ceiling_is_enforced_before_the_provider() -> None:
    client = ScriptedProviderClient([json_response(Answer(word="a"))] * 2)
    llm, *_ = guarded(client, rpm_limit=2)
    assert (await llm.generate(TASK)).ok
    assert (await llm.generate(TASK)).ok
    third = await llm.generate(TASK)
    assert third.outcome is LlmOutcome.RATE_LIMITED and third.diagnostic_code == "local_rpm_limit"
    assert len(client.requests) == 2


async def test_model_without_free_quota_is_blocked_permanently() -> None:
    client = ScriptedProviderClient(
        [ProviderError(ProviderErrorKind.NOT_FREE_TIER, "free_tier_quota_is_zero")]
    )
    llm, clock, _, _ = guarded(client)
    assert (await llm.generate(TASK)).outcome is LlmOutcome.BLOCKED_BY_POLICY
    clock.advance(2 * 86400)
    assert (await llm.generate(TASK)).outcome is LlmOutcome.BLOCKED_BY_POLICY
    assert len(client.requests) == 1


async def test_timeout_and_unexpected_errors_are_results_not_exceptions() -> None:
    llm, *_ = guarded(ScriptedProviderClient([5.0]))
    assert (await llm.generate(TASK)).outcome is LlmOutcome.TIMEOUT

    class Exploding:
        async def generate_json(self, **_: object) -> ProviderResponse:
            raise RuntimeError("boom " + SECRET)

    clock = FakeClock()
    crashing = QuotaGuardedLlm(profile(), Exploding(), UsageTracker(), clock=clock)
    result = await crashing.generate(TASK)
    assert result.outcome is LlmOutcome.UNAVAILABLE
    assert SECRET not in (result.diagnostic_code or "")


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [
        (ProviderErrorKind.AUTHENTICATION, LlmOutcome.UNAVAILABLE),
        (ProviderErrorKind.BAD_REQUEST, LlmOutcome.UNAVAILABLE),
        (ProviderErrorKind.UNAVAILABLE, LlmOutcome.UNAVAILABLE),
        (ProviderErrorKind.TIMEOUT, LlmOutcome.TIMEOUT),
    ],
)
async def test_other_provider_errors_are_not_retried(
    kind: ProviderErrorKind, outcome: LlmOutcome
) -> None:
    client = ScriptedProviderClient([ProviderError(kind, kind.value)])
    llm, _, sleep, _ = guarded(client)
    assert (await llm.generate(TASK)).outcome is outcome
    assert len(client.requests) == 1 and sleep.calls == []


async def test_logs_never_contain_the_api_key_or_prompt(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.llm")
    llm, *_ = guarded(ScriptedProviderClient([json_response(Answer(word="ok"))]))
    await llm.generate(TASK)
    assert "llm_call" in caplog.text and "strauss-dev" in caplog.text
    assert SECRET not in caplog.text and "שלום" not in caplog.text


# --- Factory: only the explicitly active profile is ever used ---------------------------------


def _two_profile_env(active: str) -> dict[str, str]:
    env = {"STRAUSS_AI_LLM_PROFILES": "dev,test", "STRAUSS_AI_LLM_ACTIVE_PROFILE": active}
    for name in ("DEV", "TEST"):
        env.update(
            {
                f"STRAUSS_AI_LLM_PROFILE_{name}_PROVIDER": "gemini",
                f"STRAUSS_AI_LLM_PROFILE_{name}_API_KEY": f"AIzaFAKE-{name}-key-3333333333333",
                f"STRAUSS_AI_LLM_PROFILE_{name}_PROJECT_LABEL": f"strauss-{name.lower()}",
                f"STRAUSS_AI_LLM_PROFILE_{name}_FREE_TIER_ACK": "true",
            }
        )
    return env


async def test_exhausted_active_profile_never_rotates_to_another_profile() -> None:
    built: list[str] = []
    clients: dict[str, ScriptedProviderClient] = {}

    def factory(p: CredentialProfile) -> ProviderClient:
        built.append(p.name)
        clients[p.name] = ScriptedProviderClient(
            [ProviderError(ProviderErrorKind.QUOTA_EXHAUSTED, "daily_quota_exhausted")]
        )
        return clients[p.name]

    settings = load_llm_settings(_two_profile_env("dev"))
    llm = build_llm_port(settings, client_factories={"gemini": factory})
    first = await llm.generate(TASK)
    second = await llm.generate(TASK)
    assert first.outcome is second.outcome is LlmOutcome.QUOTA_EXHAUSTED
    assert built == ["DEV"]  # the TEST profile's client is never created
    assert len(clients["DEV"].requests) == 1


async def test_switching_profile_is_a_configuration_change() -> None:
    built: list[str] = []

    def factory(p: CredentialProfile) -> ProviderClient:
        built.append(p.name)
        return ScriptedProviderClient([json_response(Answer(word="ok"))])

    llm = build_llm_port(
        load_llm_settings(_two_profile_env("test")), client_factories={"gemini": factory}
    )
    assert (await llm.generate(TASK)).ok
    assert built == ["TEST"]


async def test_disabled_configuration_never_calls_a_provider() -> None:
    llm = build_llm_port(load_llm_settings({}), client_factories={})
    assert isinstance(llm, DisabledLlm)
    result = await llm.generate(TASK)
    assert result.outcome is LlmOutcome.BLOCKED_BY_POLICY
    assert result.diagnostic_code == "llm_disabled"


def test_factory_refuses_a_profile_that_is_not_free_tier() -> None:
    settings = load_llm_settings(_two_profile_env("dev"))
    unsafe = profile(model="gemini-3.1-pro-preview")
    forged = type(settings)(active_profile="DEV", profiles={"DEV": unsafe})
    with pytest.raises(ValueError, match="free-tier"):
        build_llm_port(forged, client_factories={"gemini": lambda p: ScriptedProviderClient()})
    no_ack = type(settings)(active_profile="DEV", profiles={"DEV": profile(free_tier_ack=False)})
    with pytest.raises(ValueError, match="free-tier"):
        build_llm_port(no_ack, client_factories={"gemini": lambda p: ScriptedProviderClient()})
