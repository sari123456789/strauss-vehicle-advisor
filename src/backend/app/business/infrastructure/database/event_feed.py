"""`SqlBusinessEventFeed`: the Business side of `BusinessEventFeed` (decision D1, contract C4).

Events are written to `business.integration_outbox` by the Business transaction that makes
the change (B3a); this feed only reads and acknowledges them, each call in its own short
transaction. Delivery is at-least-once:

- `pending(limit)` returns unacknowledged events in `sequence` order and returns them again
  on every call until they are acknowledged. Reading never acknowledges.
  `after_sequence` (E1) pages within one scan: only events with a greater `sequence`.
- `acknowledge(ids)` sets `acknowledged_at` once; repeating it, or passing unknown IDs, has
  no further effect. The consumer (the AI worker) acknowledges only after it has durably
  accepted the event into its own queue.

The feed reads Business tables only; it never touches AI tables, queues or checkpoints.
`sequence` values may have gaps and may become visible out of order (a later sequence can
commit first); consumers must not assume either.
"""

from typing import cast
from uuid import UUID

from sqlalchemy import Table, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.infrastructure.database.models.answers import IntegrationOutboxRow
from app.contracts.business_events import BusinessEvent
from app.contracts.representatives import RepresentativeAnsweredEvent

OUTBOX = cast(Table, IntegrationOutboxRow.__table__)
MAX_BATCH = 500


class SqlBusinessEventFeed:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def pending(
        self, limit: int, *, after_sequence: int | None = None
    ) -> list[BusinessEvent]:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_BATCH:
            raise ValueError(f"limit must be between 1 and {MAX_BATCH}")
        if after_sequence is not None and (isinstance(after_sequence, bool) or after_sequence < 0):
            raise ValueError("after_sequence must be a non-negative integer")
        statement = select(OUTBOX).where(OUTBOX.c.acknowledged_at.is_(None))
        if after_sequence is not None:
            statement = statement.where(OUTBOX.c.sequence > after_sequence)
        statement = statement.order_by(OUTBOX.c.sequence).limit(limit)
        async with self._sessions() as session, session.begin():
            rows = (await session.execute(statement)).all()
        return [
            BusinessEvent(
                event_id=row.id,
                sequence=row.sequence,
                event_type=row.event_type,
                schema_version=row.schema_version,
                occurred_at=row.occurred_at,
                payload=RepresentativeAnsweredEvent.model_validate(row.payload),
            )
            for row in rows
        ]

    async def acknowledge(self, event_ids: list[UUID]) -> None:
        if not event_ids:
            return
        statement = (
            update(OUTBOX)
            .where(OUTBOX.c.id.in_(set(event_ids)), OUTBOX.c.acknowledged_at.is_(None))
            .values(acknowledged_at=func.now())
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)
