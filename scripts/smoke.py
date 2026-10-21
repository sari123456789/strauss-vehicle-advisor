"""End-to-end smoke check of a running Strauss Vehicle Advisor API (no fakes in the server).

Usage (from the repository root, with the backend virtual environment):
    set SMOKE_E001_PASSWORD=...   (also SMOKE_E005_PASSWORD, SMOKE_REP01_PASSWORD)
    src/backend/.venv/Scripts/python.exe scripts/smoke.py --base-url http://127.0.0.1:8000

The server must run with `STRAUSS_WORKER_IN_PROCESS=true` for steps 5-6. Passwords are read
from the environment only; tokens and amounts other than the checked reference values are
never printed. Exit code 0 when every step passes.

Steps:
1. `GET /health/ready` is 200.
2. e001 signs in.
3. e001 starts a conversation (a Business case is created with it).
4. Comparison `vehicle:CAR-10` for E-001 shows net "11693" (Agent Spec §8).
   4b. Comparison `waiver` for E-005 shows net "17806" (allowance from the occupation table).
5. A representative question for CAR-10, then an employee message about CAR-10: the agent
   replies (the option waits for the representative; the conversation continues).
6. rep01 claims the request and answers it; the employee's conversation receives the
   representative continuation.
"""

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

API = "/api/v1"
MONTH = "2026-10"
E001_ATTO3 = "11693"
E005_WAIVER = "17806"


class SmokeFailure(Exception):
    pass


@dataclass
class Smoke:
    client: httpx.AsyncClient
    passwords: Mapping[str, str]
    poll_timeout: float = 90.0
    results: list[tuple[str, bool, str]] = field(default_factory=list)

    async def step(self, name: str, action: Callable[[], Awaitable[str]]) -> None:
        try:
            detail = await action()
        except Exception as error:  # noqa: BLE001 - every failure becomes a FAIL line
            self.results.append((name, False, f"{type(error).__name__}: {error}"[:300]))
            raise SmokeFailure(name) from None
        self.results.append((name, True, detail))

    async def login(self, name: str) -> dict[str, str]:
        password = self.passwords.get(name)
        if not password:
            raise SmokeFailure(f"SMOKE_{name.upper()}_PASSWORD is not set")
        response = await self.client.post(
            f"{API}/auth/login", json={"login": name, "password": password}
        )
        _expect(response, 200)
        return {"Authorization": f"Bearer {response.json()['accessToken']}"}

    async def conversation(self, headers: dict[str, str]) -> dict[str, Any]:
        response = await self.client.post(
            f"{API}/conversations",
            json={"clientRequestId": str(uuid4()), "calculationMonth": MONTH},
            headers=headers,
        )
        _expect(response, 200)
        body: dict[str, Any] = response.json()
        if not body.get("caseId"):
            raise SmokeFailure("the conversation has no Business case")
        return body

    async def case(self, headers: dict[str, str], case_id: str) -> dict[str, Any]:
        response = await self.client.get(f"{API}/cases/{case_id}", headers=headers)
        _expect(response, 200)
        data: dict[str, Any] = response.json()["data"]
        return data

    async def compare(self, headers: dict[str, str], case_id: str, option: str) -> str:
        case = await self.case(headers, case_id)
        response = await self.client.post(
            f"{API}/cases/{case_id}/comparisons",
            json={
                "selection": {"mode": "explicit", "optionKeys": [option]},
                "includeCurrent": False,
                "kind": "regular",
                "expectedCaseVersion": case["caseVersion"],
                "expectedProfileVersion": case["profile"]["profileVersion"],
                "operationId": str(uuid4()),
            },
            headers=headers,
        )
        _expect(response, 200)
        [alternative] = response.json()["data"]["alternatives"]
        net = alternative.get("net") or {}
        return str(net.get("display"))

    async def poll(
        self, headers: dict[str, str], conversation: str, after: int, kinds: set[str] | None
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            response = await self.client.get(
                f"{API}/conversations/{conversation}/messages",
                params={"afterSequence": after},
                headers=headers,
            )
            _expect(response, 200)
            for message in response.json()["messages"]:
                if message["role"] == "assistant" and (kinds is None or message["kind"] in kinds):
                    found: dict[str, Any] = message
                    return found
            await asyncio.sleep(1.0)
        raise SmokeFailure(f"no assistant message {sorted(kinds or [])} within the timeout")

    async def run(self) -> bool:
        state: dict[str, Any] = {}

        async def ready() -> str:
            response = await self.client.get(f"{API}/health/ready")
            _expect(response, 200)
            return "ready"

        async def login_e001() -> str:
            state["e001"] = await self.login("e001")
            return "signed in"

        async def start() -> str:
            state["compare"] = await self.conversation(state["e001"])
            return "conversation and case created"

        async def atto3() -> str:
            shown = await self.compare(state["e001"], state["compare"]["caseId"], "vehicle:CAR-10")
            if shown != E001_ATTO3:
                raise SmokeFailure(f"net {shown!r}, expected {E001_ATTO3!r}")
            return f"net {shown}"

        async def waiver() -> str:
            headers = await self.login("e005")
            conversation = await self.conversation(headers)
            shown = await self.compare(headers, conversation["caseId"], "waiver")
            if shown != E005_WAIVER:
                raise SmokeFailure(f"net {shown!r}, expected {E005_WAIVER!r}")
            return f"net {shown}"

        async def message() -> str:
            conversation = await self.conversation(state["e001"])
            case = await self.case(state["e001"], conversation["caseId"])
            question = await self.client.post(
                f"{API}/cases/{conversation['caseId']}/representative-requests",
                json={
                    "fieldCode": "alternative.participation",
                    "optionKey": "vehicle:CAR-10",
                    "questionText": "מה דמי ההשתתפות העדכניים לרכב ATTO 3?",
                    "idempotencyKey": str(uuid4()),
                    "expectedCaseVersion": case["caseVersion"],
                },
                headers=state["e001"],
            )
            _expect(question, 200)
            state["request"] = question.json()["data"]["requestId"]
            sent = await self.client.post(
                f"{API}/conversations/{conversation['conversationId']}/messages",
                json={"messageId": str(uuid4()), "text": "כמה אקבל נטו עם ATTO 3?"},
                headers=state["e001"],
            )
            _expect(sent, 202)
            state["chat"] = conversation["conversationId"]
            reply = await self.poll(
                state["e001"], state["chat"], sent.json()["sequence"], kinds=None
            )
            state["after"] = reply["sequence"]
            return f"assistant replied ({reply['kind']})"

        async def representative() -> str:
            rep = await self.login("rep01")
            view = await self.client.get(
                f"{API}/representative-requests/{state['request']}", headers=rep
            )
            _expect(view, 200)
            claimed = await self.client.post(
                f"{API}/representative-requests/{state['request']}/claim",
                json={
                    "operationId": str(uuid4()),
                    "expectedRequestVersion": view.json()["data"]["requestVersion"],
                },
                headers=rep,
            )
            _expect(claimed, 200)
            answer = await self.client.post(
                f"{API}/representative-requests/{state['request']}/answers",
                json={
                    "operationId": str(uuid4()),
                    "expectedRequestVersion": view.json()["data"]["requestVersion"],
                    "outcome": "answered",
                    "value": "350",
                    "unit": "ILS",
                },
                headers=rep,
            )
            _expect(answer, 200)
            reply = await self.poll(
                state["e001"], state["chat"], state["after"], kinds={"representative_answer"}
            )
            return f"continuation received ({reply['kind']})"

        steps: list[tuple[str, Callable[[], Awaitable[str]]]] = [
            ("1 ready", ready),
            ("2 login e001", login_e001),
            ("3 conversation", start),
            ("4 compare CAR-10 (E-001)", atto3),
            ("4b compare waiver (E-005)", waiver),
            ("5 question + message + reply", message),
            ("6 representative answer + continuation", representative),
        ]
        for name, action in steps:
            try:
                await self.step(name, action)
            except SmokeFailure:
                return False
        return True

    def report(self) -> str:
        return "\n".join(
            f"{'PASS' if ok else 'FAIL'}  {name}: {detail}" for name, ok, detail in self.results
        )


def _expect(response: httpx.Response, status: int) -> None:
    if response.status_code != status:
        code = ""
        if response.headers.get("content-type", "").startswith("application/problem+json"):
            code = str(response.json().get("code", ""))
        raise SmokeFailure(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code} {code}".strip()
        )


def passwords_from_environment(environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    return {
        login: environ[f"SMOKE_{login.upper()}_PASSWORD"]
        for login in ("e001", "e005", "rep01")
        if environ.get(f"SMOKE_{login.upper()}_PASSWORD")
    }


async def _main(base_url: str, timeout: float) -> int:
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        smoke = Smoke(client, passwords_from_environment(), poll_timeout=timeout)
        ok = await smoke.run()
    print(smoke.report())
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Strauss Vehicle Advisor smoke check")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--poll-timeout", type=float, default=90.0)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return asyncio.run(_main(args.base_url, args.poll_timeout))


if __name__ == "__main__":
    raise SystemExit(main())
