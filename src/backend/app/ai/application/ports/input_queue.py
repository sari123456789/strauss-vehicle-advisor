"""Durable input queue (AI-internal port, decision C3). STATUS: PROVISIONAL - no implementation.

Owned by the AI module. Under decision D1 the Business Module never writes AI inputs: a
representative answer is recorded in a Business Outbox and delivered to the AI through the
shared `BusinessEventFeed` contract (`app.contracts.business_events`); the AI worker then
enqueues it here, deduplicated by event ID. Shape and implementation are the AI developer's.
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
