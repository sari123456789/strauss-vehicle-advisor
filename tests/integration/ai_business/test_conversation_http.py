"""A1: conversation HTTP routes over the real stack (marker `db`).

Real `AuthService` bearer tokens (login through the Business auth route), the real Business
case lifecycle and the AI `ConversationService` on PostgreSQL. The app is assembled here the
way the shared lifespan will: `install_business_api` + `app.state.ai_conversations` + the
conversation router under `/api/v1`. Turn processing (the worker) is A2 and not exercised.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI

from app.ai.application.ports.conversation_store import NewMessage
from app.api.business.dependencies import BusinessApi, HttpConfig
from app.api.business.installer import install_business_api
from app.api.routers import conversations
from app.business.application.identity.auth_service import AuthService
from app.business.application.identity.credential_admin import CredentialAdminService
from app.business.infrastructure.clock import SystemClock
from app.business.infrastructure.security.password_hasher import ScryptPasswordHasher
from app.business.infrastructure.security.token_codec import Hs256TokenCodec
from app.contracts.tool_context import ActorRef, Origin, Role
from tests.integration.ai_business.conftest import Stack
from tests.integration.ai_business.db_guard import sql
from tests.integration.business.database.case_helpers import new_user, persistence, profile

PASSWORD = "correct horse battery"
MONTH = "2026-10"


@dataclass
class Http:
    client: httpx.AsyncClient
    stack: Stack

    async def user(self, role: Role = Role.EMPLOYEE) -> dict[str, str]:
        """A new Business user with a password; returns its bearer header (real login)."""
        async with persistence(self.stack.url) as p:
            subject = await new_user(p, role)
            if role is Role.EMPLOYEE:
                async with p.uow() as uow:
                    await uow.profiles.add(profile(subject))
                    await uow.commit()
            operator = ActorRef(
                subject_id=uuid4(), roles=frozenset({Role.IMPORT_OPERATOR}), origin=Origin.CLI
            )
            admin = CredentialAdminService(p.uow, ScryptPasswordHasher(n=2**10), SystemClock())
            login = f"u{subject.hex[:16]}"
            await admin.set_password(operator, login, PASSWORD)
        response = await self.client.post(
            "/api/v1/auth/login", json={"login": login, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['accessToken']}"}

    async def start(self, headers: dict[str, str]) -> dict[str, Any]:
        response = await self.client.post(
            "/api/v1/conversations",
            json={"clientRequestId": str(uuid4()), "calculationMonth": MONTH},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    async def say(
        self, headers: dict[str, str], conversation_id: str, text: str, message_id: UUID
    ) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"messageId": str(message_id), "text": text},
            headers=headers,
        )


@asynccontextmanager
async def http(stack: Stack) -> AsyncIterator[Http]:
    async with persistence(stack.url) as p:
        auth = AuthService(
            p.uow,
            ScryptPasswordHasher(n=2**10),
            Hs256TokenCodec(b"k" * 32, issuer="strauss", audience="strauss-api"),
            SystemClock(),
        )
        api = BusinessApi(
            auth=auth,
            tools=stack.business.tools,
            cases=stack.business.cases,
            profiles=stack.business.profiles,
            http=HttpConfig(),
        )
        app = FastAPI()
        install_business_api(app, api)
        app.state.ai_conversations = stack.conversations
        app.include_router(conversations.router, prefix="/api/v1")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
            yield Http(c, stack)


def problem(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == code
    assert "Traceback" not in response.text


async def test_start_is_idempotent_and_creates_a_real_business_case(stack: Stack) -> None:
    async with http(stack) as h:
        headers = await h.user()
        body = {"clientRequestId": str(uuid4()), "calculationMonth": MONTH}
        first = await h.client.post("/api/v1/conversations", json=body, headers=headers)
        again = await h.client.post("/api/v1/conversations", json=body, headers=headers)
        assert first.status_code == again.status_code == 200
        assert first.json() == again.json()
        view = first.json()
        assert view["status"] == "active" and view["calculationMonth"] == MONTH
        cases = sql(
            stack.url,
            "SELECT conversation_id FROM business.cases WHERE id = %s",
            UUID(view["caseId"]),
        )
        assert cases == [(UUID(view["conversationId"]),)]
        listed = await h.client.get("/api/v1/conversations", headers=headers)
        assert [c["conversationId"] for c in listed.json()["conversations"]] == [
            view["conversationId"]
        ]


async def test_a_message_is_accepted_once_and_polled_by_sequence(stack: Stack) -> None:
    async with http(stack) as h:
        headers = await h.user()
        conversation = (await h.start(headers))["conversationId"]
        message_id = uuid4()
        accepted = await h.say(headers, conversation, "שלום", message_id)
        assert accepted.status_code == 202, accepted.text
        submitted = accepted.json()
        assert submitted["duplicate"] is False
        repeat = await h.say(headers, conversation, "שלום", message_id)
        assert repeat.status_code == 202
        assert repeat.json() == {**submitted, "duplicate": True}
        changed = await h.say(headers, conversation, "משהו אחר", message_id)
        problem(changed, 409, "OPERATION_PAYLOAD_MISMATCH")

        page = (
            await h.client.get(
                f"/api/v1/conversations/{conversation}/messages?afterSequence=0", headers=headers
            )
        ).json()
        assert [(m["role"], m["text"]) for m in page["messages"]] == [("user", "שלום")]
        assert page["lastSequence"] == submitted["sequence"] and page["hasMore"] is False

        # A reply recorded for the input appears on the next poll after the cursor.
        reply = NewMessage(
            message_id=uuid4(),
            conversation_id=UUID(conversation),
            role="assistant",
            kind="text",
            text="שלום! איך אפשר לעזור?",
            input_id=UUID(submitted["inputId"]),
        )
        async with stack.persistence.transaction() as store:
            await store.append_message(reply)
        later = (
            await h.client.get(
                f"/api/v1/conversations/{conversation}/messages",
                params={"afterSequence": page["lastSequence"]},
                headers=headers,
            )
        ).json()
        assert [m["text"] for m in later["messages"]] == [reply.text]
        assert later["lastSequence"] > page["lastSequence"]
        empty = (
            await h.client.get(
                f"/api/v1/conversations/{conversation}/messages",
                params={"afterSequence": later["lastSequence"]},
                headers=headers,
            )
        ).json()
        assert empty == {"messages": [], "lastSequence": later["lastSequence"], "hasMore": False}

        status = await h.client.get(
            f"/api/v1/conversations/{conversation}/inputs/{submitted['inputId']}", headers=headers
        )
        assert status.status_code == 200
        state = status.json()
        assert state["status"] == "pending" and state["attempts"] == 0
        assert [m["role"] for m in state["messages"]] == ["user", "assistant"]

        detail = (
            await h.client.get(f"/api/v1/conversations/{conversation}", headers=headers)
        ).json()
        assert detail["conversation"]["conversationId"] == conversation
        assert len(detail["history"]["messages"]) == 2


async def test_another_employee_sees_nothing_and_cannot_write(stack: Stack) -> None:
    async with http(stack) as h:
        owner = await h.user()
        stranger = await h.user()
        conversation = (await h.start(owner))["conversationId"]
        accepted = (await h.say(owner, conversation, "שלום", uuid4())).json()
        base = f"/api/v1/conversations/{conversation}"
        for path in (base, f"{base}/messages", f"{base}/inputs/{accepted['inputId']}"):
            problem(await h.client.get(path, headers=stranger), 404, "NOT_FOUND")
        problem(await h.say(stranger, conversation, "היי", uuid4()), 404, "NOT_FOUND")
        listed = await h.client.get("/api/v1/conversations", headers=stranger)
        assert listed.json() == {"conversations": []}
        # an input of another conversation, under the stranger's own conversation, is 404 too
        own = (await h.start(stranger))["conversationId"]
        problem(
            await h.client.get(
                f"/api/v1/conversations/{own}/inputs/{accepted['inputId']}", headers=stranger
            ),
            404,
            "NOT_FOUND",
        )


async def test_authentication_and_role_are_enforced(stack: Stack) -> None:
    async with http(stack) as h:
        problem(await h.client.get("/api/v1/conversations"), 401, "UNAUTHENTICATED")
        bad = {"Authorization": "Bearer not.a.token"}
        problem(await h.client.get("/api/v1/conversations", headers=bad), 401, "UNAUTHENTICATED")
        representative = await h.user(Role.REPRESENTATIVE)
        response = await h.client.post(
            "/api/v1/conversations",
            json={"clientRequestId": str(uuid4()), "calculationMonth": MONTH},
            headers=representative,
        )
        problem(response, 404, "NOT_FOUND")


async def test_malformed_requests_are_rejected_without_echoing_input(stack: Stack) -> None:
    async with http(stack) as h:
        headers = await h.user()
        bad_month = await h.client.post(
            "/api/v1/conversations",
            json={"clientRequestId": str(uuid4()), "calculationMonth": "2026-13"},
            headers=headers,
        )
        problem(bad_month, 400, "MALFORMED_REQUEST")
        assert "2026-13" not in bad_month.text
        conversation = (await h.start(headers))["conversationId"]
        empty = await h.say(headers, conversation, "", uuid4())
        problem(empty, 400, "MALFORMED_REQUEST")
        blank = await h.say(headers, conversation, "   ", uuid4())
        problem(blank, 400, "MALFORMED_REQUEST")
        negative = await h.client.get(
            f"/api/v1/conversations/{conversation}/messages?afterSequence=-1", headers=headers
        )
        problem(negative, 400, "MALFORMED_REQUEST")


async def test_routes_report_unavailable_when_the_ai_runtime_is_not_wired(stack: Stack) -> None:
    async with http(stack) as h:
        headers = await h.user()
        del h.client._transport.app.state.ai_conversations  # type: ignore[attr-defined]
        response = await h.client.get("/api/v1/conversations", headers=headers)
        problem(response, 503, "INFRA_UNAVAILABLE")
