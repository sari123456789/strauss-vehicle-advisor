"""Turn execution port (architecture: `GraphRunner`). STATUS: PROVISIONAL.

The AI Application depends on this Protocol; the LangGraph implementation lives in
`app.ai.workflow.runner` and is wired by composition code, so Application never imports the
concrete workflow.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from app.ai.application.ports.checkpoints import CheckpointFence
from app.ai.application.ports.response_staging import MessageRef, ResponseStager
from app.contracts.tool_context import ToolContext


class InputKind(StrEnum):
    USER_MESSAGE = "user_message"
    REPRESENTATIVE_ANSWERED = "representative_answered"


@dataclass(frozen=True)
class HistoryMessage:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class TurnInput:
    input_id: UUID
    kind: InputKind
    text: str | None = None
    request_id: UUID | None = None
    """Representative request answered (only for `representative_answered`)."""
    recent_messages: tuple[HistoryMessage, ...] = ()
    """Recent visible messages, oldest first, excluding this input."""


@dataclass(frozen=True)
class AssistantMessage:
    message_id: UUID
    """Deterministic per (input, position): replaying a turn yields the same IDs."""
    text: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


class TurnStatus(StrEnum):
    COMPLETED = "completed"
    DUPLICATE = "duplicate"
    """The input was already processed on this thread; nothing was executed again."""


@dataclass(frozen=True)
class TurnOutcome:
    status: TurnStatus
    messages: tuple[AssistantMessage, ...]
    tool_calls: int
    llm_calls: int
    checkpoint_id: str | None = None
    """Checkpoint written by this run (lineage verification at completion)."""
    parent_checkpoint_id: str | None = None
    message_refs: tuple[MessageRef, ...] = ()
    """What the checkpoint records for this input's response (IDs and content hashes)."""


@runtime_checkable
class GraphRunner(Protocol):
    async def run_turn(
        self,
        *,
        thread_id: str,
        tool_context: ToolContext,
        turn: TurnInput,
        fence: CheckpointFence | None = None,
        stager: ResponseStager | None = None,
    ) -> TurnOutcome: ...
