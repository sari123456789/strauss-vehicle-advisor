"""Payslip-import HTTP routes - AI-owned, STANDALONE (not yet included by `app.main`).

Integration handoff (shared composition, see docs/proposals/payslip-import.md): the lifespan
stores a `PayslipImportService` as `app.state.ai_payslip_imports` and `app.main` includes
`router` under `/api/v1`. The upload takes the raw PDF body (`Content-Type: application/pdf`),
so no multipart dependency is needed.

The actor comes only from the verified bearer token (`Actor`); the service derives the trusted
`ToolContext` from the actor and the conversation. Another employee's draft is a 404. Errors are
Problem Details through `BusinessRoute`; `details.payslipCode` lets the client choose the
Hebrew message. Submitted values are never echoed in errors or logs.

All DTOs below are PROVISIONAL HTTP DTOs awaiting joint approval.
"""

from datetime import datetime
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Path, Request, Response
from pydantic import Field

from app.ai.application.payslip.drafting import contract_value
from app.ai.application.payslip.errors import PayslipError, PayslipErrorCode
from app.ai.application.payslip.models import ApproveCommand, Correction, PayslipDraft
from app.ai.application.payslip.service import PayslipImportService
from app.api.business.dependencies import Actor
from app.api.business.problem import ApiProblem
from app.api.business.route import BusinessRoute, request_trace_id
from app.contracts.case_inputs import UpdateInputsResult
from app.contracts.common import ContractModel, ErrorCode, FieldPath

router = APIRouter(tags=["payslip-imports"], route_class=BusinessRoute)

PDF_MEDIA_TYPE = "application/pdf"
ConversationId = Annotated[UUID, Path(alias="conversationId")]
DraftId = Annotated[UUID, Path(alias="draftId")]


# --- DTOs (PROVISIONAL) -------------------------------------------------------------------


class EvidenceView(ContractModel):
    page: int
    line_index: int
    text: str


class DraftFieldView(ContractModel):
    field: FieldPath
    status: str
    """extracted, needs_review, missing, conflict, rejected or corrected."""
    value: str | None
    """Decimal string in profile units (rates as fractions: "0.06" = 6%)."""
    confidence: str
    evidence: list[EvidenceView]
    alternatives: list[str]
    issues: list[str]


class DraftView(ContractModel):
    draft_id: UUID
    conversation_id: UUID
    version: int
    status: str
    """review, applying or applied."""
    mode: str
    """ai_assisted or rules_only."""
    page_count: int
    created_at: datetime
    expires_at: datetime
    fields: list[DraftFieldView]
    document_issues: list[str]
    last_error: str | None


class CorrectionItem(ContractModel):
    field: FieldPath
    value: str = Field(min_length=1, max_length=40)


class CorrectDraftRequest(ContractModel):
    expected_draft_version: int = Field(ge=1)
    corrections: list[CorrectionItem] = Field(min_length=1, max_length=16)


class ApproveDraftRequest(ContractModel):
    expected_draft_version: int = Field(ge=1)
    expected_profile_version: int = Field(ge=0)
    operation_id: UUID
    """Client-generated idempotency key; repeating it never applies the draft twice."""
    fields: list[FieldPath] = Field(min_length=1, max_length=16)


# --- helpers ------------------------------------------------------------------------------


def payslip_service(request: Request) -> PayslipImportService:
    service = getattr(request.app.state, "ai_payslip_imports", None)
    if not isinstance(service, PayslipImportService):
        raise ApiProblem(ErrorCode.INFRA_UNAVAILABLE, details={"reason": "not_configured"})
    return service


def _problem(error: PayslipError) -> NoReturn:
    details = {"payslipCode": error.code.value}
    if error.field is not None:
        details["field"] = error.field
    raise ApiProblem(error.error_code, details=details) from None


def _view(draft: PayslipDraft) -> DraftView:
    return DraftView(
        draft_id=draft.draft_id,
        conversation_id=draft.conversation_id,
        version=draft.version,
        status=draft.status.value,
        mode=draft.mode.value,
        page_count=draft.page_count,
        created_at=draft.created_at,
        expires_at=draft.expires_at,
        fields=[
            DraftFieldView(
                field=f.field,
                status=f.status.value,
                value=None if f.value is None else contract_value(f.field, f.value),
                confidence=f.confidence.value,
                evidence=[
                    EvidenceView(page=e.page, line_index=e.line_index, text=e.text)
                    for e in f.evidence
                ],
                alternatives=[contract_value(f.field, v) for v in f.alternatives],
                issues=[i.value for i in f.issues],
            )
            for f in draft.fields
        ],
        document_issues=[i.value for i in draft.document_issues],
        last_error=draft.last_error,
    )


async def _read_pdf_body(request: Request, max_bytes: int) -> bytes:
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != PDF_MEDIA_TYPE:
        _problem(PayslipError(PayslipErrorCode.NOT_A_PDF))
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        _problem(PayslipError(PayslipErrorCode.FILE_TOO_LARGE))
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            _problem(PayslipError(PayslipErrorCode.FILE_TOO_LARGE))
    return bytes(body)


# --- routes -------------------------------------------------------------------------------


@router.post(
    "/conversations/{conversationId}/payslip-imports",
    status_code=201,
    response_model=DraftView,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {PDF_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def upload_payslip(
    request: Request, actor: Actor, conversation_id: ConversationId
) -> DraftView:
    service = payslip_service(request)
    data = await _read_pdf_body(request, service.config.max_bytes)
    try:
        draft = await service.upload(
            actor, conversation_id, data, trace_id=request_trace_id(request)
        )
    except PayslipError as error:
        _problem(error)
    return _view(draft)


@router.get("/payslip-imports/{draftId}", response_model=DraftView)
async def get_payslip_draft(request: Request, actor: Actor, draft_id: DraftId) -> DraftView:
    try:
        return _view(await payslip_service(request).get(actor, draft_id))
    except PayslipError as error:
        _problem(error)


@router.patch("/payslip-imports/{draftId}", response_model=DraftView)
async def correct_payslip_draft(
    request: Request, actor: Actor, draft_id: DraftId, body: CorrectDraftRequest
) -> DraftView:
    try:
        draft = await payslip_service(request).correct(
            actor,
            draft_id,
            expected_draft_version=body.expected_draft_version,
            corrections=[Correction(field=c.field, value=c.value) for c in body.corrections],
        )
    except PayslipError as error:
        _problem(error)
    return _view(draft)


@router.post("/payslip-imports/{draftId}/approve", response_model=UpdateInputsResult)
async def approve_payslip_draft(
    request: Request, actor: Actor, draft_id: DraftId, body: ApproveDraftRequest
) -> UpdateInputsResult:
    command = ApproveCommand(
        expected_draft_version=body.expected_draft_version,
        expected_profile_version=body.expected_profile_version,
        operation_id=body.operation_id,
        fields=tuple(body.fields),
    )
    try:
        return await payslip_service(request).approve(
            actor, draft_id, command, trace_id=request_trace_id(request)
        )
    except PayslipError as error:
        _problem(error)


@router.delete("/payslip-imports/{draftId}", status_code=204)
async def cancel_payslip_draft(request: Request, actor: Actor, draft_id: DraftId) -> Response:
    try:
        await payslip_service(request).cancel(actor, draft_id)
    except PayslipError as error:
        _problem(error)
    return Response(status_code=204)
