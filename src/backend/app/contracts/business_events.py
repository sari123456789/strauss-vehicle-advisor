"""Cross-module business events (C4, decision D1). STATUS: PROVISIONAL - not implemented.

Business records events (e.g. a representative answer) in its own Outbox in the same
transaction as the business change. The AI worker pulls them through `BusinessEventFeed`,
enqueues them into its own durable input queue deduplicated by `eventId`, and only then
acknowledges them. Delivery is at-least-once; consumers must be idempotent. The feed is a
capability injected only into the AI worker by bootstrap (decision D4) and returns minimal
identifiers and versions, never salary data. Not exported as JSON Schema until implemented.
"""

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import Field

from app.contracts.common import ContractModel
from app.contracts.representatives import RepresentativeAnsweredEvent


class BusinessEvent(ContractModel):
    event_id: UUID
    sequence: int = Field(ge=1)
    """Outbox order; consumers must not assume gap-free sequences."""
    event_type: Literal["representative.answered"]
    schema_version: Literal[1] = 1
    occurred_at: datetime
    payload: RepresentativeAnsweredEvent


@runtime_checkable
class BusinessEventFeed(Protocol):
    async def pending(self, limit: int) -> list[BusinessEvent]:
        """Unacknowledged events in outbox order (at most `limit`)."""
        ...

    async def acknowledge(self, event_ids: list[UUID]) -> None:
        """Mark events delivered; idempotent (acknowledging twice has no further effect)."""
        ...
