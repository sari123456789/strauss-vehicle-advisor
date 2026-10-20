"""M2 (B3b): representative HTTP routes over the real application (marker `db`).

Employee asks through the case route -> `rep01` lists, reads and answers through
`/api/v1/representative-requests` -> the answer applies to the case and its outbox event is
committed atomically. Authorization, idempotency, versions and privacy are checked at the
HTTP boundary (Problem Details, no stack traces).
"""

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

from tests.integration.ai_business.app_support import (
    application,
    application_database,
    case_version,
    login,
    problem,
    running,
    start_conversation,
)
from tests.integration.ai_business.db_guard import sql

PARTICIPATION = "alternative.participation"
BASE = "/api/v1/representative-requests"
PRIVATE_KEYS = {"caseId", "conversationId", "employeeId", "baseGross", "subjectId", "profile"}


@pytest.fixture(scope="module")
def app_db() -> Iterator[str]:
    with application_database() as url:
        yield url


async def _ask(client: httpx.AsyncClient, headers: dict[str, str]) -> tuple[str, str]:
    conversation = await start_conversation(client, headers)
    case_id = conversation["caseId"]
    response = await client.post(
        f"/api/v1/cases/{case_id}/representative-requests",
        json={
            "fieldCode": PARTICIPATION,
            "optionKey": "vehicle:CAR-10",
            "questionText": "מה דמי ההשתתפות העדכניים לרכב הזה?",
            "idempotencyKey": str(uuid4()),
            "expectedCaseVersion": await case_version(client, headers, case_id),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return case_id, response.json()["data"]["requestId"]


def _answer(version: int, value: str = "350", operation: str | None = None) -> dict[str, Any]:
    return {
        "operationId": operation or str(uuid4()),
        "expectedRequestVersion": version,
        "outcome": "answered",
        "value": value,
        "unit": "ILS",
    }


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


async def test_representative_lists_reads_and_answers(app_db: str) -> None:
    async with running(application(app_db)) as client:
        employee = await login(client, "e001")
        case_id, request_id = await _ask(client, employee)
        before = await case_version(client, employee, case_id)
        rep = await login(client, "rep01")

        listed = await client.get(BASE, headers=rep)
        assert listed.status_code == 200, listed.text
        views = listed.json()["data"]
        mine = next(v for v in views if v["requestId"] == request_id)
        assert mine["status"] == "open" and mine["fieldCode"] == PARTICIPATION
        assert not _keys(listed.json()) & PRIVATE_KEYS

        one = await client.get(f"{BASE}/{request_id}", headers=rep)
        assert one.status_code == 200 and one.json()["data"]["requestId"] == request_id

        body = _answer(mine["requestVersion"])
        answered = await client.post(f"{BASE}/{request_id}/answers", json=body, headers=rep)
        assert answered.status_code == 200, answered.text
        view = answered.json()["data"]
        assert view["outcome"] == "answered" and view["application"] == "applied"
        assert not _keys(answered.json()) & PRIVATE_KEYS

        replay = await client.post(f"{BASE}/{request_id}/answers", json=body, headers=rep)
        assert replay.status_code == 200 and replay.json()["data"] == view

        second = await client.post(
            f"{BASE}/{request_id}/answers", json=_answer(mine["requestVersion"]), headers=rep
        )
        problem(second, 409, "INVALID_STATE_TRANSITION")

        assert await case_version(client, employee, case_id) == before + 1
        events = sql(
            app_db,
            "SELECT count(*) FROM business.integration_outbox WHERE payload->>'requestId' = %s",
            request_id,
        )
        assert events == [(1,)]
        answered_only = await client.get(BASE, params={"status": "answered"}, headers=rep)
        assert request_id in [v["requestId"] for v in answered_only.json()["data"]]


async def test_stale_request_version_is_a_conflict(app_db: str) -> None:
    async with running(application(app_db)) as client:
        _, request_id = await _ask(client, await login(client, "e002"))
        rep = await login(client, "rep01")
        stale = await client.post(f"{BASE}/{request_id}/answers", json=_answer(99), headers=rep)
        problem(stale, 409, "VERSION_CONFLICT")


async def test_employees_and_anonymous_callers_are_refused(app_db: str) -> None:
    async with running(application(app_db)) as client:
        employee = await login(client, "e003")
        _, request_id = await _ask(client, employee)
        problem(await client.get(BASE, headers=employee), 403, "FORBIDDEN")
        problem(await client.get(f"{BASE}/{request_id}", headers=employee), 403, "FORBIDDEN")
        refused = await client.post(
            f"{BASE}/{request_id}/answers", json=_answer(0), headers=employee
        )
        problem(refused, 403, "FORBIDDEN")
        problem(await client.get(BASE), 401, "UNAUTHENTICATED")


async def test_unknown_request_is_not_found(app_db: str) -> None:
    async with running(application(app_db)) as client:
        rep = await login(client, "rep01")
        problem(await client.get(f"{BASE}/{uuid4()}", headers=rep), 404, "NOT_FOUND")
