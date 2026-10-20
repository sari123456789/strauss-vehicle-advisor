"""Payslip import against the REAL Business Application and conversation ownership (marker `db`).

The service gets the real `ConversationService` (trusted `ToolContext`) and the real
`BusinessToolsFacade` only through the shared ports; no Business table is touched directly.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.ai.application.payslip.errors import PayslipError, PayslipErrorCode
from app.ai.application.payslip.models import PAYSLIP_FIELDS, ApproveCommand
from app.ai.application.payslip.service import PayslipImportService
from app.ai.infrastructure.payslip.memory_store import InMemoryDraftStore
from app.ai.infrastructure.payslip.pdf_reader import PypdfTextReader
from app.contracts.common import EnvelopeStatus, FieldStatus
from tests.fakes.payslip import fixture
from tests.integration.ai_business.conftest import Stack, trace

MONTH = "2026-10"


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _service(stack: Stack) -> PayslipImportService:
    return PayslipImportService(
        reader=PypdfTextReader(),
        store=InMemoryDraftStore(),
        conversations=stack.conversations,
        business=stack.business.tools,
        clock=_Clock(),
    )


async def test_approved_payslip_updates_the_shared_profile_once(stack: Stack) -> None:
    actor = await stack.new_actor(with_profile=False)
    view = await stack.conversations.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    service = _service(stack)
    draft = await service.upload(actor, view.conversation_id, fixture("basic_he.pdf"))
    binding = await stack.conversations.turn_binding(actor, view.conversation_id, trace_id=trace())

    before = await stack.business.tools.get_conversation_context(binding.tool_context)
    assert before.status is EnvelopeStatus.OK and before.data is not None
    assert before.data.profile.base_gross.status is FieldStatus.MISSING  # nothing applied yet

    command = ApproveCommand(
        expected_draft_version=draft.version,
        expected_profile_version=before.data.latest_profile_version,
        operation_id=uuid4(),
        fields=PAYSLIP_FIELDS,
    )
    result = await service.approve(actor, draft.draft_id, command, trace_id=trace())
    assert set(result.accepted_fields) == set(PAYSLIP_FIELDS)
    assert result.new_profile_version == before.data.latest_profile_version + 1
    assert await service.approve(actor, draft.draft_id, command) == result  # replay

    after = await stack.business.tools.get_conversation_context(binding.tool_context)
    assert after.data is not None
    profile = after.data.profile
    assert profile.profile_version == result.new_profile_version
    assert profile.base_gross.value == "18500.00"
    assert profile.commissions_gross.value == "1200.00"
    assert profile.bonus_gross.value == "2000.00"
    assert profile.pension_employee_rate.value == "0.06"
    assert profile.study_fund_employee_rate.value == "0.025"
    assert profile.credit_points.value == "2.25"


async def test_stale_profile_version_and_foreign_conversation_are_rejected(stack: Stack) -> None:
    actor = await stack.new_actor()
    view = await stack.conversations.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    service = _service(stack)
    draft = await service.upload(actor, view.conversation_id, fixture("basic_he.pdf"))

    stale = ApproveCommand(
        expected_draft_version=draft.version,
        expected_profile_version=0,
        operation_id=uuid4(),
        fields=PAYSLIP_FIELDS,
    )
    with pytest.raises(PayslipError) as raised:
        await service.approve(actor, draft.draft_id, stale)
    assert raised.value.code is PayslipErrorCode.PROFILE_VERSION_CONFLICT

    stranger = await stack.new_actor()
    with pytest.raises(PayslipError) as raised:
        await service.upload(stranger, view.conversation_id, fixture("basic_he.pdf"))
    assert raised.value.code is PayslipErrorCode.CONVERSATION_NOT_FOUND
