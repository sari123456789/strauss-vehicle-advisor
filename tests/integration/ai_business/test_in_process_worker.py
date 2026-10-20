"""A2: the in-process worker (cloud plan DECISION W) end to end over HTTP (marker `db`).

HTTP submit -> durable input -> `InProcessWorker` (real `TurnWorker`, LangGraph runner with the
fenced PostgreSQL checkpointer, thread lock, leases) over the REAL Business facade -> assistant
reply in PostgreSQL -> polling. Only the model is scripted (no network).

Isolation: every test module run gets its own temporary, marked database (created and dropped
through `db_guard`), so no input left by other suites can be claimed and nothing runs against
Neon or the development database.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from app.ai.application.ports.llm import LlmTask, LlmTaskKind
from app.ai.infrastructure.checkpoints.postgres import setup_checkpointer
from app.ai.infrastructure.runtime import AiRuntime, InProcessWorker, build_ai_runtime
from app.ai.workflow.models import Intent
from app.bootstrap import BusinessServices, build_business_services
from app.contracts.business_port import BusinessToolsPort
from app.runtime import run_async
from app.settings import Settings
from tests.fakes.ai.fake_llm import ScriptedLlm, interpretation
from tests.integration.ai_business import db_guard
from tests.integration.ai_business.conftest import RecordingToolsPort, Stack
from tests.integration.ai_business.test_conversation_http import Http, http

LEASE = 3
RAW_CODES = re.compile(r"\b(40[0-9]|500|503)\b|NOT_FOUND|FORBIDDEN|Traceback", re.I)


@pytest.fixture(scope="module")
def worker_db() -> Iterator[str]:
    """A fresh marked `_test` database with both schemas at head and the checkpointer tables."""
    try:
        url = db_guard.create_migration_database(db_guard.configured_url())
    except Exception as error:  # noqa: BLE001 - unreachable server: same policy as integration_db
        from tests.integration.ai_business.conftest import REQUIRE_DB

        if REQUIRE_DB:
            pytest.fail(f"PostgreSQL is unreachable: {type(error).__name__}")
        pytest.skip(f"PostgreSQL is unreachable: {type(error).__name__}")
    try:
        db_guard.upgrade_to_head(url, migration=True)
        run_async(setup_checkpointer(url))
        yield url
    finally:
        db_guard.drop_migration_database(url)


class GatedLlm:
    """Delegates to a scripted model, but holds every call until `gate` is opened."""

    def __init__(self, inner: ScriptedLlm) -> None:
        self.inner = inner
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def generate(self, task: LlmTask[Any]) -> Any:
        self.entered.set()
        await self.gate.wait()
        return await self.inner.generate(task)


class Harness:
    def __init__(self, url: str, business: BusinessServices) -> None:
        self.url = url
        self.business = business
        self.spy = RecordingToolsPort(business.tools)
        self.runtimes: list[AiRuntime] = []

    async def runtime(self, llm: Any, worker_id: str, *, stop_timeout: float = 20.0) -> AiRuntime:
        built = await build_ai_runtime(
            self.url,
            business_tools=cast(BusinessToolsPort, self.spy),
            case_lifecycle=self.business.lifecycle,
            llm=llm,
            worker_id=worker_id,
            lease_seconds=LEASE,
        )
        built.in_process = InProcessWorker(
            built.worker, idle_seconds=0.1, stop_timeout=stop_timeout
        )
        self.runtimes.append(built)
        return built

    def stack(self, runtime: AiRuntime, llm: ScriptedLlm) -> Stack:
        return Stack(
            url=self.url,
            business=self.business,
            spy=self.spy,
            llm=llm,
            runner=runtime.runner,
            conversations=runtime.conversations,
            persistence=runtime.persistence,
        )


@pytest.fixture
async def harness(worker_db: str) -> AsyncIterator[Harness]:
    business = build_business_services(
        Settings(environment="test", database_url=worker_db, migration_database_url=worker_db)
    )
    h = Harness(worker_db, business)
    try:
        yield h
    finally:
        for runtime in h.runtimes:
            await runtime.aclose()
        await business.engine.dispose()


async def poll_reply(
    h: Http, headers: dict[str, str], conversation: str, after: int, *, timeout: float = 30.0
) -> list[dict[str, Any]]:
    """What the Angular client does: GET messages after its cursor until a reply arrives."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page = (
            await h.client.get(
                f"/api/v1/conversations/{conversation}/messages",
                params={"afterSequence": after},
                headers=headers,
            )
        ).json()
        replies = [m for m in page["messages"] if m["role"] == "assistant"]
        if replies:
            return replies
        await asyncio.sleep(0.2)
    raise AssertionError("no assistant reply was polled in time")


async def input_state(h: Http, headers: dict[str, str], conversation: str, input_id: str) -> Any:
    response = await h.client.get(
        f"/api/v1/conversations/{conversation}/inputs/{input_id}", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def wait_status(
    h: Http, headers: dict[str, str], conversation: str, input_id: str, status: str
) -> Any:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = await input_state(h, headers, conversation, input_id)
        if state["status"] == status:
            return state
        await asyncio.sleep(0.1)
    raise AssertionError(f"input never reached {status!r}")


async def test_a_message_is_answered_by_the_in_process_worker_and_polled(
    harness: Harness,
) -> None:
    llm = ScriptedLlm().interpret(interpretation(Intent.SMALLTALK))
    runtime = await harness.runtime(llm, "worker-a")
    async with http(harness.stack(runtime, llm)) as h:
        runtime.in_process.start()
        headers = await h.user()
        conversation = (await h.start(headers))["conversationId"]
        accepted = await h.say(headers, conversation, "שלום", uuid4())
        assert accepted.status_code == 202
        submitted = accepted.json()

        replies = await poll_reply(h, headers, conversation, submitted["sequence"])
        assert all(r["inputId"] == submitted["inputId"] for r in replies)
        assert replies[0]["text"].strip() and not RAW_CODES.search(replies[0]["text"])

        state = await wait_status(h, headers, conversation, submitted["inputId"], "completed")
        assert state["attempts"] == 1
        assert [m["role"] for m in state["messages"]][0] == "user"
        # the turn really ran through LangGraph on the real Business facade, with checkpoints
        assert "get_conversation_context" in harness.spy.names()
        [(checkpoints,)] = db_guard.sql(
            harness.url,
            "SELECT count(*) FROM agent.checkpoints c JOIN agent.conversations v "
            "ON c.thread_id = v.thread_id::text WHERE v.id = %s",
            UUID(conversation),
        )
        assert checkpoints > 0
        await runtime.in_process.stop()
        assert not runtime.in_process.running


async def test_two_workers_and_a_resent_message_never_process_an_input_twice(
    harness: Harness,
) -> None:
    llm = ScriptedLlm().interpret(
        interpretation(Intent.SMALLTALK), interpretation(Intent.SMALLTALK)
    )
    first = await harness.runtime(llm, "worker-a")
    second = await harness.runtime(llm, "worker-b")
    async with http(harness.stack(first, llm)) as h:
        a, b = await h.user(), await h.user()
        conv_a = (await h.start(a))["conversationId"]
        conv_b = (await h.start(b))["conversationId"]
        message_id = uuid4()
        sent_a = (await h.say(a, conv_a, "שלום", message_id)).json()
        resent = (await h.say(a, conv_a, "שלום", message_id)).json()  # client retry
        assert resent["duplicate"] is True and resent["inputId"] == sent_a["inputId"]
        sent_b = (await h.say(b, conv_b, "היי", uuid4())).json()

        first.in_process.start()
        second.in_process.start()
        replies_a = await poll_reply(h, a, conv_a, sent_a["sequence"])
        replies_b = await poll_reply(h, b, conv_b, sent_b["sequence"])
        for conversation, sent, headers in ((conv_a, sent_a, a), (conv_b, sent_b, b)):
            state = await wait_status(h, headers, conversation, sent["inputId"], "completed")
            assert state["attempts"] == 1
        await first.in_process.stop()
        await second.in_process.stop()

        assert len(llm.calls(LlmTaskKind.INTERPRET)) == 2  # one model run per input
        rows = db_guard.sql(
            harness.url,
            "SELECT conversation_id, count(*) FROM agent.inputs "
            "WHERE conversation_id = ANY(%s) GROUP BY 1",
            [UUID(conv_a), UUID(conv_b)],
        )
        assert sorted(int(n) for _, n in rows) == [1, 1]
        everything_a = (
            await h.client.get(f"/api/v1/conversations/{conv_a}/messages", headers=a)
        ).json()["messages"]
        assert [m["messageId"] for m in everything_a if m["role"] == "assistant"] == [
            r["messageId"] for r in replies_a
        ]
        assert replies_b


async def test_shutdown_waits_for_the_running_turn_to_finish(harness: Harness) -> None:
    idle = await harness.runtime(ScriptedLlm(), "worker-idle")
    idle.in_process.start()
    started = time.monotonic()
    await idle.in_process.stop()
    assert not idle.in_process.running and time.monotonic() - started < 2
    await idle.in_process.stop()  # idempotent

    scripted = ScriptedLlm().interpret(interpretation(Intent.SMALLTALK))
    gated = GatedLlm(scripted)
    runtime = await harness.runtime(gated, "worker-graceful", stop_timeout=30)
    async with http(harness.stack(runtime, scripted)) as h:
        headers = await h.user()
        conversation = (await h.start(headers))["conversationId"]
        sent = (await h.say(headers, conversation, "שלום", uuid4())).json()
        runtime.in_process.start()
        await asyncio.wait_for(gated.entered.wait(), timeout=30)
        stopping = asyncio.create_task(runtime.in_process.stop())  # shutdown mid-turn
        await asyncio.sleep(0.3)
        assert not stopping.done()  # it waits for the turn instead of abandoning it
        gated.gate.set()
        await asyncio.wait_for(stopping, timeout=30)
        assert not runtime.in_process.running
        state = await input_state(h, headers, conversation, sent["inputId"])
        assert state["status"] == "completed" and state["attempts"] == 1
        assert [m["role"] for m in state["messages"]][-1] == "assistant"


async def test_a_turn_cancelled_by_a_forced_stop_is_never_run_twice(harness: Harness) -> None:
    """Past `stop_timeout` the attempt is cancelled mid-graph. The next worker takes the
    expired claim over, finds a partial checkpoint lineage and leaves the input in
    `needs_reconciliation` (existing TurnWorker policy): no second model run, no
    half-written reply."""
    gated = GatedLlm(ScriptedLlm())  # never released: the turn hangs inside the model call
    stuck = await harness.runtime(gated, "worker-stuck", stop_timeout=0.5)
    rescue_llm = ScriptedLlm().interpret(interpretation(Intent.SMALLTALK))
    async with http(harness.stack(stuck, rescue_llm)) as h:
        headers = await h.user()
        conversation = (await h.start(headers))["conversationId"]
        sent = (await h.say(headers, conversation, "שלום", uuid4())).json()
        stuck.in_process.start()
        await asyncio.wait_for(gated.entered.wait(), timeout=30)
        await stuck.in_process.stop()  # times out and cancels the attempt
        assert not stuck.in_process.running
        state = await input_state(h, headers, conversation, sent["inputId"])
        assert state["status"] == "processing"
        assert [m["role"] for m in state["messages"]] == ["user"]

        rescuer = await harness.runtime(rescue_llm, "worker-rescue")
        rescuer.in_process.start()  # takes the claim over once the lease expires
        final = await wait_status(h, headers, conversation, sent["inputId"], "needs_reconciliation")
        await rescuer.in_process.stop()
        assert final["attempts"] == 2
        assert [m["role"] for m in final["messages"]] == ["user"]
        assert rescue_llm.calls(LlmTaskKind.INTERPRET) == []
