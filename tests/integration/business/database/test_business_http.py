"""Step 4D: the Business HTTP API on real PostgreSQL - authentication, sessions, trusted actor,
authorization, safe error mapping and the BusinessToolsFacade integration.

The API is installed on a test FastAPI app with `install_business_api` (the isolated
component); `app.main`/`app.bootstrap` are not involved (integration is a coordinated step).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.business.dependencies import BusinessApi, HttpConfig
from app.api.business.installer import install_business_api
from app.business.application.cases.case_service import CaseService
from app.business.application.identity.auth_service import AuthConfig, AuthService
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.identity.credential_admin import CredentialAdminService
from app.business.application.ports.auth import UserAccount
from app.business.application.profiles.profile_service import EmployeeProfileService
from app.business.infrastructure.clock import SystemClock
from app.business.infrastructure.security.password_hasher import ScryptPasswordHasher
from app.business.infrastructure.security.token_codec import Hs256TokenCodec
from app.contracts.case_lifecycle import CreateCaseCommand
from app.contracts.tool_context import ActorRef, Origin, Role
from tests.integration.business.database.case_helpers import Persistence, persistence, profile
from tests.integration.business.database.db_helpers import run, sql
from tests.integration.business.database.test_tools_facade import facade

pytestmark = pytest.mark.sources

ORIGIN = "https://app.example"
PASSWORD = "correct horse battery"
CSRF = {"X-CSRF-Protection": "1"}


class MovableClock:
    def __init__(self) -> None:
        self.offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(UTC) + self.offset


@dataclass
class Http:
    client: httpx.AsyncClient
    p: Persistence
    clock: MovableClock
    cases: CaseService

    async def employee(self, role: Role = Role.EMPLOYEE, *, with_profile: bool = True) -> str:
        user_id = uuid4()
        login = f"u{user_id.hex[:12]}"
        async with self.p.uow() as uow:
            await uow.users.add(UserAccount(user_id, login, frozenset({role}), display_name="ע"))
            if with_profile and role is Role.EMPLOYEE:
                await uow.profiles.add(profile(user_id))
            await uow.commit()
        operator = ActorRef(
            subject_id=uuid4(), roles=frozenset({Role.IMPORT_OPERATOR}), origin=Origin.CLI
        )
        admin = CredentialAdminService(self.p.uow, ScryptPasswordHasher(n=2**10), SystemClock())
        await admin.set_password(operator, login, PASSWORD)
        return login

    async def login(self, login: str, password: str = PASSWORD) -> httpx.Response:
        return await self.client.post(
            "/api/v1/auth/login", json={"login": login, "password": password}
        )

    async def token(self, login: str) -> dict[str, str]:
        response = await self.login(login)
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['accessToken']}"}

    async def new_case(self, login: str) -> UUID:
        async with self.p.uow() as uow:
            account = await uow.users.get_by_login(login)
        assert account is not None
        actor = ActorRef(subject_id=account.user_id, roles=account.roles, origin=Origin.AGENT)
        result = await self.cases.create_case_for_conversation(
            actor,
            CreateCaseCommand(
                conversation_id=uuid4(), calculation_month="2026-10", operation_id=uuid4()
            ),
        )
        return result.case_id


@asynccontextmanager
async def http(url: str, **overrides: Any) -> AsyncIterator[Http]:
    async with persistence(url) as p:
        clock = MovableClock()
        policy = AuthorizationPolicy()
        profiles = EmployeeProfileService(p.uow, policy, clock)
        cases = CaseService(p.uow, policy, profiles, p.reference, clock)
        auth = AuthService(
            p.uow,
            ScryptPasswordHasher(n=2**10),
            Hs256TokenCodec(b"k" * 32, issuer="strauss", audience="strauss-api"),
            clock,
            AuthConfig(access_ttl=timedelta(minutes=15), max_failures=5),
        )
        api = BusinessApi(
            auth=auth,
            tools=overrides.get("tools", facade(p)),
            cases=overrides.get("cases", cases),
            profiles=profiles,
            http=HttpConfig(allowed_origins=(ORIGIN,)),
        )
        app = FastAPI()
        install_business_api(app, api)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
            yield Http(c, p, clock, cases)


def problem(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = response.json()
    assert body["code"] == code and body["status"] == status
    assert "Traceback" not in response.text
    return body


# --- authentication and sessions ---------------------------------------------------------


def test_login_sets_a_hardened_refresh_cookie_and_returns_no_secret(business_db: str) -> None:
    async def scenario() -> None:
        async with http(business_db) as h:
            login = await h.employee()
            response = await h.login(login)
            assert response.status_code == 200
            body = response.json()
            assert body["tokenType"] == "Bearer" and body["actor"]["roles"] == ["employee"]
            cookie = response.headers["set-cookie"]
            for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/api/v1/auth"):
                assert flag.lower() in cookie.lower(), flag
            refresh = h.client.cookies.get("strauss_refresh")
            assert refresh and refresh not in response.text  # never in the body
            stored = sql(business_db, "SELECT token_hash FROM auth.refresh_sessions")
            assert all(refresh not in row[0] for row in stored)  # digests only
            me = await h.client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['accessToken']}"}
            )
            assert me.status_code == 200 and me.json()["roles"] == ["employee"]

    run(scenario())


def test_failed_logins_are_indistinguishable_and_throttled(business_db: str) -> None:
    async def scenario() -> None:
        async with http(business_db) as h:
            login = await h.employee()
            inactive = await h.employee()
            sql(business_db, "UPDATE auth.users SET active = false WHERE login = %s", inactive)
            bodies = [
                problem(await h.login(login, "wrong password"), 401, "UNAUTHENTICATED"),
                problem(await h.login("nobody-here"), 401, "UNAUTHENTICATED"),
                problem(await h.login(inactive), 401, "UNAUTHENTICATED"),
            ]
            assert len({(b["title"], str(b["details"])) for b in bodies}) == 1
            for _ in range(4):
                await h.login(login, "wrong password")
            limited = await h.login(login)  # even the right password is throttled now
            body = problem(limited, 429, "RATE_LIMITED")
            assert int(limited.headers["Retry-After"]) == int(body["details"]["retryAfterSeconds"])
            h.clock.offset = timedelta(minutes=16)
            assert (await h.login(login)).status_code == 200

    run(scenario())


def test_bearer_tokens_are_verified_on_every_request(business_db: str) -> None:
    async def scenario() -> None:
        async with http(business_db) as h:
            login = await h.employee()
            headers = await h.token(login)
            problem(await h.client.get("/api/v1/auth/me"), 401, "UNAUTHENTICATED")
            bad = {"Authorization": "Bearer not.a.token"}
            response = await h.client.get("/api/v1/auth/me", headers=bad)
            assert problem(response, 401, "UNAUTHENTICATED")
            assert response.headers["WWW-Authenticate"] == "Bearer"
            assert (await h.client.get("/api/v1/auth/me", headers=headers)).status_code == 200
            h.clock.offset = timedelta(minutes=16)  # access token expired
            problem(await h.client.get("/api/v1/auth/me", headers=headers), 401, "UNAUTHENTICATED")
            h.clock.offset = timedelta(0)
            sql(business_db, "UPDATE auth.users SET active = false WHERE login = %s", login)
            problem(await h.client.get("/api/v1/auth/me", headers=headers), 401, "UNAUTHENTICATED")

    run(scenario())


def test_refresh_rotates_and_reuse_ends_the_session_family(business_db: str) -> None:
    async def scenario() -> None:
        async with http(business_db) as h:
            login = await h.employee()
            await h.login(login)
            first = h.client.cookies.get("strauss_refresh")
            problem(await h.client.post("/api/v1/auth/refresh"), 403, "FORBIDDEN")  # no CSRF
            foreign = {**CSRF, "Origin": "https://evil.example"}
            problem(await h.client.post("/api/v1/auth/refresh", headers=foreign), 403, "FORBIDDEN")
            rotated = await h.client.post(
                "/api/v1/auth/refresh", headers={**CSRF, "Origin": ORIGIN}
            )
            assert rotated.status_code == 200
            second = h.client.cookies.get("strauss_refresh")
            assert second and second != first
            newer = {"Authorization": f"Bearer {rotated.json()['accessToken']}"}
            assert (await h.client.get("/api/v1/auth/me", headers=newer)).status_code == 200
            h.client.cookies.set("strauss_refresh", first or "", path="/api/v1/auth")
            reused = await h.client.post("/api/v1/auth/refresh", headers=CSRF)
            problem(reused, 401, "UNAUTHENTICATED")  # token theft detected
            problem(await h.client.get("/api/v1/auth/me", headers=newer), 401, "UNAUTHENTICATED")
            h.client.cookies.set("strauss_refresh", second or "", path="/api/v1/auth")
            problem(
                await h.client.post("/api/v1/auth/refresh", headers=CSRF), 401, "UNAUTHENTICATED"
            )

    run(scenario())


def test_logout_revokes_immediately(business_db: str) -> None:
    async def scenario() -> None:
        async with http(business_db) as h:
            login = await h.employee()
            headers = await h.token(login)
            out = await h.client.post("/api/v1/auth/logout", headers=CSRF)
            assert out.status_code == 204 and 'strauss_refresh=""' in out.headers["set-cookie"]
            problem(await h.client.get("/api/v1/auth/me", headers=headers), 401, "UNAUTHENTICATED")
            again = await h.client.post("/api/v1/auth/logout", headers=CSRF)
            assert again.status_code == 204  # idempotent, reveals nothing

    run(scenario())


# --- business routes ---------------------------------------------------------------------


def test_case_routes_use_the_facade_with_a_trusted_context(business_db: str) -> None:
    async def scenario() -> None:
        async with http(business_db) as h:
            login = await h.employee()
            headers = await h.token(login)
            case_id = await h.new_case(login)
            base = f"/api/v1/cases/{case_id}"
            trace = {**headers, "X-Request-ID": "turn-0001"}
            context = await h.client.get(base, headers=trace)
            assert context.status_code == 200 and context.json()["status"] == "ok"
            assert context.json()["traceId"] == "turn-0001"
            assert context.headers["X-Request-ID"] == "turn-0001"
            assert context.headers["Cache-Control"] == "no-store"
            assert context.headers["X-Content-Type-Options"] == "nosniff"
            listed = await h.client.post(f"{base}/option-lists", headers=headers, json={})
            assert listed.status_code == 200 and listed.json()["data"]["items"]
            compared = await h.client.post(
                f"{base}/comparisons",
                headers=headers,
                json={
                    "selection": {"mode": "explicit", "optionKeys": ["vehicle:CAR-10"]},
                    "includeCurrent": False,
                    "kind": "regular",
                    "expectedCaseVersion": 0,
                    "expectedProfileVersion": 1,
                    "operationId": str(uuid4()),
                },
            )
            assert compared.status_code == 200, compared.text
            assert compared.json()["status"] in {"ok", "needs_information", "partial"}
            run_id = compared.json()["data"]["runId"]
            details = await h.client.get(f"{base}/comparisons/{run_id}", headers=headers)
            assert details.status_code == 200
            asked = await h.client.post(
                f"{base}/representative-requests",
                headers=headers,
                json={
                    "fieldCode": "alternative.participation",
                    "optionKey": "vehicle:CAR-11",
                    "questionText": "מה ההשתתפות?",
                    "idempotencyKey": str(uuid4()),
                    "expectedCaseVersion": 0,
                },
            )
            assert asked.status_code == 200 and asked.json()["data"]["created"]
            request_id = asked.json()["data"]["requestId"]
            status = await h.client.get(
                f"{base}/representative-requests/{request_id}", headers=headers
            )
            assert status.status_code == 200 and status.json()["data"]["status"] == "open"
            mine = await h.client.get("/api/v1/me/profile", headers=headers)
            assert mine.status_code == 200 and mine.json()["data"]["baseGross"]["value"]

    run(scenario())


def test_business_errors_become_safe_problem_details(business_db: str) -> None:
    async def scenario() -> None:
        async with http(business_db) as h:
            login = await h.employee()
            other = await h.employee()
            headers = await h.token(login)
            case_id = await h.new_case(login)
            foreign_case = await h.new_case(other)
            base = f"/api/v1/cases/{case_id}"
            for target in (foreign_case, uuid4()):  # AC26/AC27: no disclosure
                body = problem(
                    await h.client.get(f"/api/v1/cases/{target}", headers=headers),
                    404,
                    "NOT_FOUND",
                )
                assert body["details"] == {} and body["issues"] == []
            stale = await h.client.patch(
                base,
                headers=headers,
                json={
                    "mode": "hypothetical",
                    "expectedCaseVersion": 7,
                    "operationId": str(uuid4()),
                    "changes": [{"op": "set", "field": "payroll.baseGross", "value": "18500.00"}],
                },
            )
            problem(stale, 409, "VERSION_CONFLICT")
            malformed = await h.client.patch(
                base, headers=headers, json={"salaryLeak": "18531.77", "mode": "x"}
            )
            body = problem(malformed, 400, "MALFORMED_REQUEST")
            assert "18531.77" not in malformed.text and "salaryLeak" in body["details"]["fields"]
            missing = await h.client.post(
                f"{base}/vehicle-tax-values", headers=headers, json={"catalogueId": "CAR-99"}
            )
            body = problem(missing, 404, "NOT_FOUND")
            assert [i["code"] for i in body["issues"]] == ["VEHICLE_NOT_FOUND"]
            bad_uuid = await h.client.get("/api/v1/cases/not-a-uuid", headers=headers)
            problem(bad_uuid, 400, "MALFORMED_REQUEST")
            rep_login = await h.employee(Role.REPRESENTATIVE)
            rep = await h.token(rep_login)
            problem(await h.client.get(base, headers=rep), 403, "FORBIDDEN")
            sql(
                business_db,
                "UPDATE auth.users SET roles = ARRAY['representative'] WHERE login = %s",
                login,
            )
            problem(await h.client.get(base, headers=headers), 403, "FORBIDDEN")  # roles from DB

    run(scenario())


def test_unexpected_failures_are_500_or_503_without_details(
    business_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    class BrokenCases:
        async def context_for_case(self, *_: Any) -> Any:
            raise RuntimeError("boom baseGross=18531.77")

    class BrokenTools:
        async def get_conversation_context(self, _ctx: Any) -> Any:
            raise RuntimeError("never reached")

    async def scenario() -> None:
        async with http(business_db, cases=BrokenCases()) as h:
            login = await h.employee()
            headers = await h.token(login)
            response = await h.client.get(f"/api/v1/cases/{uuid4()}", headers=headers)
            body = problem(response, 500, "INFRA_UNAVAILABLE")
            assert body["details"] == {"reason": "internal_error"}
            assert "18531.77" not in response.text and "18531.77" not in caplog.text
        async with persistence(business_db) as p:  # a facade failure is an envelope -> 503
            tools = facade(p)
            tools._cases = BrokenTools()  # type: ignore[assignment]
            async with http(business_db, tools=tools) as h:
                login = await h.employee()
                headers = await h.token(login)
                case_id = await h.new_case(login)
                failed = await h.client.get(f"/api/v1/cases/{case_id}", headers=headers)
                body = problem(failed, 503, "INFRA_UNAVAILABLE")
                assert body["details"] == {}

    run(scenario())
