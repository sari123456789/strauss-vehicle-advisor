"""Durable input queue port (architecture Section 18).

The representative service enqueues `RepresentativeAnsweredEvent` inside the same Unit of Work
that saves the answer and the applicable case mutation. The Business Application never writes
AI tables directly; the AI Engine's Infrastructure provides the `agent.inputs` implementation.
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.contracts.representatives import RepresentativeAnsweredEvent


class EnqueueOutcome(StrEnum):
    ENQUEUED = "enqueued"
    DUPLICATE = "duplicate"
    """Same event ID with identical payload: no new input is created."""


class InputPayloadMismatchError(Exception):
    """Same event ID reused with a different payload."""


@runtime_checkable
class DurableInputQueue(Protocol):
    async def enqueue_representative_answered(
        self, event: RepresentativeAnsweredEvent
    ) -> EnqueueOutcome: ...
