"""M3: representative answer -> Business outbox -> relay -> AI input -> resumed conversation.

Runs over the real application (`create_app` lifespan, in-process worker and relay) on a
temporary, seeded PostgreSQL database; only the model is scripted. Also covers the E1 feed
cursor and crash recovery between enqueue and acknowledge on real tables.
"""

import asyncio
import time
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.ai.application.event_relay import BusinessEventRelay
from app.ai.workflow.models import Intent, OptionReference
from app.bootstrap import build_event_relay
from tests.fakes.ai.fake_llm import ScriptedLlm, interpretation
from tests.integration.ai_business.app_support import (
    application,
    application_database,
    case_version,
    login,
    running,
    start_conversation,
)
from tests.integration.ai_business.db_guard import sql

ATTO_3 = "vehicle:CAR-10"
REQUESTS = "/api/v1/representative-requests"


@pytest.fixture(scope="module")
def app_db() -> Iterator[str]:
    with application_database() as url:
        yield url


async def _ask(client: httpx.AsyncClient, headers: dict[str, str], case_id: str) -> str:
    response = await client.post(
        f"/api/v1/cases/{case_id}/representative-requests",
        json={
            "fieldCode": "alternative.participation",
            "optionKey": ATTO_3,
            "questionText": "מה דמי ההשתתפות לרכב ATTO 3?",
            "idempotencyKey": str(uuid4()),
            "expectedCaseVersion": await case_version(client, headers, case_id),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["requestId"])


async def _answer(client: httpx.AsyncClient, request_id: str, value: str = "350") -> None:
    rep = await login(client, "rep01")
    view = (await client.get(f"{REQUESTS}/{request_id}", headers=rep)).json()["data"]
    claimed = await client.post(
        f"{REQUESTS}/{request_id}/claim",
        json={"operationId": str(uuid4()), "expectedRequestVersion": view["requestVersion"]},
        headers=rep,
    )
    assert claimed.status_code == 200, claimed.text  # answering requires the claim
    body = {
        "operationId": str(uuid4()),
        "expectedRequestVersion": view["requestVersion"],
        "outcome": "answered",
        "value": value,
        "unit": "ILS",
    }
    response = await client.post(f"{REQUESTS}/{request_id}/answers", json=body, headers=rep)
    assert response.status_code == 200, response.text


async def _messages(
    client: httpx.AsyncClient, headers: dict[str, str], conversation: str
) -> list[dict[str, Any]]:
    page = await client.get(f"/api/v1/conversations/{conversation}/messages", headers=headers)
    assert page.status_code == 200, page.text
    messages: list[dict[str, Any]] = page.json()["messages"]
    return messages


async def _wait_for(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    conversation: str,
    kind: str,
    *,
    timeout: float = 45.0,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        messages = await _messages(client, headers, conversation)
        if any(m["kind"] == kind for m in messages):
            return messages
        await asyncio.sleep(0.2)
    seen = [(m["role"], m["kind"], m["payload"]) for m in messages]
    raise AssertionError(f"no {kind!r} message arrived; got {seen}")


def _outbox(url: str, request_id: str) -> list[tuple[Any, ...]]:
    return sql(
        url,
        "SELECT id, acknowledged_at IS NOT NULL FROM business.integration_outbox "
        "WHERE payload->>'requestId' = %s",
        request_id,
    )


def _answered_inputs(url: str, event_id: UUID) -> list[tuple[Any, ...]]:
    return sql(
        url,
        "SELECT status FROM agent.inputs WHERE source = 'representative_answered' "
        "AND external_id = %s",
        str(event_id),
    )


async def test_answer_resumes_the_conversation_and_recalculates_with_the_answer(
    app_db: str,
) -> None:
    llm = ScriptedLlm().interpret(
        interpretation(
            Intent.CALCULATE_OPTIONS,
            option_refs=[OptionReference(kind="catalogue_id", value="CAR-10")],
        )
    )
    app = application(app_db, llm=llm, worker_in_process=True)
    async with running(app) as client:
        employee = await login(client, "e001")
        conversation = await start_conversation(client, employee)
        conversation_id, case_id = conversation["conversationId"], conversation["caseId"]
        request_id = await _ask(client, employee, case_id)

        sent = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"messageId": str(uuid4()), "text": "כמה אקבל נטו עם ATTO 3?"},
            headers=employee,
        )
        assert sent.status_code == 202, sent.text
        # The only requested option waits for the representative; the conversation goes on.
        first = await _wait_for(client, employee, conversation_id, "representative_waiting")
        assert not [m for m in first if m["kind"] in ("comparison", "representative_answer")]

        await _answer(client, request_id, "350")
        messages = await _wait_for(client, employee, conversation_id, "representative_answer")
        [(event_id, acknowledged)] = _outbox(app_db, request_id)
        assert acknowledged is True
        assert [status for (status,) in _answered_inputs(app_db, event_id)] in (
            ["completed"],
            ["processing"],
            ["pending"],
        )
        answer_at = next(m["sequence"] for m in messages if m["kind"] == "representative_answer")
        deadline = time.monotonic() + 30
        recalculated: list[dict[str, Any]] = []
        while time.monotonic() < deadline and not recalculated:
            messages = await _messages(client, employee, conversation_id)
            recalculated = [
                m for m in messages if m["kind"] == "comparison" and m["sequence"] > answer_at
            ]
            await asyncio.sleep(0.2)
        assert recalculated, "the answered option is recalculated in the same conversation"
        run_id = recalculated[-1]["payload"]["runId"]
        details = await client.get(
            f"/api/v1/cases/{case_id}/comparisons/{run_id}", headers=employee
        )
        assert details.status_code == 200, details.text
        [atto] = details.json()["data"]["result"]["alternatives"]
        assert atto["optionKey"] == ATTO_3 and atto["status"] == "calculated"
        assert atto["vehicle"]["participationSource"] == "representative"
        assert _answered_inputs(app_db, event_id) == [("completed",)]


async def test_feed_cursor_pages_past_earlier_events(app_db: str) -> None:
    app = application(app_db)  # no worker or relay: events stay pending
    async with running(app) as client:
        employee = await login(client, "e002")
        requests = []
        for _ in range(2):
            case_id = (await start_conversation(client, employee))["caseId"]
            requests.append(await _ask(client, employee, case_id))
        for request_id in requests:
            await _answer(client, request_id)
        feed = app.state.business_services.event_feed
        pending = await feed.pending(500)
        mine = [e for e in pending if str(e.payload.request_id) in requests]
        assert len(mine) == 2 and mine[0].sequence < mine[1].sequence
        after_first = await feed.pending(500, after_sequence=mine[0].sequence)
        assert mine[0].event_id not in {e.event_id for e in after_first}
        assert mine[1].event_id in {e.event_id for e in after_first}
        assert [e.sequence for e in after_first] == sorted(e.sequence for e in after_first)
        await feed.acknowledge([e.event_id for e in mine])
        assert not {e.event_id for e in await feed.pending(500)} & {e.event_id for e in mine}


async def test_crash_between_enqueue_and_acknowledge_is_redelivered_once(app_db: str) -> None:
    app = application(app_db)
    async with running(app) as client:
        employee = await login(client, "e003")
        case_id = (await start_conversation(client, employee))["caseId"]
        request_id = await _ask(client, employee, case_id)
        await _answer(client, request_id)
        services, runtime = app.state.business_services, app.state.ai_runtime
        [(event_id, acknowledged)] = _outbox(app_db, request_id)
        assert acknowledged is False
        [event] = [e for e in await services.event_feed.pending(500) if e.event_id == event_id]

        # The first relay instance enqueued the event and "crashed" before acknowledging it.
        await runtime.conversations.enqueue_representative_answered(event.payload)
        assert _outbox(app_db, request_id)[0][1] is False

        restarted: BusinessEventRelay = build_event_relay(services, runtime)
        report = await restarted.run_once()
        assert report.delivered >= 1
        assert _outbox(app_db, request_id)[0][1] is True
        assert len(_answered_inputs(app_db, event_id)) == 1
