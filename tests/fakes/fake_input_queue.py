"""In-memory `DurableInputQueue` honoring transaction staging and event-ID deduplication."""

from uuid import UUID

from app.application.ports.input_queue import EnqueueOutcome, InputPayloadMismatchError
from app.contracts.representatives import RepresentativeAnsweredEvent


class InMemoryInputQueue:
    def __init__(self) -> None:
        self.committed: dict[UUID, RepresentativeAnsweredEvent] = {}
        self._staged: dict[UUID, RepresentativeAnsweredEvent] = {}

    async def enqueue_representative_answered(
        self, event: RepresentativeAnsweredEvent
    ) -> EnqueueOutcome:
        existing = self.committed.get(event.event_id) or self._staged.get(event.event_id)
        if existing is not None:
            if existing != event:
                raise InputPayloadMismatchError(str(event.event_id))
            return EnqueueOutcome.DUPLICATE
        self._staged[event.event_id] = event
        return EnqueueOutcome.ENQUEUED

    def commit_staged(self) -> None:
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        self._staged.clear()
