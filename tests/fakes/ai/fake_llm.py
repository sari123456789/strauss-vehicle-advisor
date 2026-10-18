"""Deterministic LLM fakes for AI tests. No network, no API keys.

- `ScriptedLlm` implements `LlmPort`: it returns pre-scripted results per task kind, in order,
  and records every task it received (to assert on prompt content and call counts).
- `ScriptedProviderClient` implements the low-level `ProviderClient` used by `QuotaGuardedLlm`:
  it returns scripted responses or raises scripted `ProviderError`s.
- `FakeClock` / `FakeSleep` make time-based quota behavior deterministic.
"""

import asyncio
from collections import deque
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from app.ai.application.ports.llm import (
    LlmMessage,
    LlmOutcome,
    LlmResult,
    LlmTask,
    LlmTaskKind,
    OutputT,
)
from app.ai.infrastructure.llm.provider import (
    FinishKind,
    ProviderError,
    ProviderResponse,
)
from app.ai.workflow.models import ComposedText, Intent, TurnInterpretation


class ScriptedLlm:
    def __init__(self) -> None:
        self._scripts: dict[LlmTaskKind, deque[LlmResult[Any]]] = {
            kind: deque() for kind in LlmTaskKind
        }
        self.tasks: list[LlmTask[Any]] = []

    def script(self, kind: LlmTaskKind, *results: LlmResult[Any]) -> "ScriptedLlm":
        self._scripts[kind].extend(results)
        return self

    def interpret(self, *values: TurnInterpretation) -> "ScriptedLlm":
        return self.script(LlmTaskKind.INTERPRET, *(ok(v) for v in values))

    def compose(self, *texts: str) -> "ScriptedLlm":
        return self.script(LlmTaskKind.COMPOSE, *(ok(ComposedText(text=t)) for t in texts))

    async def generate(self, task: LlmTask[OutputT]) -> LlmResult[OutputT]:
        self.tasks.append(task)
        script = self._scripts[task.kind]
        if not script:
            return LlmResult(LlmOutcome.UNAVAILABLE, diagnostic_code="fake_script_exhausted")
        result = script.popleft()
        if result.value is not None and not isinstance(result.value, task.output_type):
            raise AssertionError(f"scripted {type(result.value).__name__} for {task.kind}")
        return result

    def calls(self, kind: LlmTaskKind) -> list[LlmTask[Any]]:
        return [t for t in self.tasks if t.kind is kind]


def ok(value: BaseModel) -> LlmResult[Any]:
    return LlmResult(LlmOutcome.OK, value=value)


def failure(outcome: LlmOutcome, retry_after: float | None = None) -> LlmResult[Any]:
    return LlmResult(outcome, retry_after_seconds=retry_after, diagnostic_code="scripted")


def interpretation(intent: Intent = Intent.UNCLEAR, **fields: Any) -> TurnInterpretation:
    return TurnInterpretation(intent=intent, **fields)


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 10, 18, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class FakeSleep:
    """Records requested sleeps and advances the fake clock instead of waiting."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.advance(seconds)


Scripted = ProviderResponse | ProviderError | float
"""A response, an error to raise, or a delay in seconds to hang for (to provoke a timeout)."""


class ScriptedProviderClient:
    def __init__(self, items: Iterable[Scripted] = ()) -> None:
        self._items: deque[Scripted] = deque(items)
        self.requests: list[dict[str, Any]] = []

    def push(self, *items: Scripted) -> None:
        self._items.extend(items)

    async def generate_json(
        self,
        *,
        system_instruction: str,
        messages: Sequence[LlmMessage],
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> ProviderResponse:
        self.requests.append(
            {
                "system_instruction": system_instruction,
                "messages": list(messages),
                "json_schema": json_schema,
                "max_output_tokens": max_output_tokens,
            }
        )
        if not self._items:
            raise AssertionError("unexpected provider call")
        item = self._items.popleft()
        if isinstance(item, ProviderError):
            raise item
        if isinstance(item, float):
            await asyncio.sleep(item)
            raise AssertionError("the call should have timed out")
        return item


def json_response(model: BaseModel) -> ProviderResponse:
    return ProviderResponse(model.model_dump_json(), FinishKind.COMPLETE, 10, 5)
