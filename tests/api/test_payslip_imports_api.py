"""Standalone payslip-import router on a minimal FastAPI app (not yet wired into app.main)."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.application.payslip.service import PayslipImportService
from app.ai.infrastructure.payslip.memory_store import InMemoryDraftStore
from app.ai.infrastructure.payslip.pdf_reader import PypdfTextReader
from app.api.business.dependencies import current_actor
from app.api.routers.payslip_imports import router
from app.contracts.tool_context import ActorRef
from tests.fakes.payslip import (
    FakeBusiness,
    FakeConversations,
    ManualClock,
    employee,
    fixture,
)

PDF = {"Content-Type": "application/pdf"}


class Api:
    def __init__(self) -> None:
        self.conversations = FakeConversations()
        self.business = FakeBusiness()
        self.owner = employee()
        self.actor: ActorRef = self.owner
        self.conversation_id = self.conversations.open(self.owner)
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.state.ai_payslip_imports = PayslipImportService(
            reader=PypdfTextReader(),
            store=InMemoryDraftStore(),
            conversations=self.conversations,
            business=self.business,  # type: ignore[arg-type]
            clock=ManualClock(),
        )
        app.dependency_overrides[current_actor] = lambda: self.actor
        self.client = TestClient(app)

    def upload(self, data: bytes, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
        return self.client.post(
            f"/api/v1/conversations/{self.conversation_id}/payslip-imports",
            content=data,
            headers=headers or PDF,
        )


@pytest.fixture
def api() -> Api:
    return Api()


def test_upload_review_correct_and_approve(api: Api) -> None:
    created = api.upload(fixture("ambiguous.pdf"))
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["status"] == "review" and draft["version"] == 1
    fields = {f["field"]: f for f in draft["fields"]}
    # Hourly rate and hours on the base line are not offered as the monthly base.
    assert fields["payroll.baseGross"]["status"] == "needs_review"
    assert fields["payroll.baseGross"]["value"] == "15000.00"
    assert fields["payroll.baseGross"]["alternatives"] == []
    assert fields["payroll.baseGross"]["evidence"][0]["page"] == 1
    assert created.headers["Cache-Control"] == "no-store"

    draft_url = f"/api/v1/payslip-imports/{draft['draftId']}"
    assert api.client.get(draft_url).json()["draftId"] == draft["draftId"]

    patched = api.client.patch(
        draft_url,
        json={
            "expectedDraftVersion": 1,
            "corrections": [{"field": "payroll.baseGross", "value": "15000.00"}],
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["version"] == 2

    approved = api.client.post(
        f"{draft_url}/approve",
        json={
            "expectedDraftVersion": 2,
            "expectedProfileVersion": 2,
            "operationId": str(uuid4()),
            "fields": ["payroll.baseGross"],
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["acceptedFields"] == ["payroll.baseGross"]
    assert approved.json()["newProfileVersion"] == 3
    assert api.client.get(draft_url).json()["status"] == "applied"


def test_rejects_non_pdf_content_type_and_oversized_bodies(api: Api) -> None:
    wrong = api.upload(b"%PDF-1.7", headers={"Content-Type": "text/plain"})
    assert wrong.status_code == 400
    assert wrong.json()["details"]["payslipCode"] == "NOT_A_PDF"
    big = api.upload(b"%PDF-1.7" + b" " * (3 * 1024 * 1024))
    assert big.status_code == 422
    assert big.json()["details"]["payslipCode"] == "FILE_TOO_LARGE"


@pytest.mark.parametrize(
    ("name", "code"),
    [("scanned.pdf", "SCANNED_UNSUPPORTED"), ("encrypted.pdf", "ENCRYPTED_PDF")],
)
def test_unsupported_pdfs_return_problem_details(api: Api, name: str, code: str) -> None:
    response = api.upload(fixture(name))
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["details"] == {"payslipCode": code}


def test_other_employees_get_not_found_without_details(api: Api) -> None:
    draft = api.upload(fixture("basic_he.pdf")).json()
    api.actor = employee()
    response = api.client.get(f"/api/v1/payslip-imports/{draft['draftId']}")
    assert response.status_code == 404
    assert response.json()["details"] == {"payslipCode": "DRAFT_NOT_FOUND"}
    assert "18500" not in response.text
    foreign = api.upload(fixture("basic_he.pdf"))
    assert foreign.status_code == 404
    assert foreign.json()["details"]["payslipCode"] == "CONVERSATION_NOT_FOUND"


def test_invalid_correction_is_not_echoed(api: Api) -> None:
    draft = api.upload(fixture("basic_he.pdf")).json()
    response = api.client.patch(
        f"/api/v1/payslip-imports/{draft['draftId']}",
        json={
            "expectedDraftVersion": 1,
            "corrections": [{"field": "payroll.pensionEmployeeRate", "value": "777"}],
        },
    )
    assert response.status_code == 422
    assert response.json()["details"] == {
        "payslipCode": "INVALID_FIELD_VALUE",
        "field": "payroll.pensionEmployeeRate",
    }
    assert "777" not in response.text


def test_cancel_removes_the_draft(api: Api) -> None:
    draft = api.upload(fixture("basic_he.pdf")).json()
    url = f"/api/v1/payslip-imports/{draft['draftId']}"
    assert api.client.delete(url).status_code == 204
    assert api.client.get(url).status_code == 404


def test_service_not_configured_is_unavailable() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[current_actor] = employee
    response = TestClient(app).get(f"/api/v1/payslip-imports/{uuid4()}")
    assert response.status_code == 503
