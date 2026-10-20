"""PayslipImportService: upload, review, explicit approval and the in-memory demo store."""

import asyncio
import logging
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.ai.application.payslip.errors import PayslipError, PayslipErrorCode
from app.ai.application.payslip.extraction import AiFieldMapping, PayslipInterpretation
from app.ai.application.payslip.models import (
    ApproveCommand,
    Correction,
    DocumentIssue,
    DraftStatus,
    ExtractionMode,
    FieldStatus,
    PayslipDraft,
)
from app.ai.application.payslip.ports import PayslipImportConfig
from app.ai.application.payslip.service import PayslipImportService
from app.ai.application.ports.llm import LlmOutcome, LlmTaskKind
from app.ai.infrastructure.payslip.memory_store import InMemoryDraftStore
from app.ai.infrastructure.payslip.pdf_reader import PypdfTextReader
from app.contracts.common import ErrorCode, FieldPath, UpdateMode
from app.contracts.tool_context import ActorRef
from tests.fakes.ai.fake_llm import ScriptedLlm, failure, ok
from tests.fakes.payslip import (
    FakeBusiness,
    FakeConversations,
    ManualClock,
    employee,
    fixture,
    representative,
)

F = FieldPath
ALL = (
    F.BASE_GROSS,
    F.COMMISSIONS_GROSS,
    F.BONUS_GROSS,
    F.PENSION_EMPLOYEE_RATE,
    F.STUDY_FUND_EMPLOYEE_RATE,
    F.CREDIT_POINTS,
)


class Harness:
    def __init__(self, *, llm_enabled: bool = False) -> None:
        self.clock = ManualClock()
        self.store = InMemoryDraftStore()
        self.conversations = FakeConversations()
        self.business = FakeBusiness()
        self.llm = ScriptedLlm()
        self.config = PayslipImportConfig(external_llm_enabled=llm_enabled)
        self.service = self._service()
        self.actor = employee()
        self.conversation_id = self.conversations.open(self.actor)

    def _service(self) -> PayslipImportService:
        return PayslipImportService(
            reader=PypdfTextReader(),
            store=self.store,
            conversations=self.conversations,
            business=self.business,  # type: ignore[arg-type]
            clock=self.clock,
            llm=self.llm,
            config=self.config,
        )

    def restart(self) -> None:
        """A process restart: the in-memory store starts empty."""
        self.store = InMemoryDraftStore()
        self.service = self._service()

    async def upload(
        self, name: str = "basic_he.pdf", actor: ActorRef | None = None
    ) -> PayslipDraft:
        return await self.service.upload(actor or self.actor, self.conversation_id, fixture(name))

    def command(
        self, draft: PayslipDraft, *fields: FieldPath, op: UUID | None = None
    ) -> ApproveCommand:
        return ApproveCommand(
            expected_draft_version=draft.version,
            expected_profile_version=self.business.profile_version,
            operation_id=op or uuid4(),
            fields=fields or ALL,
        )


def _status(draft: PayslipDraft, path: FieldPath) -> FieldStatus:
    field = draft.field_for(path)
    assert field is not None
    return field.status


def _code(raised: pytest.ExceptionInfo[PayslipError]) -> PayslipErrorCode:
    return raised.value.code


# --- upload --------------------------------------------------------------------------------


async def test_upload_creates_review_draft_without_touching_the_profile() -> None:
    h = Harness()
    draft = await h.upload()
    assert draft.status is DraftStatus.REVIEW and draft.version == 1
    assert draft.mode is ExtractionMode.RULES_ONLY
    assert draft.document_issues == (DocumentIssue.AI_DISABLED,)
    assert {f.status for f in draft.fields} == {FieldStatus.NEEDS_REVIEW}
    assert h.llm.tasks == []  # external LLM is off by default
    assert h.business.updates == [] and h.business.context_calls == 0


async def test_ai_assisted_upload_extracts_confirmed_fields() -> None:
    h = Harness(llm_enabled=True)
    mappings = [
        AiFieldMapping(
            field="payroll.baseGross",
            candidate_id="c1",
            value_kind="amount",
            value_index=0,
            party="employee",
            period="monthly",
            confidence="high",
        )
    ]
    h.llm.script(LlmTaskKind.INTERPRET, ok(PayslipInterpretation(mappings=mappings)))
    draft = await h.upload()
    assert draft.mode is ExtractionMode.AI_ASSISTED
    base = draft.field_for(F.BASE_GROSS)
    assert base is not None and base.status is FieldStatus.EXTRACTED
    assert base.value == Decimal("18500.00")
    assert _status(draft, F.BONUS_GROSS) is FieldStatus.NEEDS_REVIEW
    (task,) = h.llm.calls(LlmTaskKind.INTERPRET)
    assert "000000018" not in task.messages[0].text and "1234567" not in task.messages[0].text


async def test_llm_failure_falls_back_to_rules_only() -> None:
    h = Harness(llm_enabled=True)
    h.llm.script(LlmTaskKind.INTERPRET, failure(LlmOutcome.RATE_LIMITED))
    draft = await h.upload()
    assert draft.mode is ExtractionMode.RULES_ONLY
    assert draft.document_issues == (DocumentIssue.AI_UNAVAILABLE,)
    assert {f.status for f in draft.fields} == {FieldStatus.NEEDS_REVIEW}


async def test_injection_document_is_never_sent_to_the_llm() -> None:
    h = Harness(llm_enabled=True)
    draft = await h.upload("injection.pdf")
    assert h.llm.tasks == []
    assert draft.document_issues == (DocumentIssue.SUSPICIOUS_CONTENT,)
    base = draft.field_for(F.BASE_GROSS)
    assert base is not None and base.value == Decimal("12000.00")
    assert base.status is FieldStatus.NEEDS_REVIEW
    assert _status(draft, F.BONUS_GROSS) is FieldStatus.MISSING


async def test_model_reporting_instructions_discards_its_interpretation() -> None:
    h = Harness(llm_enabled=True)
    h.llm.script(LlmTaskKind.INTERPRET, ok(PayslipInterpretation(instructions_in_document=True)))
    draft = await h.upload()
    assert draft.mode is ExtractionMode.RULES_ONLY
    assert draft.document_issues == (DocumentIssue.SUSPICIOUS_CONTENT,)


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", PayslipErrorCode.EMPTY_FILE),
        (b"not a pdf", PayslipErrorCode.NOT_A_PDF),
        (b"%PDF-1.4" + b" " * (3 * 1024 * 1024), PayslipErrorCode.FILE_TOO_LARGE),
    ],
    ids=["empty", "not_pdf", "too_large"],
)
async def test_invalid_uploads_create_no_draft(data: bytes, code: PayslipErrorCode) -> None:
    h = Harness()
    with pytest.raises(PayslipError) as raised:
        await h.service.upload(h.actor, h.conversation_id, data)
    assert _code(raised) is code


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("encrypted.pdf", PayslipErrorCode.ENCRYPTED_PDF),
        ("scanned.pdf", PayslipErrorCode.SCANNED_UNSUPPORTED),
        ("too_many_pages.pdf", PayslipErrorCode.TOO_MANY_PAGES),
    ],
)
async def test_unsupported_documents_are_rejected(name: str, code: PayslipErrorCode) -> None:
    h = Harness()
    with pytest.raises(PayslipError) as raised:
        await h.upload(name)
    assert _code(raised) is code


async def test_upload_requires_the_callers_own_conversation_and_employee_role() -> None:
    h = Harness()
    with pytest.raises(PayslipError) as raised:
        await h.upload(actor=employee())
    assert _code(raised) is PayslipErrorCode.CONVERSATION_NOT_FOUND
    with pytest.raises(PayslipError) as raised:
        await h.upload(actor=representative())
    assert _code(raised) is PayslipErrorCode.FORBIDDEN


async def test_extraction_timeout_is_reported() -> None:
    h = Harness()
    h.config = replace(h.config, extraction_timeout_seconds=0.01)
    h.service = h._service()

    class SlowReader(PypdfTextReader):
        def read(self, data: bytes, config: PayslipImportConfig):  # type: ignore[no-untyped-def]
            import time

            time.sleep(0.2)
            return super().read(data, config)

    h.service._reader = SlowReader()
    with pytest.raises(PayslipError) as raised:
        await h.upload()
    assert _code(raised) is PayslipErrorCode.EXTRACTION_TIMEOUT


# --- review and corrections -------------------------------------------------------------


async def test_drafts_of_other_employees_are_not_found() -> None:
    h = Harness()
    draft = await h.upload()
    stranger = employee()
    for call in (
        h.service.get(stranger, draft.draft_id),
        h.service.correct(
            stranger,
            draft.draft_id,
            expected_draft_version=1,
            corrections=[Correction(F.BASE_GROSS, "1")],
        ),
        h.service.approve(stranger, draft.draft_id, h.command(draft)),
        h.service.cancel(stranger, draft.draft_id),
    ):
        with pytest.raises(PayslipError) as raised:
            await call
        assert _code(raised) is PayslipErrorCode.DRAFT_NOT_FOUND
    assert h.business.updates == []
    assert (await h.service.get(h.actor, draft.draft_id)).draft_id == draft.draft_id


async def test_corrections_are_validated_by_the_business_contract() -> None:
    h = Harness()
    draft = await h.upload("ambiguous.pdf")
    for value in ("6", "-1", "abc", "1e3"):
        with pytest.raises(PayslipError) as raised:
            await h.service.correct(
                h.actor,
                draft.draft_id,
                expected_draft_version=1,
                corrections=[Correction(F.PENSION_EMPLOYEE_RATE, value)],
            )
        assert _code(raised) is PayslipErrorCode.INVALID_FIELD_VALUE
        assert raised.value.field == F.PENSION_EMPLOYEE_RATE.value
    corrected = await h.service.correct(
        h.actor,
        draft.draft_id,
        expected_draft_version=1,
        corrections=[
            Correction(F.PENSION_EMPLOYEE_RATE, "0.06"),
            Correction(F.BASE_GROSS, "15000.00"),
        ],
    )
    assert corrected.version == 2
    pension = corrected.field_for(F.PENSION_EMPLOYEE_RATE)
    assert pension is not None and pension.status is FieldStatus.CORRECTED
    assert pension.value == Decimal("0.06")
    with pytest.raises(PayslipError) as raised:  # stale draft version
        await h.service.correct(
            h.actor,
            draft.draft_id,
            expected_draft_version=1,
            corrections=[Correction(F.BONUS_GROSS, "1000")],
        )
    assert _code(raised) is PayslipErrorCode.DRAFT_VERSION_CONFLICT


# --- approval ------------------------------------------------------------------------------


async def test_approval_sends_one_version_checked_actual_update() -> None:
    h = Harness()
    draft = await h.upload()
    op = uuid4()
    result = await h.service.approve(h.actor, draft.draft_id, h.command(draft, op=op))
    ((ctx, request),) = h.business.updates
    assert ctx.actor == h.actor and ctx.conversation_id == h.conversation_id
    assert ctx.case_id == h.conversations.cases[h.conversation_id]
    assert request.mode is UpdateMode.ACTUAL_UPDATE
    assert request.expected_profile_version == 2 and request.expected_case_version == 4
    assert request.operation_id == op
    assert {c.field: c.value for c in request.changes} == {  # type: ignore[union-attr]
        F.BASE_GROSS: "18500.00",
        F.COMMISSIONS_GROSS: "1200.00",
        F.BONUS_GROSS: "2000.00",
        F.PENSION_EMPLOYEE_RATE: "0.06",
        F.STUDY_FUND_EMPLOYEE_RATE: "0.025",
        F.CREDIT_POINTS: "2.25",
    }
    assert result.new_profile_version == 3
    stored = await h.service.get(h.actor, draft.draft_id)
    assert stored.status is DraftStatus.APPLIED
    assert all(f.value is None and f.evidence == () for f in stored.fields)  # values purged


async def test_only_selected_fields_are_sent() -> None:
    h = Harness()
    draft = await h.upload()
    await h.service.approve(h.actor, draft.draft_id, h.command(draft, F.BASE_GROSS))
    ((_, request),) = h.business.updates
    assert [c.field for c in request.changes] == [F.BASE_GROSS]


async def test_duplicate_approval_replays_without_a_second_effect() -> None:
    h = Harness()
    draft = await h.upload()
    command = h.command(draft)
    first = await h.service.approve(h.actor, draft.draft_id, command)
    again = await h.service.approve(h.actor, draft.draft_id, command)
    assert again == first and len(h.business.updates) == 1
    with pytest.raises(PayslipError) as raised:
        await h.service.approve(h.actor, draft.draft_id, replace(command, operation_id=uuid4()))
    assert _code(raised) is PayslipErrorCode.DRAFT_ALREADY_APPLIED
    with pytest.raises(PayslipError) as raised:
        await h.service.approve(h.actor, draft.draft_id, replace(command, fields=(F.BASE_GROSS,)))
    assert _code(raised) is PayslipErrorCode.OPERATION_PAYLOAD_MISMATCH
    assert len(h.business.updates) == 1


async def test_concurrent_approvals_and_corrections_have_a_single_effect() -> None:
    h = Harness()
    h.business.delay = 0.05
    draft = await h.upload()
    command = h.command(draft)
    results = await asyncio.gather(
        h.service.approve(h.actor, draft.draft_id, command),
        h.service.approve(h.actor, draft.draft_id, command),
        h.service.correct(
            h.actor,
            draft.draft_id,
            expected_draft_version=1,
            corrections=[Correction(F.BASE_GROSS, "1")],
        ),
        return_exceptions=True,
    )
    assert results[0] == results[1]
    assert isinstance(results[2], PayslipError)
    assert results[2].code is PayslipErrorCode.DRAFT_ALREADY_APPLIED
    assert len(h.business.updates) == 1


async def test_profile_version_conflict_keeps_the_draft_for_retry() -> None:
    h = Harness()
    draft = await h.upload()
    stale = replace(h.command(draft), expected_profile_version=1)
    with pytest.raises(PayslipError) as raised:
        await h.service.approve(h.actor, draft.draft_id, stale)
    assert _code(raised) is PayslipErrorCode.PROFILE_VERSION_CONFLICT
    kept = await h.service.get(h.actor, draft.draft_id)
    assert kept.status is DraftStatus.REVIEW
    assert kept.last_error == PayslipErrorCode.PROFILE_VERSION_CONFLICT.value
    assert kept.field_for(F.BASE_GROSS).value == Decimal("18500.00")  # type: ignore[union-attr]
    await h.service.approve(h.actor, draft.draft_id, h.command(kept))
    assert h.business.profile_version == 3


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ErrorCode.INVALID_BUSINESS_INPUT, PayslipErrorCode.BUSINESS_REJECTED),
        (ErrorCode.INPUT_OUT_OF_SUPPORTED_RANGE, PayslipErrorCode.BUSINESS_REJECTED),
        (ErrorCode.INFRA_UNAVAILABLE, PayslipErrorCode.BUSINESS_UNAVAILABLE),
        (ErrorCode.FORBIDDEN, PayslipErrorCode.CONVERSATION_NOT_FOUND),
    ],
)
async def test_business_rejections_are_mapped_and_draft_stays_reviewable(
    error: ErrorCode, code: PayslipErrorCode
) -> None:
    h = Harness()
    h.business.update_error = error
    draft = await h.upload()
    with pytest.raises(PayslipError) as raised:
        await h.service.approve(h.actor, draft.draft_id, h.command(draft))
    assert _code(raised) is code
    assert (await h.service.get(h.actor, draft.draft_id)).status is DraftStatus.REVIEW


async def test_unresolved_fields_cannot_be_approved_and_nothing_is_sent() -> None:
    h = Harness()
    draft = await h.upload("ambiguous.pdf")
    for path, code in (
        (F.BASE_GROSS, PayslipErrorCode.FIELD_NOT_APPROVABLE),  # conflict
        (F.CREDIT_POINTS, PayslipErrorCode.FIELD_NOT_APPROVABLE),  # missing
        (F.STUDY_FUND_EMPLOYEE_RATE, PayslipErrorCode.FIELD_NOT_APPROVABLE),  # rejected
    ):
        with pytest.raises(PayslipError) as raised:
            await h.service.approve(
                h.actor, draft.draft_id, h.command(draft, F.COMMISSIONS_GROSS, path)
            )
        assert _code(raised) is code
    assert h.business.updates == []
    with pytest.raises(PayslipError) as raised:
        await h.service.approve(
            h.actor, draft.draft_id, h.command(draft, F.BASE_GROSS, F.BASE_GROSS)
        )
    assert _code(raised) is PayslipErrorCode.INVALID_SELECTION


async def test_corrected_conflict_can_be_approved() -> None:
    h = Harness()
    draft = await h.upload("ambiguous.pdf")
    draft = await h.service.correct(
        h.actor,
        draft.draft_id,
        expected_draft_version=1,
        corrections=[Correction(F.BASE_GROSS, "15000.00"), Correction(F.BONUS_GROSS, "2500")],
    )
    await h.service.approve(h.actor, draft.draft_id, h.command(draft, F.BASE_GROSS, F.BONUS_GROSS))
    ((_, request),) = h.business.updates
    assert {c.field: c.value for c in request.changes} == {  # type: ignore[union-attr]
        F.BASE_GROSS: "15000.00",
        F.BONUS_GROSS: "2500",
    }


async def test_stale_draft_version_blocks_approval() -> None:
    h = Harness()
    draft = await h.upload()
    await h.service.correct(
        h.actor,
        draft.draft_id,
        expected_draft_version=1,
        corrections=[Correction(F.BASE_GROSS, "18000")],
    )
    with pytest.raises(PayslipError) as raised:
        await h.service.approve(h.actor, draft.draft_id, h.command(draft))
    assert _code(raised) is PayslipErrorCode.DRAFT_VERSION_CONFLICT
    assert h.business.updates == []


# --- in-memory store lifecycle -------------------------------------------------------------


async def test_expired_drafts_cannot_be_read_or_approved() -> None:
    h = Harness()
    draft = await h.upload()
    h.clock.advance(h.config.draft_ttl_seconds)
    with pytest.raises(PayslipError) as raised:
        await h.service.approve(h.actor, draft.draft_id, h.command(draft))
    assert _code(raised) is PayslipErrorCode.DRAFT_EXPIRED
    with pytest.raises(PayslipError) as raised:
        await h.service.get(h.actor, draft.draft_id)
    assert _code(raised) is PayslipErrorCode.DRAFT_NOT_FOUND
    assert h.business.updates == []


async def test_restart_loses_drafts_and_never_updates_the_profile() -> None:
    h = Harness()
    draft = await h.upload()
    h.restart()
    with pytest.raises(PayslipError) as raised:
        await h.service.approve(h.actor, draft.draft_id, h.command(draft))
    assert _code(raised) is PayslipErrorCode.DRAFT_NOT_FOUND
    assert h.business.updates == []


async def test_draft_limits_per_employee_and_cancellation() -> None:
    h = Harness()
    drafts = [await h.upload() for _ in range(h.config.max_drafts_per_owner)]
    with pytest.raises(PayslipError) as raised:
        await h.upload()
    assert _code(raised) is PayslipErrorCode.TOO_MANY_DRAFTS
    await h.service.cancel(h.actor, drafts[0].draft_id)
    await h.upload()
    with pytest.raises(PayslipError) as raised:
        await h.service.get(h.actor, drafts[0].draft_id)
    assert _code(raised) is PayslipErrorCode.DRAFT_NOT_FOUND


async def test_expired_drafts_free_capacity() -> None:
    h = Harness()
    for _ in range(h.config.max_drafts_per_owner):
        await h.upload()
    h.clock.advance(h.config.draft_ttl_seconds + 1)
    await h.upload()


# --- privacy -------------------------------------------------------------------------------


async def test_logs_contain_no_salary_values_labels_or_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    h = Harness(llm_enabled=True)
    h.llm.script(LlmTaskKind.INTERPRET, ok(PayslipInterpretation()))
    draft = await h.upload()
    await h.service.approve(h.actor, draft.draft_id, h.command(draft))
    with pytest.raises(PayslipError):
        await h.upload("scanned.pdf")
    text = caplog.text
    assert "payslip draft created" in text and "payslip draft applied" in text
    for secret in ("18500.00", "18,500", "1200.00", "0.06", "2.25", "יסוד", "000000018", "ישראלי"):
        assert secret not in text
