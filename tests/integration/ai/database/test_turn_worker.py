"""TurnWorker over real PostgreSQL (`strauss_ai_test`) with Business fakes and a scripted model.

Covers normal processing, duplicate inputs, failed tool calls, bounded retries, dead inputs,
expired leases (with and without a live holder) and recovery after a worker crash.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.ai.application.conversation_service import ConversationService, ConversationView
from app.ai.application.ports.conversation_store import COMPLETED, DEAD, RETRY
from app.ai.application.ports.graph_runner import GraphRunner, TurnOutcome
from app.ai.application.turn_worker import WorkResult
from app.ai.infrastructure.persistence.thread_lock import PostgresThreadLock
from app.ai.infrastructure.runtime import AiRuntime, build_ai_runtime
from app.ai.workflow.models import Intent
from app.contracts.tool_context import ActorRef, Origin, Role, ToolContext
from tests.fakes.ai.fake_case_lifecycle import FakeCaseLifecycle
from tests.fakes.ai.fake_llm import ScriptedLlm, interpretation
from tests.fakes.fake_business_port import FakeBusinessPort
from tests.integration.ai.database.conftest import sql

MONTH = "2026-10"
LEASE = 3


class Crash(BaseException):
    """Simulated process death: not an `Exception`, so the worker does not record it."""


class ToolsFailing:
    """A Business port whose catalogue tool raises (an unexpected adapter failure)."""

    def __init__(self) -> None:
        self._fake = FakeBusinessPort("success")

    async def list_eligible_options(self, ctx: ToolContext, request: Any) -> Any:
        raise ConnectionError("business adapter down")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fake, name)


class FlakyRunner:
    """Wraps the real runner; raises before running for the first `failures` calls, or
    raises `Crash` after a complete run (graph and staging done, input not completed)."""

    def __init__(self, inner: GraphRunner, *, failures: int = 0, crash_after: bool = False):
        self._inner = inner
        self.failures = failures
        self.crash_after = crash_after
        self.runs = 0

    async def run_turn(self, **kwargs: Any) -> TurnOutcome:
        self.runs += 1
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("transient failure")
        outcome = await self._inner.run_turn(**kwargs)
        if self.crash_after:
            self.crash_after = False
            raise Crash
        return outcome


def _employee() -> ActorRef:
    return ActorRef(subject_id=uuid4(), roles=frozenset({Role.EMPLOYEE}), origin=Origin.HTTP)


@pytest.fixture(autouse=True)
def _park_other_inputs(migrated_ai_db: str) -> None:
    """Inputs left by other tests in this module must not be claimed by these workers."""
    sql(
        migrated_ai_db,
        "UPDATE agent.inputs SET status = 'dead', completed_at = now() "
        "WHERE status IN ('pending', 'retry', 'processing')",
    )


@pytest.fixture
async def llm() -> ScriptedLlm:
    return ScriptedLlm()


@pytest.fixture
async def runtime(migrated_ai_db: str, llm: ScriptedLlm) -> AsyncIterator[AiRuntime]:
    built = await build_ai_runtime(
        migrated_ai_db,
        business_tools=FakeBusinessPort("success"),
        case_lifecycle=FakeCaseLifecycle(),
        llm=llm,
        worker_id="worker-a",
        lease_seconds=LEASE,
        setup_checkpointer_tables=True,
    )
    try:
        yield built
    finally:
        await built.aclose()


async def _conversation(service: ConversationService) -> tuple[ActorRef, ConversationView]:
    actor = _employee()
    view = await service.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    return actor, view


def _status(url: str, input_id: UUID) -> tuple[str, int, str | None]:
    [(status, attempts, error)] = sql(
        url, "SELECT status, attempts, last_error_code FROM agent.inputs WHERE id = %s", input_id
    )
    return str(status), int(str(attempts)), None if error is None else str(error)


def _make_due(url: str, input_id: UUID) -> None:
    sql(url, "UPDATE agent.inputs SET next_attempt_at = now() WHERE id = %s", input_id)


async def _expire(url: str, input_id: UUID) -> None:
    sql(
        url,
        "UPDATE agent.inputs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
        input_id,
    )


# --- Normal processing and duplicates ---------------------------------------------------------


async def test_a_queued_message_is_processed_and_published(
    runtime: AiRuntime, llm: ScriptedLlm, migrated_ai_db: str
) -> None:
    actor, view = await _conversation(runtime.conversations)
    llm.interpret(interpretation(Intent.LIST_OPTIONS))
    sent = await runtime.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=uuid4(), text="אילו רכבים מתאימים לי?"
    )
    report = await runtime.worker.run_once()
    assert report is not None and report.input_id == sent.input_id
    assert report.result is WorkResult.COMPLETED and report.attempt == 1
    assert _status(migrated_ai_db, sent.input_id) == (COMPLETED, 1, None)
    history = await runtime.conversations.history(actor, view.conversation_id)
    assert [m.role for m in history][:2] == ["user", "assistant"]
    assert history[1].kind == "options" and "ATTO 3" in history[1].text
    assert all(m.input_id == sent.input_id for m in history)
    assert await runtime.worker.run_once() is None  # nothing left


async def test_a_duplicate_message_is_queued_and_processed_once(
    runtime: AiRuntime, llm: ScriptedLlm, migrated_ai_db: str
) -> None:
    actor, view = await _conversation(runtime.conversations)
    llm.interpret(interpretation(Intent.LIST_OPTIONS))
    client_id = uuid4()
    first = await runtime.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=client_id, text="תראה רכבים"
    )
    again = await runtime.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=client_id, text="תראה רכבים"
    )
    assert again.duplicate and again.input_id == first.input_id
    assert (await runtime.worker.run_once()) is not None
    assert await runtime.worker.run_once() is None
    assert len(llm.tasks) == 1
    history = await runtime.conversations.history(actor, view.conversation_id)
    assert [m.role for m in history].count("user") == 1


async def test_inputs_of_one_conversation_run_in_order(
    runtime: AiRuntime, llm: ScriptedLlm
) -> None:
    actor, view = await _conversation(runtime.conversations)
    llm.interpret(interpretation(Intent.LIST_OPTIONS), interpretation(Intent.SMALLTALK))
    first = await runtime.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=uuid4(), text="תראה רכבים"
    )
    second = await runtime.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=uuid4(), text="תודה"
    )
    reports = [await runtime.worker.run_once(), await runtime.worker.run_once()]
    assert [r.input_id for r in reports if r] == [first.input_id, second.input_id]
    # The second turn received the first exchange as history.
    assert len(llm.tasks[1].messages) > 1


# --- Failures ---------------------------------------------------------------------------------


async def test_a_failed_tool_call_completes_with_a_safe_answer(
    migrated_ai_db: str, llm: ScriptedLlm
) -> None:
    runtime = await build_ai_runtime(
        migrated_ai_db,
        business_tools=ToolsFailing(),
        case_lifecycle=FakeCaseLifecycle(),
        llm=llm,
        lease_seconds=LEASE,
    )
    try:
        actor, view = await _conversation(runtime.conversations)
        llm.interpret(interpretation(Intent.LIST_OPTIONS))
        sent = await runtime.conversations.submit_user_message(
            actor, view.conversation_id, client_message_id=uuid4(), text="תראה רכבים"
        )
        report = await runtime.worker.run_once()
        assert report is not None and report.result is WorkResult.COMPLETED
        assert _status(migrated_ai_db, sent.input_id)[0] == COMPLETED  # not retried
        history = await runtime.conversations.history(actor, view.conversation_id)
        answer = history[-1]
        assert answer.role == "assistant" and answer.kind == "error"
        assert "ConnectionError" not in answer.text and "INFRA" not in answer.text
    finally:
        await runtime.aclose()


async def test_an_unexpected_failure_is_retried_with_backoff_then_completes(
    runtime: AiRuntime, llm: ScriptedLlm, migrated_ai_db: str
) -> None:
    flaky = FlakyRunner(runtime.runner, failures=1)
    runtime.worker._runner = flaky  # the same worker with a failing runner
    actor, view = await _conversation(runtime.conversations)
    llm.interpret(interpretation(Intent.SMALLTALK))
    sent = await runtime.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=uuid4(), text="שלום"
    )
    first = await runtime.worker.run_once()
    assert first is not None and first.result is WorkResult.RETRY
    assert _status(migrated_ai_db, sent.input_id) == (RETRY, 1, "RuntimeError")
    assert await runtime.worker.run_once() is None  # not due yet (backoff)
    _make_due(migrated_ai_db, sent.input_id)
    second = await runtime.worker.run_once()
    assert second is not None and second.result is WorkResult.COMPLETED and second.attempt == 2
    assert _status(migrated_ai_db, sent.input_id)[0] == COMPLETED


async def test_attempts_are_bounded_and_the_input_becomes_dead(
    migrated_ai_db: str, llm: ScriptedLlm
) -> None:
    runtime = await build_ai_runtime(
        migrated_ai_db,
        business_tools=FakeBusinessPort("success"),
        case_lifecycle=FakeCaseLifecycle(),
        llm=llm,
        lease_seconds=LEASE,
    )
    try:
        runtime.worker._runner = FlakyRunner(runtime.runner, failures=99)
        runtime.worker._max_attempts = 2
        actor, view = await _conversation(runtime.conversations)
        sent = await runtime.conversations.submit_user_message(
            actor, view.conversation_id, client_message_id=uuid4(), text="שלום"
        )
        assert (await runtime.worker.run_once()).result is WorkResult.RETRY  # type: ignore[union-attr]
        _make_due(migrated_ai_db, sent.input_id)
        assert (await runtime.worker.run_once()).result is WorkResult.DEAD  # type: ignore[union-attr]
        assert _status(migrated_ai_db, sent.input_id)[:2] == (DEAD, 2)
        assert await runtime.worker.run_once() is None
    finally:
        await runtime.aclose()


# --- Expired leases and restart ---------------------------------------------------------------


async def test_an_expired_lease_of_a_dead_holder_is_taken_over(
    runtime: AiRuntime, llm: ScriptedLlm, migrated_ai_db: str
) -> None:
    actor, view = await _conversation(runtime.conversations)
    llm.interpret(interpretation(Intent.SMALLTALK))
    sent = await runtime.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=uuid4(), text="שלום"
    )
    async with runtime.persistence.transaction() as store:  # a worker that then disappears
        stale = await store.claim_next_input("worker-gone", lease_seconds=LEASE)
    assert stale is not None and stale.input.input_id == sent.input_id
    await _expire(migrated_ai_db, sent.input_id)
    report = await runtime.worker.run_once()
    assert report is not None and report.result is WorkResult.COMPLETED and report.attempt == 2
    assert _status(migrated_ai_db, sent.input_id)[0] == COMPLETED
    # The old holder's fencing token no longer writes anything.
    async with runtime.persistence.transaction() as store:
        assert not await store.is_claim_live(stale)


async def test_an_expired_lease_is_not_taken_over_while_its_holder_holds_the_thread(
    runtime: AiRuntime, llm: ScriptedLlm, migrated_ai_db: str
) -> None:
    actor, view = await _conversation(runtime.conversations)
    sent = await runtime.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=uuid4(), text="שלום"
    )
    async with runtime.persistence.transaction() as store:
        stale = await store.claim_next_input("worker-paused", lease_seconds=LEASE)
    assert stale is not None
    await _expire(migrated_ai_db, sent.input_id)
    async with PostgresThreadLock(migrated_ai_db).hold(str(view.thread_id)) as held:
        assert held  # the paused holder still owns the thread
        assert await runtime.worker.run_once() is None
        assert _status(migrated_ai_db, sent.input_id)[:2] == ("processing", 1)
    llm.interpret(interpretation(Intent.SMALLTALK))
    report = await runtime.worker.run_once()  # holder gone: now it is taken over
    assert report is not None and report.result is WorkResult.COMPLETED


async def test_a_crash_after_the_graph_is_recovered_without_running_it_again(
    runtime: AiRuntime, llm: ScriptedLlm, migrated_ai_db: str
) -> None:
    flaky = FlakyRunner(runtime.runner, crash_after=True)
    runtime.worker._runner = flaky
    actor, view = await _conversation(runtime.conversations)
    llm.interpret(interpretation(Intent.LIST_OPTIONS))
    sent = await runtime.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=uuid4(), text="תראה רכבים"
    )
    with pytest.raises(Crash):
        await runtime.worker.run_once()  # graph done and staged; the process "dies"
    assert _status(migrated_ai_db, sent.input_id)[0] == "processing"
    history = await runtime.conversations.history(actor, view.conversation_id)
    assert [m.role for m in history] == ["user"]  # nothing published before completion
    await _expire(migrated_ai_db, sent.input_id)

    restarted = await build_ai_runtime(  # a new worker process
        migrated_ai_db,
        business_tools=FakeBusinessPort("success"),
        case_lifecycle=FakeCaseLifecycle(),
        llm=llm,
        worker_id="worker-b",
        lease_seconds=LEASE,
    )
    try:
        report = await restarted.worker.run_once()
    finally:
        await restarted.aclose()
    assert report is not None and report.result is WorkResult.RECOVERED
    assert flaky.runs == 1 and len(llm.tasks) == 1  # the graph and the model ran once
    assert _status(migrated_ai_db, sent.input_id)[0] == COMPLETED
    history = await runtime.conversations.history(actor, view.conversation_id)
    assert [m.role for m in history] == ["user", "assistant"]
    assert history[1].kind == "options"


async def test_run_forever_processes_until_stopped(
    runtime: AiRuntime, llm: ScriptedLlm, migrated_ai_db: str
) -> None:
    actor, view = await _conversation(runtime.conversations)
    llm.interpret(interpretation(Intent.SMALLTALK))
    sent = await runtime.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=uuid4(), text="שלום"
    )
    stop = asyncio.Event()
    task = asyncio.create_task(runtime.worker.run_forever(stop, idle_seconds=0.05))
    for _ in range(100):
        if _status(migrated_ai_db, sent.input_id)[0] == COMPLETED:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert _status(migrated_ai_db, sent.input_id)[0] == COMPLETED
