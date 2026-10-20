"""M3: `BusinessEventRelay` delivery semantics with in-memory feed and queue doubles.

Covers: acknowledge only after durable enqueue, transient failures (no acknowledgement,
backoff), poison events (never acknowledged, never blocking later events, per-event backoff,
quarantine), mixed pages, duplicates, pagination with the E1 cursor and graceful stop.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.ai.application.errors import ConversationAccessError
from app.ai.application.event_relay import (
    EVENT_BACKOFF_SECONDS,
    QUARANTINE_AFTER,
    BusinessEventRelay,
    RelayTransientError,
)
from app.ai.application.ports.input_queue import EnqueueOutcome, InputPayloadMismatchError
from app.contracts.business_events import BusinessEvent
from app.contracts.representatives import RepresentativeAnsweredEvent


def event(sequence: int, case_id: UUID | None = None) -> BusinessEvent:
    event_id = uuid4()
    now = datetime(2026, 10, 20, tzinfo=UTC)
    return BusinessEvent(
        event_id=event_id,
        sequence=sequence,
        event_type="representative.answered",
        occurred_at=now,
        payload=RepresentativeAnsweredEvent(
            event_id=event_id,
            occurred_at=now,
            request_id=uuid4(),
            case_id=case_id or uuid4(),
            request_version=1,
            case_version_after=2,
        ),
    )


class Feed:
    def __init__(self, events: list[BusinessEvent]) -> None:
        self.events = list(events)
        self.acked: list[UUID] = []
        self.pages: list[int | None] = []
        self.fail_pending = False
        self.fail_ack = False

    async def pending(
        self, limit: int, *, after_sequence: int | None = None
    ) -> list[BusinessEvent]:
        self.pages.append(after_sequence)
        if self.fail_pending:
            raise ConnectionError("db down")
        acked = set(self.acked)
        open_ = [e for e in self.events if e.event_id not in acked]
        if after_sequence is not None:
            open_ = [e for e in open_ if e.sequence > after_sequence]
        return sorted(open_, key=lambda e: e.sequence)[:limit]

    async def acknowledge(self, event_ids: list[UUID]) -> None:
        if self.fail_ack:
            raise ConnectionError("db down")
        self.acked.extend(i for i in event_ids if i not in self.acked)


class Queue:
    def __init__(self) -> None:
        self.inputs: dict[UUID, RepresentativeAnsweredEvent] = {}
        self.unbound: set[UUID] = set()
        self.mismatch: set[UUID] = set()
        self.fail_transient = False

    async def enqueue_representative_answered(
        self, payload: RepresentativeAnsweredEvent
    ) -> EnqueueOutcome:
        if self.fail_transient:
            raise OSError("agent db down")
        if payload.case_id in self.unbound:
            raise ConversationAccessError("no conversation is bound to this case")
        if payload.event_id in self.mismatch:
            raise InputPayloadMismatchError(str(payload.event_id))
        if payload.event_id in self.inputs:
            return EnqueueOutcome.DUPLICATE
        self.inputs[payload.event_id] = payload
        return EnqueueOutcome.ENQUEUED


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def relay(feed: Feed, queue: Queue, clock: Clock, **kw: int) -> BusinessEventRelay:
    return BusinessEventRelay(feed, queue, clock=clock, **kw)


async def test_acknowledges_only_events_that_were_durably_enqueued() -> None:
    events = [event(1), event(2)]
    feed, queue = Feed(events), Queue()
    report = await relay(feed, queue, Clock()).run_once()
    assert report.delivered == 2
    assert set(queue.inputs) == {e.event_id for e in events}
    assert feed.acked == [e.event_id for e in events]


async def test_transient_enqueue_failure_acknowledges_nothing_and_raises() -> None:
    feed, queue = Feed([event(1)]), Queue()
    queue.fail_transient = True
    with pytest.raises(RelayTransientError):
        await relay(feed, queue, Clock()).run_once()
    assert feed.acked == []


async def test_transient_failure_mid_page_acknowledges_only_what_was_accepted() -> None:
    first, second = event(1), event(2)
    feed, queue = Feed([first, second]), Queue()
    original = queue.enqueue_representative_answered

    async def flaky(payload: RepresentativeAnsweredEvent) -> EnqueueOutcome:
        if payload.event_id == second.event_id:
            raise OSError("agent db down")
        return await original(payload)

    queue.enqueue_representative_answered = flaky  # type: ignore[method-assign]
    with pytest.raises(RelayTransientError):
        await relay(feed, queue, Clock()).run_once()
    assert feed.acked == [first.event_id]


async def test_feed_failure_is_transient() -> None:
    feed = Feed([event(1)])
    feed.fail_pending = True
    with pytest.raises(RelayTransientError):
        await relay(feed, Queue(), Clock()).run_once()


async def test_poison_event_is_kept_and_does_not_block_later_events() -> None:
    poison, good = event(1), event(2)
    feed, queue = Feed([poison, good]), Queue()
    queue.unbound.add(poison.payload.case_id)
    r = relay(feed, queue, Clock())
    report = await r.run_once()
    assert report.delivered == 1 and report.failed == 1
    assert feed.acked == [good.event_id]
    assert poison.event_id not in queue.inputs
    assert r.status()["backingOff"] == 1


async def test_payload_mismatch_is_a_permanent_failure_and_never_acknowledged() -> None:
    bad = event(1)
    feed, queue = Feed([bad]), Queue()
    queue.mismatch.add(bad.event_id)
    report = await relay(feed, queue, Clock()).run_once()
    assert report.failed == 1 and feed.acked == []


async def test_poison_event_backs_off_and_is_retried_after_the_delay() -> None:
    poison = event(1)
    feed, queue, clock = Feed([poison]), Queue(), Clock()
    queue.unbound.add(poison.payload.case_id)
    r = relay(feed, queue, clock)
    await r.run_once()
    deferred = await r.run_once()
    assert deferred.deferred == 1 and deferred.failed == 0
    clock.now += EVENT_BACKOFF_SECONDS[0] + 1
    queue.unbound.clear()  # the conversation now exists
    retried = await r.run_once()
    assert retried.delivered == 1 and feed.acked == [poison.event_id]
    assert r.status()["backingOff"] == 0


async def test_repeated_failures_quarantine_the_event_but_keep_it() -> None:
    poison = event(1)
    feed, queue, clock = Feed([poison]), Queue(), Clock()
    queue.unbound.add(poison.payload.case_id)
    r = relay(feed, queue, clock)
    for _ in range(QUARANTINE_AFTER):
        await r.run_once()
        clock.now += 10_000
    assert r.status()["quarantined"] == 1
    assert feed.acked == []


async def test_duplicate_delivery_is_acknowledged_without_a_second_input() -> None:
    first = event(1)
    feed, queue = Feed([first]), Queue()
    queue.inputs[first.event_id] = first.payload  # enqueued earlier, crash before acknowledge
    report = await relay(feed, queue, Clock()).run_once()
    assert report.delivered == 1 and feed.acked == [first.event_id]
    assert len(queue.inputs) == 1


async def test_acknowledge_failure_redelivers_as_duplicate_on_the_next_scan() -> None:
    first = event(1)
    feed, queue = Feed([first]), Queue()
    feed.fail_ack = True
    r = relay(feed, queue, Clock())
    with pytest.raises(RelayTransientError):
        await r.run_once()
    feed.fail_ack = False
    assert (await r.run_once()).delivered == 1
    assert feed.acked == [first.event_id] and len(queue.inputs) == 1


async def test_pages_past_failing_events_with_the_cursor() -> None:
    poisons = [event(n) for n in range(1, 4)]
    good = event(4)
    feed, queue = Feed([*poisons, good]), Queue()
    queue.unbound.update(p.payload.case_id for p in poisons)
    report = await relay(feed, queue, Clock(), page_size=2).run_once()
    assert report.delivered == 1 and feed.acked == [good.event_id]
    assert feed.pages[0] is None and feed.pages[1] == 2


async def test_each_scan_restarts_from_the_beginning() -> None:
    feed, queue = Feed([event(5)]), Queue()
    r = relay(feed, queue, Clock())
    await r.run_once()
    late = event(3)  # a lower sequence that committed later
    feed.events.append(late)
    await r.run_once()
    assert late.event_id in feed.acked
    assert feed.pages.count(None) == 2


async def test_run_forever_stops_gracefully_and_backs_off_on_transient_failure() -> None:
    feed, queue = Feed([event(1)]), Queue()
    queue.fail_transient = True
    r = relay(feed, queue, Clock())
    r.start(idle_seconds=0.01)
    await asyncio.sleep(0.05)
    assert r.running and r.status()["transientFailures"] >= 1
    await r.stop(timeout=1)
    assert not r.running and feed.acked == []
    await r.stop()  # idempotent
