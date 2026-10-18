"""Checkpoint lineage and write fencing (AI-internal port, P2c stage 2).

`CheckpointFence` is an async check that raises `StaleClaimError` when the current input claim
no longer owns its input. The runner installs it in `current_checkpoint_fence` for one graph
run; the PostgreSQL checkpointer calls it before every checkpoint write.

This is check-then-write, NOT an atomic fence: LangGraph writes checkpoints on its own
connection, outside the claim's transaction, so a write can still land between a successful
check and the write itself. That residual race is closed by lineage verification
(`ConversationStore.bind_lineage` / `complete_input`), which turns any unexpected lineage into
`needs_reconciliation` instead of continuing silently.
"""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.ai.application.ports.conversation_store import LineageObservation
from app.ai.application.ports.response_staging import MessageRef

CheckpointFence = Callable[[], Awaitable[None]]

current_checkpoint_fence: ContextVar[CheckpointFence | None] = ContextVar(
    "current_checkpoint_fence", default=None
)


@dataclass(frozen=True)
class CheckpointRef:
    checkpoint_id: str
    parent_checkpoint_id: str | None


@runtime_checkable
class CheckpointLineageReader(Protocol):
    async def observe(self, thread_id: str, input_id: str) -> LineageObservation:
        """Latest checkpoint of the thread, its parent, and whether it contains the input."""
        ...

    async def latest_message_refs(self, thread_id: str, input_id: str) -> list[MessageRef] | None:
        """Response references recorded by the latest checkpoint for `input_id`, if any."""
        ...
