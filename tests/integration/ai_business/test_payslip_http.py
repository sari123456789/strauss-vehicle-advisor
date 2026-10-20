"""Payslip import over HTTP on the real stack (marker `db`): real login and bearer tokens, the
real conversation ownership, the real Business actual_update, PostgreSQL. The app is assembled
the way the shared lifespan will (business API + conversations + payslip router); `app.main`
itself is wired at the coordinated integration stage.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI

import tests.integration.ai_business.test_conversation_http as conversation_http
from app.ai.infrastructure.payslip.composition import build_payslip_service
from app.api.business.dependencies import BusinessApi, HttpConfig
from app.api.business.installer import install_business_api
from app.api.routers import conversations, payslip_imports
from app.business.application.identity.auth_service import AuthService
from app.business.infrastructure.clock import SystemClock
from app.business.infrastructure.security.password_hasher import ScryptPasswordHasher
from app.business.infrastructure.security.token_codec import Hs256TokenCodec
from tests.fakes.payslip import fixture
from tests.integration.ai_business.conftest import Stack
from tests.integration.ai_business.db_guard import sql
from tests.integration.business.database.case_helpers import persistence

PDF = {"Content-Type": "application/pdf"}


@asynccontextmanager
async def http(stack: Stack) -> AsyncIterator[conversation_http.Http]:
    async with persistence(stack.url) as p:
        auth = AuthService(
            p.uow,
            ScryptPasswordHasher(n=2**10),
            Hs256TokenCodec(b"k" * 32, issuer="strauss", audience="strauss-api"),
            SystemClock(),
        )
        app = FastAPI()
        install_business_api(
            app,
            BusinessApi(
                auth=auth,
                tools=stack.business.tools,
                cases=stack.business.cases,
                profiles=stack.business.profiles,
                http=HttpConfig(),
            ),
        )
        app.state.ai_conversations = stack.conversations
        app.state.ai_payslip_imports = build_payslip_service(
            conversations=stack.conversations, business=stack.business.tools
        )
        app.include_router(conversations.router, prefix="/api/v1")
        app.include_router(payslip_imports.router, prefix="/api/v1")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
            yield conversation_http.Http(c, stack)


async def _profile(h: conversation_http.Http, headers: dict[str, str]) -> dict[str, Any]:
    response = await h.client.get("/api/v1/me/profile", headers=headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def test_full_payslip_journey_over_http(stack: Stack) -> None:
    async with http(stack) as h:
        headers = await h.user()
        conversation = (await h.start(headers))["conversationId"]
        before = await _profile(h, headers)

        uploaded = await h.client.post(
            f"/api/v1/conversations/{conversation}/payslip-imports",
            content=fixture("ambiguous.pdf"),
            headers={**headers, **PDF},
        )
        assert uploaded.status_code == 201, uploaded.text
        draft = uploaded.json()
        url = f"/api/v1/payslip-imports/{draft['draftId']}"
        assert await _profile(h, headers) == before  # nothing applied before approval

        corrected = await h.client.patch(
            url,
            json={
                "expectedDraftVersion": 1,
                "corrections": [
                    {"field": "payroll.baseGross", "value": "15000.00"},
                    {"field": "payroll.pensionEmployeeRate", "value": "0.06"},
                ],
            },
            headers=headers,
        )
        assert corrected.status_code == 200, corrected.text

        body = {
            "expectedDraftVersion": 2,
            "expectedProfileVersion": before["profileVersion"],
            "operationId": str(uuid4()),
            "fields": ["payroll.baseGross", "payroll.pensionEmployeeRate"],
        }
        approved = await h.client.post(f"{url}/approve", json=body, headers=headers)
        assert approved.status_code == 200, approved.text
        replay = await h.client.post(f"{url}/approve", json=body, headers=headers)
        assert replay.json() == approved.json()

        after = await _profile(h, headers)
        assert after["profileVersion"] == before["profileVersion"] + 1
        assert after["baseGross"]["value"] == "15000.00"
        assert after["pensionEmployeeRate"]["value"] == "0.06"
        assert after["baseGross"]["source"] == "employee"
        assert after["bonusGross"] == before["bonusGross"]  # not selected -> untouched

        # The conversation's own case observes the updated profile (case synchronized).
        rows = sql(
            stack.url,
            "SELECT salary_profile_version_used FROM business.cases WHERE conversation_id = %s",
            draft["conversationId"],
        )
        assert rows == [(after["profileVersion"],)]


async def test_http_requires_authentication_and_ownership(stack: Stack) -> None:
    async with http(stack) as h:
        owner = await h.user()
        stranger = await h.user()
        conversation = (await h.start(owner))["conversationId"]
        path = f"/api/v1/conversations/{conversation}/payslip-imports"

        anonymous = await h.client.post(path, content=fixture("basic_he.pdf"), headers=PDF)
        conversation_http.problem(anonymous, 401, "UNAUTHENTICATED")

        foreign = await h.client.post(
            path, content=fixture("basic_he.pdf"), headers={**stranger, **PDF}
        )
        conversation_http.problem(foreign, 404, "NOT_FOUND")

        draft = (
            await h.client.post(path, content=fixture("basic_he.pdf"), headers={**owner, **PDF})
        ).json()
        url = f"/api/v1/payslip-imports/{draft['draftId']}"
        conversation_http.problem(await h.client.get(url, headers=stranger), 404, "NOT_FOUND")
        stale = await h.client.post(
            f"{url}/approve",
            json={
                "expectedDraftVersion": 1,
                "expectedProfileVersion": 99,
                "operationId": str(uuid4()),
                "fields": ["payroll.baseGross"],
            },
            headers=owner,
        )
        conversation_http.problem(stale, 409, "VERSION_CONFLICT")
        assert stale.json()["details"]["payslipCode"] == "PROFILE_VERSION_CONFLICT"
