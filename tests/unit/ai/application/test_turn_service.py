"""Turn validation before any tool or model call."""

from uuid import uuid4

import pytest

from app.ai.application.ports.graph_runner import (
    InputKind,
    TurnInput,
    TurnOutcome,
    TurnStatus,
)
from app.ai.application.turn_service import (
    MAX_MESSAGE_CHARS,
    ConversationTurnService,
    InvalidTurnError,
    TurnBinding,
)
from app.contracts.tool_context import ActorRef, Origin, Role, ToolContext

CTX = ToolContext(
    actor=ActorRef(subject_id=uuid4(), roles=frozenset({Role.EMPLOYEE}), origin=Origin.WORKER),
    conversation_id=uuid4(),
    case_id=uuid4(),
    trace_id="trace",
)
BINDING = TurnBinding(thread_id=str(uuid4()), tool_context=CTX)


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ToolContext, TurnInput]] = []

    async def run_turn(
        self,
        *,
        thread_id: str,
        tool_context: ToolContext,
        turn: TurnInput,
        fence: object = None,
    ) -> TurnOutcome:
        self.calls.append((thread_id, tool_context, turn))
        return TurnOutcome(TurnStatus.COMPLETED, (), 0, 0)


@pytest.mark.parametrize(
    "turn",
    [
        TurnInput(uuid4(), InputKind.USER_MESSAGE, text="   "),
        TurnInput(uuid4(), InputKind.USER_MESSAGE, text=None),
        TurnInput(uuid4(), InputKind.USER_MESSAGE, text="א" * (MAX_MESSAGE_CHARS + 1)),
        TurnInput(uuid4(), InputKind.REPRESENTATIVE_ANSWERED),
    ],
)
async def test_invalid_turns_are_rejected(turn: TurnInput) -> None:
    runner = RecordingRunner()
    with pytest.raises(InvalidTurnError):
        await ConversationTurnService(runner).process(BINDING, turn)
    assert runner.calls == []


async def test_valid_turn_runs_on_the_stored_thread() -> None:
    runner = RecordingRunner()
    turn = TurnInput(uuid4(), InputKind.USER_MESSAGE, text="אילו רכבים מתאימים לי?")
    await ConversationTurnService(runner).process(BINDING, turn)
    assert runner.calls == [(BINDING.thread_id, CTX, turn)]
    assert BINDING.thread_id != str(CTX.conversation_id)


def test_thread_id_must_not_be_the_conversation_id() -> None:
    with pytest.raises(InvalidTurnError):
        TurnBinding(thread_id=str(CTX.conversation_id), tool_context=CTX)
