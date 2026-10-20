"""Cross-module business events (C4, decision D1). Implemented: Business `SqlBusinessEventFeed`
(B3a) and the AI `BusinessEventRelay` (full-system integration, M3).

Business records events (e.g. a representative answer) in its own Outbox in the same
transaction as the business change. The AI relay pulls them through `BusinessEventFeed`,
enqueues them into its own durable input queue deduplicated by `eventId`, and only then
acknowledges them. Delivery is at-least-once; consumers must be idempotent. The feed is a
capability injected only into the AI relay by bootstrap (decision D4) and returns minimal
identifiers and versions, never salary data. Not exported as JSON Schema (internal port).

`pending(limit, after_sequence=...)` (E1, approved 2026-10-20) lets a consumer page past
events it cannot accept yet, so one failing event never blocks later valid ones. The cursor
is per scan only: `sequence` may have gaps and may become visible out of order (a later
sequence can commit first), so a consumer must restart every scan from `None` and must never
persist the cursor as a delivery watermark.
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
    async def pending(
        self, limit: int, *, after_sequence: int | None = None
    ) -> list[BusinessEvent]:
        """Unacknowledged events in `sequence` order (at most `limit`), only those with
        `sequence > after_sequence` when it is given."""
        ...

    async def acknowledge(self, event_ids: list[UUID]) -> None:
        """Mark events delivered; idempotent (acknowledging twice has no further effect)."""
        ...
