"""Local API server for the browser journey test (test infrastructure, not production code).

The real application (`app.main.create_app`, real Business services, real PostgreSQL, the
in-process worker and relay) with ONE substitution: a deterministic keyword model instead of
Gemini, so the Playwright journey never calls a paid or rate-limited provider.

Usage (from src/backend; local Docker database only, never Neon):
    PYTHONPATH=../.. .venv/Scripts/python.exe ../../tests/e2e_server.py --port 8000
"""

import argparse
import asyncio
from typing import Any

import uvicorn

from app.ai.application.ports.llm import LlmOutcome, LlmResult, LlmTask, LlmTaskKind
from app.ai.workflow.models import Intent, OptionReference, TurnInterpretation
from app.main import create_app
from app.settings import Settings

KEYWORDS: list[tuple[str, dict[str, Any]]] = [
    (
        "ATTO",
        {
            "intent": Intent.CALCULATE_OPTIONS,
            "option_refs": [OptionReference(kind="catalogue_id", value="CAR-10")],
        },
    ),
    ("ויתור", {"intent": Intent.CALCULATE_OPTIONS, "include_waiver": True}),
    ("אוותר", {"intent": Intent.CALCULATE_OPTIONS, "include_waiver": True}),
    ("רכבים", {"intent": Intent.LIST_OPTIONS}),
]


class KeywordLlm:
    """Interprets a few Hebrew keywords deterministically; composing falls back to the
    workflow's own templates (every amount still comes from the Business result)."""

    async def generate(self, task: LlmTask[Any]) -> LlmResult[Any]:
        if task.kind is not LlmTaskKind.INTERPRET:
            return LlmResult(LlmOutcome.UNAVAILABLE, diagnostic_code="e2e_keyword_model")
        text = task.messages[-1].text if task.messages else ""
        for keyword, fields in KEYWORDS:
            if keyword in text:
                return LlmResult(LlmOutcome.OK, value=TurnInterpretation(**fields))
        return LlmResult(LlmOutcome.OK, value=TurnInterpretation(intent=Intent.SMALLTALK))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    # The Angular dev server (ng serve / Playwright) is the browser origin; refresh and logout
    # accept only allowed origins (CSRF check).
    settings = Settings(
        worker_in_process=True,
        auth_allowed_origins=[
            "http://127.0.0.1:4300",
            "http://localhost:4200",
            "http://127.0.0.1:4200",
        ],
    )
    app = create_app(settings, llm=KeywordLlm())
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, loop="none")
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    main()
