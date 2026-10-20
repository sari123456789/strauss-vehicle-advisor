"""M3B (B4): vehicle waiver and maintenance allowance end to end over the real application.

Authoritative data: the workbook sheet "עיסוקים" (14 occupations: mandatory car, waiver allowed,
allowance 1,200 / 1,500 / 2,000 / 2,600 / 3,000) imported into `business.occupation_policies`.
Reference cases from the sheet "חישובים" (Agent Spec §8):
- E-005: MAZDA 3 -> waiver with a 2,000 allowance, alternative net 17,806.288 -> "17806".
- E-003: allowance 1,500 -> MG4 (CAR-06), alternative net 13,108.657 -> "13109" (AC13).
Plus T21 (mandatory-car occupation: ineligible, never a number) and T22/T23 in a conversation:
the agent shows only Business values and never invents an allowance.
"""

import asyncio
import re
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.ai.workflow.models import Intent
from tests.fakes.ai.fake_llm import ScriptedLlm, interpretation
from tests.integration.ai_business.app_support import (
    application,
    application_database,
    login,
    problem,
    running,
    start_conversation,
)

pytestmark = pytest.mark.sources

AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+|\d{4,}")
YEAR = re.compile(r"\b20\d\d\b")
"""Source years in names and disclaimers; amounts are always shown with a separator."""
RAW_CODES = re.compile(r"\b(40[0-9]|500|503)\b|NOT_FOUND|FORBIDDEN|Traceback", re.I)


@pytest.fixture(scope="module")
def app_db() -> Iterator[str]:
    with application_database() as url:
        yield url


async def _case(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    conversation = await start_conversation(client, headers)
    response = await client.get(f"/api/v1/cases/{conversation['caseId']}", headers=headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return {**conversation, "caseVersion": data["caseVersion"], "profile": data["profile"]}


async def _compare(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    case: dict[str, Any],
    option: str,
    *,
    kind: str = "regular",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "selection": {"mode": "explicit", "optionKeys": [option]},
        "includeCurrent": False,
        "kind": kind,
        "expectedCaseVersion": case["caseVersion"],
        "operationId": str(uuid4()),
    }
    if kind == "regular":
        body["expectedProfileVersion"] = case["profile"]["profileVersion"]
    response = await client.post(
        f"/api/v1/cases/{case['caseId']}/comparisons", json=body, headers=headers
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def test_e005_waiver_uses_the_occupation_allowance(app_db: str) -> None:
    async with running(application(app_db)) as client:
        headers = await login(client, "e005")
        case = await _case(client, headers)
        result = await _compare(client, headers, case, "waiver")
        [waiver] = result["alternatives"]
        assert waiver["optionKey"] == "waiver" and waiver["status"] == "calculated"
        assert waiver["net"]["display"] == "17806"
        assert waiver["net"]["raw"].startswith("17806.28")
        assert waiver["breakdown"]["allowance"]["display"] == "2000"  # A from the table


async def test_e003_allowance_to_mg4(app_db: str) -> None:
    async with running(application(app_db)) as client:
        headers = await login(client, "e003")
        case = await _case(client, headers)
        result = await _compare(client, headers, case, "vehicle:CAR-06")
        [mg4] = result["alternatives"]
        assert mg4["status"] == "calculated"
        assert mg4["net"]["display"] == "13109"
        assert mg4["breakdown"]["allowance"]["display"] == "0"  # AC13: no residual allowance


async def test_mandatory_car_occupation_is_ineligible_for_waiver(app_db: str) -> None:
    async with running(application(app_db)) as client:
        headers = await login(client, "e001")
        case = await _case(client, headers)
        patched = await client.patch(
            f"/api/v1/cases/{case['caseId']}",
            json={
                "mode": "hypothetical",
                "expectedCaseVersion": case["caseVersion"],
                "operationId": str(uuid4()),
                "changes": [
                    {"op": "set", "field": "employee.occupationId", "value": "OCC-FIELD-SALES-REP"}
                ],
            },
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        case["caseVersion"] = patched.json()["caseVersion"]
        result = await _compare(client, headers, case, "waiver", kind="hypothetical")
        [waiver] = result["alternatives"]
        assert waiver["status"] == "ineligible"
        assert "waiver_not_allowed_mandatory_car" in waiver["eligibilityReasons"]
        assert waiver["net"] is None and waiver["breakdown"] is None


async def _converse(
    client: httpx.AsyncClient, headers: dict[str, str], conversation: str, text: str
) -> list[dict[str, Any]]:
    sent = await client.post(
        f"/api/v1/conversations/{conversation}/messages",
        json={"messageId": str(uuid4()), "text": text},
        headers=headers,
    )
    assert sent.status_code == 202, sent.text
    after = sent.json()["sequence"]
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        state = await client.get(
            f"/api/v1/conversations/{conversation}/inputs/{sent.json()['inputId']}",
            headers=headers,
        )
        if state.json()["status"] in ("completed", "dead", "needs_reconciliation"):
            page = await client.get(
                f"/api/v1/conversations/{conversation}/messages",
                params={"afterSequence": after},
                headers=headers,
            )
            replies: list[dict[str, Any]] = [
                m for m in page.json()["messages"] if m["role"] == "assistant"
            ]
            assert state.json()["status"] == "completed", replies
            return replies
        await asyncio.sleep(0.2)
    raise AssertionError("the turn did not finish")


async def test_waiver_conversation_shows_only_the_business_amounts(app_db: str) -> None:
    llm = ScriptedLlm().interpret(interpretation(Intent.CALCULATE_OPTIONS, include_waiver=True))
    app = application(app_db, llm=llm, worker_in_process=True)
    async with running(app) as client:
        headers = await login(client, "e005")
        conversation = (await start_conversation(client, headers))["conversationId"]
        replies = await _converse(client, headers, conversation, "כמה אקבל אם אוותר על הרכב?")
        text = "\n".join(r["text"] for r in replies)
        assert not RAW_CODES.search(text)
        comparisons = [r for r in replies if r["kind"] == "comparison"]
        assert comparisons, [r["kind"] for r in replies]
        run_id = comparisons[-1]["payload"]["runId"]
        case_id = (
            await client.get(f"/api/v1/conversations/{conversation}", headers=headers)
        ).json()["conversation"]["caseId"]
        details = await client.get(f"/api/v1/cases/{case_id}/comparisons/{run_id}", headers=headers)
        alternatives = details.json()["data"]["result"]["alternatives"]
        waiver = next(a for a in alternatives if a["optionKey"] == "waiver")
        assert waiver["net"]["display"] == "17806"
        assert "17,806" in text
        displays = {
            a[key]["display"]
            for a in alternatives
            for key in ("net", "delta")
            if a.get(key) is not None
        }
        # Years (model years, source-data years) are not amounts.
        shown = {m.replace(",", "") for m in AMOUNT.findall(YEAR.sub("", text))}
        assert shown <= displays, shown - displays  # no invented amount


async def test_occupation_outside_the_table_is_never_given_an_allowance(app_db: str) -> None:
    """T23: an occupation missing from the table is not resolved and cannot be set; the
    agent must clarify (or escalate) instead of inventing a waiver allowance."""
    async with running(application(app_db)) as client:
        headers = await login(client, "e001")
        case = await _case(client, headers)
        base = f"/api/v1/cases/{case['caseId']}"
        resolved = await client.post(
            f"{base}/occupation-resolutions",
            json={"query": "אסטרונאוט/ית חלל"},
            headers=headers,
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["data"]["status"] in ("not_found", "ambiguous")
        official = await client.post(
            f"{base}/occupation-resolutions", json={"query": "מפתח/ת תוכנה"}, headers=headers
        )
        assert official.json()["data"]["status"] == "resolved"
        refused = await client.patch(
            base,
            json={
                "mode": "hypothetical",
                "expectedCaseVersion": case["caseVersion"],
                "operationId": str(uuid4()),
                "changes": [
                    {"op": "set", "field": "employee.occupationId", "value": "OCC-NOT-IN-TABLE"}
                ],
            },
            headers=headers,
        )
        problem(refused, 422, "INVALID_BUSINESS_INPUT")
        assert refused.json()["details"] == {"field": "employee.occupationId"}
