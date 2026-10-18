"""Optional live smoke test against the configured free-tier Gemini profile.

Skipped unless STRAUSS_AI_LIVE_LLM=1 and an active profile is configured (environment or
`.env`). It sends exactly one small request on the explicitly active profile and accepts a
rate-limit or quota outcome as a valid (non-failing) result: quota is never worked around.
"""

import os

import pytest

from app.ai.application.ports.llm import LlmMessage, LlmOutcome, LlmTask, LlmTaskKind
from app.ai.infrastructure.llm.factory import build_llm_port
from app.ai.infrastructure.llm.settings import load_llm_settings
from app.ai.workflow.models import Intent, TurnInterpretation
from app.ai.workflow.prompts import INTERPRET_SYSTEM

pytestmark = pytest.mark.llm_live


@pytest.fixture
def live_settings():  # type: ignore[no-untyped-def]
    if os.environ.get("STRAUSS_AI_LIVE_LLM") != "1":
        pytest.skip("live LLM tests are opt-in (STRAUSS_AI_LIVE_LLM=1)")
    settings = load_llm_settings()
    if settings.active.provider == "disabled":
        pytest.skip("no LLM profile is configured")
    return settings


async def test_one_interpretation_request(live_settings) -> None:  # type: ignore[no-untyped-def]
    llm = build_llm_port(live_settings)
    result = await llm.generate(
        LlmTask(
            kind=LlmTaskKind.INTERPRET,
            system_instruction=INTERPRET_SYSTEM,
            messages=(LlmMessage("user", "STATE={}\nEMPLOYEE_MESSAGE=איזה רכבים מתאימים לי?"),),
            output_type=TurnInterpretation,
            max_output_tokens=400,
        )
    )
    if result.outcome in (LlmOutcome.RATE_LIMITED, LlmOutcome.QUOTA_EXHAUSTED):
        pytest.skip(f"free-tier limit reached: {result.diagnostic_code}")
    assert result.ok, result.diagnostic_code
    assert result.value is not None and result.value.intent is Intent.LIST_OPTIONS
