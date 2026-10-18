"""Conversation turn coordination.

Validates an incoming turn and delegates it to the injected `GraphRunner` on the
conversation's own LangGraph thread. The `TurnBinding` (thread ID and `ToolContext`) is built
by `ConversationService.turn_binding` from persisted conversation ownership; the thread ID is
a separate stored value, never the conversation ID, and identity is never derived from
message content. Durable inputs are recorded by `ConversationService`; ordering and worker
recovery arrive in P2c/P3.
"""

from dataclasses import dataclass

from app.ai.application.ports.graph_runner import GraphRunner, InputKind, TurnInput, TurnOutcome
from app.contracts.tool_context import ToolContext

MAX_MESSAGE_CHARS = 8000
"""Architecture §6 demonstration support limit."""


class InvalidTurnError(ValueError):
    """The turn is malformed; it is rejected before any tool or model call."""


@dataclass(frozen=True)
class TurnBinding:
    """What a turn needs: the separate LangGraph thread and the server-built tool context."""

    thread_id: str
    tool_context: ToolContext

    def __post_init__(self) -> None:
        if not self.thread_id or self.thread_id == str(self.tool_context.conversation_id):
            raise InvalidTurnError("the LangGraph thread ID must differ from the conversation ID")


class ConversationTurnService:
    def __init__(self, runner: GraphRunner) -> None:
        self._runner = runner

    async def process(self, binding: TurnBinding, turn: TurnInput) -> TurnOutcome:
        if turn.kind is InputKind.USER_MESSAGE:
            if turn.text is None or not turn.text.strip():
                raise InvalidTurnError("a user message requires text")
            if len(turn.text) > MAX_MESSAGE_CHARS:
                raise InvalidTurnError("the message exceeds the supported length")
        elif turn.request_id is None:
            raise InvalidTurnError("a representative answer requires a request ID")
        return await self._runner.run_turn(
            thread_id=binding.thread_id, tool_context=binding.tool_context, turn=turn
        )
