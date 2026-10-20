"""Durable turn execution: claims queued inputs and runs them on their conversation's thread.

Independent of any web framework: the same worker runs in-process for a single-instance demo
or as a separate process (`run_forever`). One input at a time per worker; a conversation's
inputs run strictly in order (the claim query only offers the head of each queue).

Per input:
1. Claim it with a lease (or take over an expired claim while holding the thread's lock).
2. Hold the thread lock and renew the lease in the background for the whole attempt.
3. Verify checkpoint lineage. A previous attempt that already finished the graph is completed
   from its staged response without running the graph again; an unverifiable lineage is left in
   `needs_reconciliation`.
4. Run the graph with the fence and the response stager, then complete the input - which
   publishes the staged response atomically with `completed`. Nothing is marked completed
   before its output is durable.

Failures: a lost claim abandons the attempt without writing; any other exception records a
failed attempt and the store decides `retry` (bounded backoff), `dead` or reconciliation.
Expected tool and model failures never get here: the graph turns them into visible answers.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.ai.application.ports import conversation_store as ps
from app.ai.application.ports.checkpoints import CheckpointFence, CheckpointLineageReader
from app.ai.application.ports.graph_runner import (
    GraphRunner,
    HistoryMessage,
    InputKind,
    TurnInput,
)
from app.ai.application.ports.response_staging import ResponseStager
from app.ai.application.ports.thread_lock import ThreadLock
from app.contracts.business_port import CANONICAL_TOOL_NAMES
from app.contracts.tool_context import ActorRef, Origin, Role, ToolContext

logger = logging.getLogger("app.ai.worker")

DEFAULT_LEASE_SECONDS = 120
HISTORY_MESSAGES = 6
MESSAGE_PAGE = 200
REPLAY_SAFE_TOOLS = frozenset(CANONICAL_TOOL_NAMES)
"""Every Business tool is idempotent under the workflow's deterministic operation IDs, so a
retried attempt may repeat any tool call. (The side-effect journal is not written yet.)"""


class WorkResult(StrEnum):
    COMPLETED = "completed"
    RECOVERED = "recovered"
    """Completed from a previous attempt's checkpoint and staged response (no new run)."""
    RETRY = "retry"
    DEAD = "dead"
    NEEDS_RECONCILIATION = "needs_reconciliation"
    ABANDONED = "abandoned"
    """The claim was lost (taken over or expired); this worker wrote nothing more."""
    THREAD_BUSY = "thread_busy"


@dataclass(frozen=True)
class WorkReport:
    input_id: UUID
    result: WorkResult
    attempt: int


StagerFactory = Callable[[ps.InputClaim], ResponseStager]


class TurnWorker:
    def __init__(
        self,
        transactions: ps.ConversationTransactions,
        runner: GraphRunner,
        lineage: CheckpointLineageReader,
        stager_factory: StagerFactory,
        thread_lock: ThreadLock,
        *,
        worker_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_attempts: int = ps.MAX_ATTEMPTS,
    ) -> None:
        if lease_seconds < 3:
            raise ValueError("lease_seconds must allow a heartbeat")
        self._tx = transactions
        self._runner = runner
        self._lineage = lineage
        self._stager_factory = stager_factory
        self._lock = thread_lock
        self._worker_id = worker_id
        self._lease = lease_seconds
        self._max_attempts = max_attempts

    # --- loop ----------------------------------------------------------------------------

    async def run_forever(self, stop: asyncio.Event, *, idle_seconds: float = 1.0) -> None:
        """Process inputs until `stop` is set; sleeps only when there is nothing to do."""
        while not stop.is_set():
            try:
                report = await self.run_once()
            except Exception as error:  # infrastructure outage: keep the loop alive
                logger.warning("worker_cycle_failed error=%s", type(error).__name__)
                report = None
            if report is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
                except TimeoutError:
                    pass

    async def run_once(self) -> WorkReport | None:
        """Recover one expired claim, or else process the next due input. None: idle."""
        recovered = await self._take_over_one()
        if recovered is not None:
            return recovered
        async with self._tx.transaction() as store:
            claim = await store.claim_next_input(self._worker_id, lease_seconds=self._lease)
        if claim is None:
            return None
        async with self._lock.hold(str(claim.thread_id)) as held:
            if not held:  # another holder still runs this thread: try again later
                return await self._fail(claim, "thread_busy", WorkResult.THREAD_BUSY)
            return await self._attempt(claim)

    async def _take_over_one(self) -> WorkReport | None:
        async with self._tx.transaction() as store:
            expired = await store.list_expired_claims(limit=5)
        for candidate in expired:
            async with self._lock.hold(str(candidate.thread_id)) as held:
                if not held:
                    continue  # the previous holder may still be alive: never take over
                async with self._tx.transaction() as store:
                    claim = await store.take_over_expired_claim(
                        candidate, self._worker_id, lease_seconds=self._lease
                    )
                if claim is None:
                    continue
                logger.info("worker_took_over input=%s", claim.input.input_id)
                return await self._attempt(claim)
        return None

    # --- one attempt ---------------------------------------------------------------------

    async def _attempt(self, claim: ps.InputClaim) -> WorkReport:
        heartbeat = asyncio.create_task(self._heartbeat(claim))
        try:
            return await self._execute(claim)
        except ps.StaleClaimError:
            return self._report(claim, WorkResult.ABANDONED)
        except Exception as error:
            logger.warning(
                "turn_failed input=%s attempt=%s error=%s",
                claim.input.input_id,
                claim.attempt,
                type(error).__name__,
            )
            return await self._fail(claim, type(error).__name__, None)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _execute(self, claim: ps.InputClaim) -> WorkReport:
        thread_id = str(claim.thread_id)
        input_id = str(claim.input.input_id)
        observation = await self._lineage.observe(thread_id, input_id)
        async with self._tx.transaction() as store:
            lineage = await store.bind_lineage(claim, observation)
        if lineage is ps.LineageResult.MISMATCH:
            return self._report(claim, WorkResult.NEEDS_RECONCILIATION)
        if lineage is ps.LineageResult.ALREADY_EXECUTED:
            # A previous attempt finished the graph but not the input: publish its response.
            refs = await self._lineage.latest_message_refs(thread_id, input_id)
            if refs is None or observation.latest_checkpoint_id is None:
                async with self._tx.transaction() as store:
                    await store.mark_needs_reconciliation(claim, reason="message_refs_missing")
                return self._report(claim, WorkResult.NEEDS_RECONCILIATION)
            async with self._tx.transaction() as store:
                status = await store.complete_input(
                    claim,
                    checkpoint_id=observation.latest_checkpoint_id,
                    parent_checkpoint_id=observation.latest_parent_checkpoint_id,
                    message_refs=refs,
                )
            return self._report(claim, _completion(status, WorkResult.RECOVERED))

        context, turn = await self._prepare(claim)
        outcome = await self._runner.run_turn(
            thread_id=thread_id,
            tool_context=context,
            turn=turn,
            fence=self._fence(claim),
            stager=self._stager_factory(claim),
        )
        if outcome.checkpoint_id is None:
            async with self._tx.transaction() as store:
                await store.mark_needs_reconciliation(claim, reason="checkpoint_missing")
            return self._report(claim, WorkResult.NEEDS_RECONCILIATION)
        async with self._tx.transaction() as store:
            status = await store.complete_input(
                claim,
                checkpoint_id=outcome.checkpoint_id,
                parent_checkpoint_id=outcome.parent_checkpoint_id,
                message_refs=outcome.message_refs,
            )
        return self._report(claim, _completion(status, WorkResult.COMPLETED))

    async def _prepare(self, claim: ps.InputClaim) -> tuple[ToolContext, TurnInput]:
        """The server-built context (the conversation's owner) and the turn with its history."""
        record = claim.input
        messages: list[ps.MessageRecord] = []
        async with self._tx.transaction() as store:
            conversation = await store.get_conversation(record.conversation_id)
            while True:  # page up to this input's own message
                after = messages[-1].sequence if messages else 0
                page = await store.list_messages(
                    record.conversation_id, after_sequence=after, limit=MESSAGE_PAGE
                )
                messages.extend(page)
                if len(page) < MESSAGE_PAGE or any(m.input_id == record.input_id for m in page):
                    break
        if conversation is None or conversation.case_id is None:
            raise ps.ConversationNotFoundError(str(record.conversation_id))
        context = ToolContext(
            actor=ActorRef(
                subject_id=claim.owner_subject_id,
                roles=frozenset({Role.EMPLOYEE}),
                origin=Origin.WORKER,
            ),
            conversation_id=conversation.conversation_id,
            case_id=conversation.case_id,
            trace_id=f"in-{record.input_id.hex[:16]}-a{claim.attempt}",
        )
        return context, _turn(record, _history(messages, record.input_id))

    async def _fail(
        self, claim: ps.InputClaim, error_code: str, override: WorkResult | None
    ) -> WorkReport:
        try:
            async with self._tx.transaction() as store:
                decision = await store.fail_input(
                    claim,
                    error_code=error_code,
                    replay_safe_tools=REPLAY_SAFE_TOOLS,
                    max_attempts=self._max_attempts,
                )
        except ps.StaleClaimError:
            return self._report(claim, WorkResult.ABANDONED)
        if override is not None and decision.status == ps.RETRY:
            return self._report(claim, override)
        return self._report(claim, WorkResult(decision.status))

    def _fence(self, claim: ps.InputClaim) -> CheckpointFence:
        async def fence() -> None:
            async with self._tx.transaction() as store:
                if not await store.is_claim_live(claim):
                    raise ps.StaleClaimError(str(claim.input.input_id))

        return fence

    async def _heartbeat(self, claim: ps.InputClaim) -> None:
        interval = max(1.0, self._lease / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                async with self._tx.transaction() as store:
                    await store.renew_lease(claim, lease_seconds=self._lease)
            except ps.StaleClaimError:
                return  # the fence stops any further write of this attempt
            except Exception as error:
                logger.warning("lease_renewal_failed error=%s", type(error).__name__)

    @staticmethod
    def _report(claim: ps.InputClaim, result: WorkResult) -> WorkReport:
        return WorkReport(claim.input.input_id, result, claim.attempt)


def _completion(status: str, success: WorkResult) -> WorkResult:
    return success if status == ps.COMPLETED else WorkResult.NEEDS_RECONCILIATION


def _history(messages: list[ps.MessageRecord], input_id: UUID) -> tuple[HistoryMessage, ...]:
    """Visible messages before this input's own message, newest last (bounded)."""
    earlier: list[HistoryMessage] = []
    for message in messages:
        if message.input_id == input_id and message.role == ps.USER:
            break
        if message.role == "assistant":
            earlier.append(HistoryMessage("assistant", message.text))
        elif message.role == "user":
            earlier.append(HistoryMessage("user", message.text))
    return tuple(earlier[-HISTORY_MESSAGES:])


def _turn(record: ps.InputRecord, history: tuple[HistoryMessage, ...]) -> TurnInput:
    payload: dict[str, Any] = record.payload
    if record.source == ps.REPRESENTATIVE_ANSWERED:
        return TurnInput(
            record.input_id,
            InputKind.REPRESENTATIVE_ANSWERED,
            request_id=UUID(str(payload["requestId"])),
            recent_messages=history,
        )
    return TurnInput(
        record.input_id,
        InputKind.USER_MESSAGE,
        text=str(payload.get("text") or ""),
        recent_messages=history,
    )
