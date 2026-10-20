"""Business outbox -> AI durable input queue relay (decision D1, full-system integration M3).

Delivery stage only. The relay moves `representative.answered` events from the Business feed
into the AI's own durable input queue; the turn worker then processes those inputs with its
own, independent retry/dead/reconciliation lifecycle (`fail_input`). The two stages never
share a transaction or a database schema.

Guarantees:
- **Acknowledge only after durable acceptance.** An event is acknowledged only after
  `enqueue_representative_answered` committed (or reported the identical input as a
  duplicate). A crash between enqueue and acknowledge redelivers the event; the queue's
  dedup by event ID (`uuid5(event_id)` input IDs) makes that harmless.
- **One bad event never blocks the others.** A permanent failure for one event (no
  conversation bound to its case, or the same event ID with a different payload) is never
  acknowledged and never discarded: it stays in the Business outbox (durable, recoverable),
  is retried with a per-event backoff (30 s, 2 min, 10 min, then hourly), and the scan pages
  past it with `after_sequence` (E1) so later events are still delivered. After 10 attempts
  the event is quarantined: still retried hourly, and logged at ERROR on every attempt.
- **Transient failures** (database or connection errors from the feed or the queue) stop the
  current scan without acknowledging what was not accepted and back off globally (1 s, 5 s,
  30 s, 2 min, then every 5 min).
- **Restarts lose nothing.** Backoff counters live in memory only; after a restart every
  unacknowledged event is simply tried again from the outbox.

Logs carry event IDs, sequences, attempt counts and error type names only - never payloads.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID

from app.ai.application.errors import ConversationAccessError
from app.ai.application.ports.input_queue import DurableInputQueue, InputPayloadMismatchError
from app.contracts.business_events import BusinessEvent, BusinessEventFeed

logger = logging.getLogger("app.ai.relay")

EVENT_BACKOFF_SECONDS = (30.0, 120.0, 600.0, 3600.0)
QUARANTINE_AFTER = 10
QUARANTINE_RETRY_SECONDS = 3600.0
TRANSIENT_BACKOFF_SECONDS = (1.0, 5.0, 30.0, 120.0, 300.0)
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 10
DEFAULT_IDLE_SECONDS = 2.0
DEFAULT_STOP_TIMEOUT_SECONDS = 10.0

PERMANENT_ERRORS: tuple[type[Exception], ...] = (
    ConversationAccessError,
    InputPayloadMismatchError,
)


class RelayTransientError(Exception):
    """The scan stopped on a transient failure; unaccepted events stay unacknowledged."""


@dataclass
class _EventFailure:
    sequence: int
    attempts: int
    error: str
    retry_at: float

    @property
    def quarantined(self) -> bool:
        return self.attempts >= QUARANTINE_AFTER


@dataclass(frozen=True)
class RelayReport:
    delivered: int = 0
    deferred: int = 0
    """Events skipped in this scan because they are backing off after a permanent failure."""
    failed: int = 0
    """Events that failed permanently in this scan (kept unacknowledged)."""


@dataclass
class RelayStatus:
    delivered_total: int = 0
    transient_failures: int = 0
    failing_events: dict[UUID, _EventFailure] = field(default_factory=dict)

    def counters(self) -> dict[str, int]:
        failing = self.failing_events.values()
        return {
            "delivered": self.delivered_total,
            "backingOff": sum(1 for f in failing if not f.quarantined),
            "quarantined": sum(1 for f in failing if f.quarantined),
            "transientFailures": self.transient_failures,
        }


class BusinessEventRelay:
    def __init__(
        self,
        feed: BusinessEventFeed,
        queue: DurableInputQueue,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if page_size < 1 or max_pages < 1:
            raise ValueError("page_size and max_pages must be positive")
        self._feed = feed
        self._queue = queue
        self._page_size = page_size
        self._max_pages = max_pages
        self._clock = clock
        self._status = RelayStatus()
        self._consecutive_transient = 0
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    # --- one scan ----------------------------------------------------------------------------

    async def run_once(self) -> RelayReport:
        """Scan the feed once: deliver what can be accepted, acknowledge it, page past the
        rest. Raises `RelayTransientError` on a transient failure."""
        now = self._clock()
        delivered = deferred = failed = 0
        cursor: int | None = None
        seen: set[UUID] = set()
        complete = False
        for _ in range(self._max_pages):
            events = await self._call(self._feed.pending(self._page_size, after_sequence=cursor))
            if not events:
                complete = True
                break
            accepted: list[UUID] = []
            try:
                for event in events:
                    seen.add(event.event_id)
                    failure = self._status.failing_events.get(event.event_id)
                    if failure is not None and failure.retry_at > now:
                        deferred += 1
                        continue
                    if await self._deliver(event, now):
                        accepted.append(event.event_id)
                    else:
                        failed += 1
            finally:
                # Whatever was durably accepted is acknowledged, even if the scan stops.
                if accepted:
                    await self._call(self._feed.acknowledge(accepted))
                    delivered += len(accepted)
                    self._status.delivered_total += len(accepted)
            cursor = events[-1].sequence
            if len(events) < self._page_size:
                complete = True
                break
        if complete:
            # Events acknowledged elsewhere (another instance) are no longer pending.
            for event_id in set(self._status.failing_events) - seen:
                del self._status.failing_events[event_id]
        return RelayReport(delivered=delivered, deferred=deferred, failed=failed)

    async def _deliver(self, event: BusinessEvent, now: float) -> bool:
        try:
            await self._queue.enqueue_representative_answered(event.payload)
        except PERMANENT_ERRORS as error:
            self._record_failure(event, type(error).__name__, now)
            return False
        except Exception as error:
            logger.warning(
                "relay_enqueue_transient_failure event_id=%s sequence=%s error=%s",
                event.event_id,
                event.sequence,
                type(error).__name__,
            )
            raise RelayTransientError(type(error).__name__) from None
        self._status.failing_events.pop(event.event_id, None)
        return True

    def _record_failure(self, event: BusinessEvent, error: str, now: float) -> None:
        previous = self._status.failing_events.get(event.event_id)
        attempts = 1 if previous is None else previous.attempts + 1
        if attempts >= QUARANTINE_AFTER:
            delay = QUARANTINE_RETRY_SECONDS
        else:
            delay = EVENT_BACKOFF_SECONDS[min(attempts, len(EVENT_BACKOFF_SECONDS)) - 1]
        failure = _EventFailure(event.sequence, attempts, error, now + delay)
        self._status.failing_events[event.event_id] = failure
        log = logger.error if failure.quarantined else logger.warning
        log(
            "relay_event_not_accepted event_id=%s sequence=%s error=%s attempts=%s "
            "quarantined=%s retry_in_s=%s",
            event.event_id,
            event.sequence,
            error,
            attempts,
            failure.quarantined,
            int(delay),
        )

    async def _call[T](self, awaitable: Awaitable[T]) -> T:
        try:
            return await awaitable
        except RelayTransientError:
            raise
        except Exception as error:
            logger.warning("relay_feed_transient_failure error=%s", type(error).__name__)
            raise RelayTransientError(type(error).__name__) from None

    # --- loop and lifecycle ----------------------------------------------------------------------

    def status(self) -> dict[str, int]:
        return self._status.counters()

    def transient_delay(self) -> float:
        index = min(self._consecutive_transient, len(TRANSIENT_BACKOFF_SECONDS)) - 1
        return TRANSIENT_BACKOFF_SECONDS[max(index, 0)]

    async def run_forever(
        self, stop: asyncio.Event, *, idle_seconds: float = DEFAULT_IDLE_SECONDS
    ) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
                self._consecutive_transient = 0
                delay = idle_seconds
            except RelayTransientError:
                self._consecutive_transient += 1
                self._status.transient_failures += 1
                delay = self.transient_delay()
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, *, idle_seconds: float = DEFAULT_IDLE_SECONDS) -> None:
        if self.running:
            raise RuntimeError("the event relay is already running")
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self.run_forever(self._stop, idle_seconds=idle_seconds), name="business-event-relay"
        )
        logger.info("event_relay_started")

    async def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> None:
        """Idempotent. Lets the current scan finish; cancels after `timeout` (an event enqueued
        but not yet acknowledged is then simply redelivered)."""
        task, stop = self._task, self._stop
        if task is None or stop is None:
            return
        stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        finally:
            self._task = None
            self._stop = None
        logger.info("event_relay_stopped")
