"""Gemini adapter: error classification, safe client settings and response mapping (offline)."""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors
from pydantic import BaseModel, SecretStr

from app.ai.application.ports.llm import LlmMessage
from app.ai.infrastructure.llm.free_tier import GEMINI_ENDPOINT
from app.ai.infrastructure.llm.gemini import (
    GeminiProviderClient,
    build_gemini_client,
    classify_gemini_error,
)
from app.ai.infrastructure.llm.provider import FinishKind, ProviderError, ProviderErrorKind
from app.ai.infrastructure.llm.schema import provider_json_schema
from app.ai.infrastructure.llm.settings import CredentialProfile
from app.ai.workflow.models import TurnInterpretation

SECRET = "AIzaFAKE-gemini-key-44444444444444444"
FREE_TIER_METRIC = "generativelanguage.googleapis.com/generate_content_free_tier_requests"


def gemini_profile(model: str = "gemini-3.5-flash-lite") -> CredentialProfile:
    return CredentialProfile(
        name="DEV",
        provider="gemini",
        model=model,
        api_key=SecretStr(SECRET),
        project_label="strauss-dev",
        rpm_limit=10,
        daily_request_limit=100,
        timeout_seconds=Decimal(20),
        free_tier_ack=True,
    )


def quota_error(quota_id: str, value: str = "20", delay: str | None = None) -> dict[str, Any]:
    details: list[dict[str, Any]] = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [
                {
                    "quotaMetric": FREE_TIER_METRIC,
                    "quotaId": quota_id,
                    "quotaValue": value,
                }
            ],
        }
    ]
    if delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay})
    return {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}


def test_per_minute_limit_is_temporary_with_retry_delay() -> None:
    error = classify_gemini_error(
        429, quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", delay="36s")
    )
    assert error.kind is ProviderErrorKind.RATE_LIMITED and error.retry_after_seconds == 36.0


def test_per_day_quota_is_exhaustion() -> None:
    error = classify_gemini_error(
        429, quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    )
    assert error.kind is ProviderErrorKind.QUOTA_EXHAUSTED


def test_zero_free_quota_means_not_free_tier() -> None:
    error = classify_gemini_error(
        429, quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier", value="0")
    )
    assert error.kind is ProviderErrorKind.NOT_FREE_TIER


def test_unstructured_429_is_treated_as_temporary() -> None:
    assert classify_gemini_error(429, {"error": {}}).kind is ProviderErrorKind.RATE_LIMITED


@pytest.mark.parametrize(
    ("code", "body", "kind"),
    [
        (401, {"error": {"status": "UNAUTHENTICATED"}}, ProviderErrorKind.AUTHENTICATION),
        (403, {"error": {"status": "PERMISSION_DENIED"}}, ProviderErrorKind.AUTHENTICATION),
        (
            400,
            {"error": {"message": "API key not valid", "details": [{"reason": "API_KEY_INVALID"}]}},
            ProviderErrorKind.AUTHENTICATION,
        ),
        (400, {"error": {"status": "INVALID_ARGUMENT"}}, ProviderErrorKind.BAD_REQUEST),
        (504, {"error": {"status": "DEADLINE_EXCEEDED"}}, ProviderErrorKind.TIMEOUT),
        (503, {"error": {"status": "UNAVAILABLE"}}, ProviderErrorKind.UNAVAILABLE),
    ],
)
def test_other_errors(code: int, body: dict[str, Any], kind: ProviderErrorKind) -> None:
    error = classify_gemini_error(code, body)
    assert error.kind is kind
    assert "API key" not in error.code


def test_client_is_pinned_to_ai_studio_without_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_API_KEY", "ambient-key-must-not-be-used")
    monkeypatch.setenv("GEMINI_API_KEY", "ambient-key-must-not-be-used")
    client = build_gemini_client(gemini_profile())
    api = client._api_client
    assert api.vertexai is False
    assert api.api_key == SECRET
    assert api._http_options.base_url == GEMINI_ENDPOINT
    assert api._http_options.retry_options.attempts == 1
    assert api._http_options.timeout == 20_000


def test_sdk_client_stays_open_after_garbage_collection() -> None:
    """Regression (M4): the adapter must keep the `genai.Client`; a collected client closes its
    HTTP client and every later call failed with "client has been closed"."""
    import gc

    adapter = GeminiProviderClient(gemini_profile())
    gc.collect()
    api = adapter._models._api_client
    assert adapter._sdk_client is not None and adapter._sdk_client._api_client is api
    http = getattr(api, "_async_httpx_client", None)
    assert http is None or not http.is_closed


@pytest.mark.parametrize("model", ["gemini-3.1-pro-preview", "gemini-omni-flash", "gemini-2.5-pro"])
def test_paid_or_unapproved_models_are_refused(model: str) -> None:
    with pytest.raises(ValueError, match="free-tier"):
        build_gemini_client(gemini_profile(model))
    with pytest.raises(ValueError, match="free-tier"):
        GeminiProviderClient(gemini_profile(model), models=object())


class Echo(BaseModel):
    text: str


class FakeModels:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def response(text: str, finish: str = "STOP", block: str | None = None) -> Any:
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
        prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
        usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=4),
    )


async def _call(models: FakeModels) -> Any:
    client = GeminiProviderClient(gemini_profile(), models=models)
    return await client.generate_json(
        system_instruction="sys",
        messages=[LlmMessage("user", "שלום"), LlmMessage("assistant", "היי")],
        json_schema=provider_json_schema(Echo),
        max_output_tokens=256,
    )


async def test_request_uses_structured_output_and_bounded_tokens() -> None:
    models = FakeModels(response('{"text": "ok"}'))
    result = await _call(models)
    assert result.text == '{"text": "ok"}' and result.finish is FinishKind.COMPLETE
    assert (result.input_tokens, result.output_tokens) == (12, 4)
    call = models.calls[0]
    assert call["model"] == "gemini-3.5-flash-lite"
    config = call["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema["properties"]["text"]["type"] == "string"
    assert config.max_output_tokens == 256 and config.candidate_count == 1
    assert [c.role for c in call["contents"]] == ["user", "model"]


@pytest.mark.parametrize(
    ("model", "level"),
    [
        ("gemini-3.5-flash-lite", "MINIMAL"),
        ("gemini-3.6-flash", "MINIMAL"),
        ("gemini-3.8-flash", "LOW"),  # rejects "minimal"
        ("gemini-3.1-flash-lite", None),  # not in the thinking table: model default
    ],
)
async def test_thinking_level_follows_the_documented_minimum(model: str, level: str | None) -> None:
    models = FakeModels(response('{"text": "ok"}'))
    client = GeminiProviderClient(gemini_profile(model), models=models)
    await client.generate_json(
        system_instruction="sys",
        messages=[LlmMessage("user", "x")],
        json_schema=provider_json_schema(Echo),
        max_output_tokens=64,
    )
    thinking = models.calls[0]["config"].thinking_config
    assert (thinking.thinking_level.name if thinking else None) == level


def test_every_free_tier_gemini_model_has_a_thinking_entry() -> None:
    from app.ai.infrastructure.llm.free_tier import FREE_TIER_MODELS, GEMINI_THINKING_LEVEL

    assert set(GEMINI_THINKING_LEVEL) == FREE_TIER_MODELS["gemini"]


@pytest.mark.parametrize(
    ("raw", "finish"),
    [
        (response("{", finish="MAX_TOKENS"), FinishKind.TRUNCATED),
        (response("", finish="SAFETY"), FinishKind.BLOCKED),
        (response("", block="SAFETY"), FinishKind.BLOCKED),
    ],
)
async def test_finish_reasons(raw: Any, finish: FinishKind) -> None:
    assert (await _call(FakeModels(raw))).finish is finish


async def test_sdk_errors_become_safe_provider_errors() -> None:
    body = quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    with pytest.raises(ProviderError) as raised:
        await _call(FakeModels(errors.ClientError(429, body)))
    assert raised.value.kind is ProviderErrorKind.QUOTA_EXHAUSTED
    assert raised.value.__cause__ is None  # the raw SDK error (and payload) is not chained


def test_interpretation_schema_is_ref_free_and_has_no_identifier_fields() -> None:
    schema = provider_json_schema(TurnInterpretation)
    text = str(schema)
    assert "$ref" not in text and "$defs" not in text

    def property_names(node: Any) -> set[str]:
        names: set[str] = set()
        if isinstance(node, dict):
            names |= set(node.get("properties", {}))
            for value in node.values():
                names |= property_names(value)
        elif isinstance(node, list):
            for value in node:
                names |= property_names(value)
        return names

    names = property_names(schema)
    assert not {n for n in names if n.lower().endswith("id") or "case" in n.lower()}
