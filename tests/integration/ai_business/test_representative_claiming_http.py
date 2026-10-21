"""Representative claiming over the real application (marker `db`).

Claim, release, summary, filters and paging through `/api/v1/representative-requests`:
the handler is visible to every representative, only the handler can answer or release, and
claims are idempotent by operation ID. Problem Details are checked at the HTTP boundary.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest

from tests.integration.ai_business.app_support import (
    application,
    application_database,
    login,
    problem,
    running,
    set_password,
)
from tests.integration.ai_business.db_guard import sql
from tests.integration.ai_business.test_representative_http import _ask as ask_request

BASE = "/api/v1/representative-requests"
PRIVATE_KEYS = {"caseId", "conversationId", "employeeId", "subjectId", "claimedBy", "profile"}


@pytest.fixture(scope="module")
def app_db() -> Iterator[str]:
    with application_database() as url:
        yield url


def _claim_body(version: int = 0, operation: str | None = None) -> dict[str, object]:
    return {"operationId": operation or str(uuid4()), "expectedRequestVersion": version}


def _release_body(version: int, operation: str | None = None) -> dict[str, object]:
    return {"operationId": operation or str(uuid4()), "expectedAssignmentVersion": version}


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


async def test_claim_is_visible_to_others_and_only_the_handler_can_answer(app_db: str) -> None:
    await _set_rep02_password(app_db)
    async with running(application(app_db)) as client:
        employee = await login(client, "e001")
        _, request_id = await ask_request(client, employee)
        rep1 = await login(client, "rep01")
        rep2 = await login(client, "rep02")

        claimed = await client.post(f"{BASE}/{request_id}/claim", json=_claim_body(), headers=rep1)
        assert claimed.status_code == 200, claimed.text
        result = claimed.json()["data"]
        assert set(result) == {"assignmentId", "assignmentVersion", "claimedAt"}
        assert not _keys(claimed.json()) & PRIVATE_KEYS

        mine = (await client.get(f"{BASE}/{request_id}", headers=rep1)).json()["data"]
        assert mine["handlingStatus"] == "assigned_to_me" and mine["canAnswer"]
        assert mine["canRelease"] and not mine["canClaim"]

        theirs = (await client.get(f"{BASE}/{request_id}", headers=rep2)).json()["data"]
        assert theirs["handlingStatus"] == "assigned_to_other"
        assert theirs["handlerDisplayName"] and theirs["handlerDisplayName"] != "rep01"
        assert not theirs["canClaim"] and not theirs["canAnswer"] and not theirs["canRelease"]
        assert not _keys(theirs) & PRIVATE_KEYS

        problem(
            await client.post(f"{BASE}/{request_id}/claim", json=_claim_body(), headers=rep2),
            409,
            "INVALID_STATE_TRANSITION",
        )
        answer = {
            "operationId": str(uuid4()),
            "expectedRequestVersion": 0,
            "outcome": "answered",
            "value": "350",
            "unit": "ILS",
        }
        problem(
            await client.post(f"{BASE}/{request_id}/answers", json=answer, headers=rep2),
            403,
            "FORBIDDEN",
        )
        problem(
            await client.post(
                f"{BASE}/{request_id}/release",
                json=_release_body(0),
                headers=rep2,
            ),
            403,
            "FORBIDDEN",
        )


async def test_release_returns_the_request_and_the_other_can_claim_it(app_db: str) -> None:
    await _set_rep02_password(app_db)
    async with running(application(app_db)) as client:
        employee = await login(client, "e002")
        _, request_id = await ask_request(client, employee)
        rep1 = await login(client, "rep01")
        rep2 = await login(client, "rep02")
        await client.post(f"{BASE}/{request_id}/claim", json=_claim_body(), headers=rep1)

        released = await client.post(
            f"{BASE}/{request_id}/release", json=_release_body(0), headers=rep1
        )
        assert released.status_code == 200, released.text
        assert set(released.json()["data"]) == {"assignmentId", "releasedAt"}

        view = (await client.get(f"{BASE}/{request_id}", headers=rep2)).json()["data"]
        assert view["handlingStatus"] == "unclaimed" and view["canClaim"]
        queue = await client.get(
            BASE, params={"assignment": "unclaimed", "search": request_id}, headers=rep2
        )
        assert [v["requestId"] for v in queue.json()["data"]] == [request_id]

        claimed = await client.post(f"{BASE}/{request_id}/claim", json=_claim_body(), headers=rep2)
        assert claimed.status_code == 200, claimed.text
        problem(
            await client.post(f"{BASE}/{request_id}/release", json=_release_body(0), headers=rep1),
            403,
            "FORBIDDEN",
        )


async def test_claim_is_idempotent_by_operation_id(app_db: str) -> None:
    async with running(application(app_db)) as client:
        _, request_id = await ask_request(client, await login(client, "e003"))
        rep = await login(client, "rep01")
        operation = str(uuid4())
        first = await client.post(
            f"{BASE}/{request_id}/claim", json=_claim_body(operation=operation), headers=rep
        )
        second = await client.post(
            f"{BASE}/{request_id}/claim", json=_claim_body(operation=operation), headers=rep
        )
        assert first.status_code == 200 and second.status_code == 200
        assert second.json()["data"] == first.json()["data"]
        assert sql(
            app_db,
            "SELECT count(*) FROM business.request_assignments WHERE request_id = %s",
            request_id,
        ) == [(1,)]


async def test_claim_refusals_are_safe_problem_details(app_db: str) -> None:
    async with running(application(app_db)) as client:
        employee = await login(client, "e005")
        _, request_id = await ask_request(client, employee)
        problem(
            await client.post(f"{BASE}/{request_id}/claim", json=_claim_body(), headers=employee),
            403,
            "FORBIDDEN",
        )
        rep = await login(client, "rep01")
        problem(
            await client.post(f"{BASE}/{uuid4()}/claim", json=_claim_body(), headers=rep),
            404,
            "NOT_FOUND",
        )
        problem(
            await client.post(f"{BASE}/not-a-uuid/claim", json=_claim_body(), headers=rep),
            400,
            "MALFORMED_REQUEST",
        )
        stale = await client.post(
            f"{BASE}/{request_id}/claim", json=_claim_body(version=99), headers=rep
        )
        problem(stale, 409, "VERSION_CONFLICT")
        anonymous = await client.post(f"{BASE}/{request_id}/claim", json=_claim_body())
        problem(anonymous, 401, "UNAUTHENTICATED")


async def test_summary_is_counted_over_the_queue_and_is_not_a_request_id(app_db: str) -> None:
    async with running(application(app_db)) as client:
        rep = await login(client, "rep01")
        before = (await client.get(f"{BASE}/summary", headers=rep)).json()["data"]
        assert set(before) == {
            "total",
            "unclaimed",
            "assignedToMe",
            "assignedToOthers",
            "answered",
            "cannotDetermine",
            "cancelled",
        }
        employee = await login(client, "e001")
        _, request_id = await ask_request(client, employee)
        await client.post(f"{BASE}/{request_id}/claim", json=_claim_body(), headers=rep)
        after = (await client.get(f"{BASE}/summary", headers=rep)).json()["data"]
        assert after["total"] == before["total"] + 1
        assert after["unclaimed"] == before["unclaimed"]
        assert after["assignedToMe"] == before["assignedToMe"] + 1


async def test_list_filters_and_paging_are_validated_and_stable(app_db: str) -> None:
    async with running(application(app_db)) as client:
        rep = await login(client, "rep01")
        employee = await login(client, "e002")
        ids = []
        for _ in range(3):
            _, request_id = await ask_request(client, employee)
            ids.append(request_id)
        claimed = await client.post(f"{BASE}/{ids[0]}/claim", json=_claim_body(), headers=rep)
        assert claimed.status_code == 200, claimed.text

        mine = await client.get(BASE, params={"assignment": "mine", "limit": 200}, headers=rep)
        assert mine.status_code == 200, mine.text
        assert all(v["handlingStatus"] == "assigned_to_me" for v in mine.json()["data"])

        first_page = await client.get(BASE, params={"search": ids[1][:8]}, headers=rep)
        assert [v["requestId"] for v in first_page.json()["data"]] == [ids[1]]

        page = await client.get(BASE, params={"limit": 2, "after": ids[0]}, headers=rep)
        assert page.status_code == 200, page.text
        assert ids[0] not in [v["requestId"] for v in page.json()["data"]]

        unknown_cursor = await client.get(BASE, params={"after": str(uuid4())}, headers=rep)
        problem(unknown_cursor, 422, "INVALID_BUSINESS_INPUT")
        bad_filter = await client.get(BASE, params={"assignment": "bogus"}, headers=rep)
        problem(bad_filter, 400, "MALFORMED_REQUEST")


async def _set_rep02_password(url: str) -> None:
    await set_password(url, "rep02")
