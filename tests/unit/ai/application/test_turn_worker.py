"""TurnWorker logic that needs no database: history, turn mapping, loop resilience."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.ai.application.ports import conversation_store as ps
from app.ai.application.ports.graph_runner import InputKind
from app.ai.application.turn_worker import (
    HISTORY_MESSAGES,
    REPLAY_SAFE_TOOLS,
    TurnWorker,
    _history,
    _turn,
)
from app.contracts.business_port import CANONICAL_TOOL_NAMES

NOW = datetime(2026, 10, 19, tzinfo=UTC)


def _message(sequence: int, role: str, text: str, input_id: UUID | None) -> ps.MessageRecord:
    return ps.MessageRecord(uuid4(), uuid4(), sequence, role, "text", text, {}, input_id, NOW)


def _input(source: str, payload: dict[str, Any]) -> ps.InputRecord:
    return ps.InputRecord(uuid4(), uuid4(), 1, source, uuid4(), payload, ps.PROCESSING, 1)


def _worker(**kw: Any) -> TurnWorker:
    return TurnWorker(None, None, None, None, None, worker_id="w", **kw)  # type: ignore[arg-type]


def test_history_holds_only_earlier_visible_messages_and_is_bounded() -> None:
    current = uuid4()
    earlier = [
        _message(i, "user" if i % 2 else "assistant", f"m{i}", uuid4()) for i in range(1, 11)
    ]
    own = _message(11, "user", "now", current)
    later = _message(12, "user", "after", uuid4())
    history = _history([*earlier, own, later], current)
    assert len(history) == HISTORY_MESSAGES
    assert [h.text for h in history] == [f"m{i}" for i in range(5, 11)]
    assert {h.role for h in history} <= {"user", "assistant"}


def test_user_and_representative_inputs_map_to_turns() -> None:
    user = _input(ps.USER, {"text": "שלום"})
    turn = _turn(user, ())
    assert (turn.kind, turn.text, turn.input_id) == (InputKind.USER_MESSAGE, "שלום", user.input_id)
    request = uuid4()
    answered = _input(ps.REPRESENTATIVE_ANSWERED, {"requestId": str(request)})
    turn = _turn(answered, ())
    assert turn.kind is InputKind.REPRESENTATIVE_ANSWERED and turn.request_id == request


def test_retries_treat_every_idempotent_business_tool_as_replay_safe() -> None:
    assert frozenset(CANONICAL_TOOL_NAMES) == REPLAY_SAFE_TOOLS


def test_the_lease_must_leave_room_for_a_heartbeat() -> None:
    with pytest.raises(ValueError):
        _worker(lease_seconds=2)


async def test_the_loop_survives_an_infrastructure_outage_and_stops_on_request() -> None:
    worker = _worker()
    calls = 0
    stop = asyncio.Event()

    async def flaky_cycle() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("database restarting")
        if calls == 3:
            stop.set()

    worker.run_once = flaky_cycle  # type: ignore[method-assign]
    await asyncio.wait_for(worker.run_forever(stop, idle_seconds=0.01), timeout=2)
    assert calls == 3
