"""`PayslipImportService`: upload -> extraction draft -> employee review -> explicit approval.

- Nothing reaches the shared EmployeeProfile before `approve`. Approval sends ONE
  `update_conversation_inputs` request (`mode=actual_update`, `expectedProfileVersion`,
  client `operationId`) through the Business port, so Business re-validates every value,
  enforces ownership and the version check, and applies all fields atomically or none.
- The `ToolContext` is always built by the server from the actor and the draft's own
  conversation (`ConversationBinding`), never taken from the client.
- Another employee's draft is indistinguishable from a missing one.
- Logs carry identifiers, sizes, counts and codes only: no values, labels or document text.
"""

import asyncio
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.ai.application.errors import ConversationError
from app.ai.application.payslip.candidates import build_candidates
from app.ai.application.payslip.drafting import build_fields, contract_value
from app.ai.application.payslip.errors import PayslipError, PayslipErrorCode
from app.ai.application.payslip.extraction import PayslipInterpretation, interpretation_task
from app.ai.application.payslip.models import (
    APPROVABLE,
    PAYSLIP_FIELDS,
    AppliedOperation,
    ApproveCommand,
    Candidate,
    Confidence,
    Correction,
    DocumentIssue,
    DocumentText,
    DraftField,
    DraftStatus,
    ExtractionMode,
    FieldStatus,
    PayslipDraft,
)
from app.ai.application.payslip.ports import (
    Clock,
    ConversationBinding,
    DraftStore,
    PayslipImportConfig,
    PdfTextReader,
)
from app.ai.application.ports.llm import LlmPort
from app.contracts.business_port import BusinessToolsPort
from app.contracts.case_inputs import SetChange, UpdateInputsRequest, UpdateInputsResult
from app.contracts.common import ErrorCode, FieldPath, UpdateMode
from app.contracts.tool_context import ActorRef, Role, ToolContext

logger = logging.getLogger(__name__)

_BUSINESS_ERRORS: dict[ErrorCode, PayslipErrorCode] = {
    ErrorCode.VERSION_CONFLICT: PayslipErrorCode.PROFILE_VERSION_CONFLICT,
    ErrorCode.OPERATION_PAYLOAD_MISMATCH: PayslipErrorCode.OPERATION_PAYLOAD_MISMATCH,
    ErrorCode.NOT_FOUND: PayslipErrorCode.CONVERSATION_NOT_FOUND,
    ErrorCode.FORBIDDEN: PayslipErrorCode.CONVERSATION_NOT_FOUND,
    ErrorCode.UNAUTHENTICATED: PayslipErrorCode.CONVERSATION_NOT_FOUND,
    ErrorCode.INFRA_UNAVAILABLE: PayslipErrorCode.BUSINESS_UNAVAILABLE,
    ErrorCode.RATE_LIMITED: PayslipErrorCode.BUSINESS_UNAVAILABLE,
}


def _require_employee(actor: ActorRef) -> None:
    if Role.EMPLOYEE not in actor.roles:
        raise PayslipError(PayslipErrorCode.FORBIDDEN)


def _request_hash(command: ApproveCommand) -> str:
    payload = {
        "draftVersion": command.expected_draft_version,
        "profileVersion": command.expected_profile_version,
        "fields": [f.value for f in command.fields],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _strip_values(fields: Sequence[DraftField]) -> tuple[DraftField, ...]:
    """After approval only field names and statuses remain (no amounts or evidence)."""
    return tuple(
        DraftField(field=f.field, status=f.status, confidence=f.confidence) for f in fields
    )


class PayslipImportService:
    def __init__(
        self,
        *,
        reader: PdfTextReader,
        store: DraftStore,
        conversations: ConversationBinding,
        business: BusinessToolsPort,
        clock: Clock,
        llm: LlmPort | None = None,
        config: PayslipImportConfig | None = None,
    ) -> None:
        self._reader = reader
        self._store = store
        self._conversations = conversations
        self._business = business
        self._clock = clock
        self._llm = llm
        self.config = config or PayslipImportConfig()

    # --- upload -----------------------------------------------------------------------

    async def upload(
        self, actor: ActorRef, conversation_id: UUID, data: bytes, *, trace_id: str | None = None
    ) -> PayslipDraft:
        _require_employee(actor)
        await self._tool_context(actor, conversation_id, trace_id)
        if not data:
            raise PayslipError(PayslipErrorCode.EMPTY_FILE)
        if len(data) > self.config.max_bytes:
            raise PayslipError(PayslipErrorCode.FILE_TOO_LARGE)
        document = await self._read(data)
        candidates = build_candidates(document)
        interpretation, issues = await self._interpret(candidates)
        now = self._clock.now()
        draft = PayslipDraft(
            draft_id=uuid4(),
            owner_subject_id=actor.subject_id,
            conversation_id=conversation_id,
            version=1,
            status=DraftStatus.REVIEW,
            created_at=now,
            expires_at=now + timedelta(seconds=self.config.draft_ttl_seconds),
            mode=ExtractionMode.AI_ASSISTED
            if interpretation is not None
            else ExtractionMode.RULES_ONLY,
            page_count=document.page_count,
            fields=build_fields(candidates, interpretation),
            document_issues=issues,
        )
        await self._store.add(draft, now=now, config=self.config)
        statuses = Counter(f.status.value for f in draft.fields)
        logger.info(
            "payslip draft created draft_id=%s pages=%d bytes=%d candidates=%d mode=%s "
            "statuses=%s issues=%s",
            draft.draft_id,
            document.page_count,
            document.byte_size,
            len(candidates),
            draft.mode.value,
            dict(sorted(statuses.items())),
            [i.value for i in issues],
        )
        return draft

    async def _read(self, data: bytes) -> DocumentText:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._reader.read, data, self.config),
                timeout=self.config.extraction_timeout_seconds,
            )
        except PayslipError as error:
            logger.info("payslip rejected code=%s bytes=%d", error.code.value, len(data))
            raise
        except TimeoutError:
            logger.warning("payslip extraction timed out bytes=%d", len(data))
            raise PayslipError(PayslipErrorCode.EXTRACTION_TIMEOUT) from None
        except Exception as error:  # a parser bug must not leak details or crash the route
            logger.warning("payslip extraction failed type=%s", type(error).__name__)
            raise PayslipError(PayslipErrorCode.MALFORMED_PDF) from None

    async def _interpret(
        self, candidates: Sequence[Candidate]
    ) -> tuple[PayslipInterpretation | None, tuple[DocumentIssue, ...]]:
        if not candidates:
            return None, (DocumentIssue.NO_CANDIDATES,)
        if any(c.suspicious for c in candidates):
            # Instruction-like text: the AI is not consulted at all for this document.
            return None, (DocumentIssue.SUSPICIOUS_CONTENT,)
        if self._llm is None or not self.config.external_llm_enabled:
            return None, (DocumentIssue.AI_DISABLED,)
        result = await self._llm.generate(interpretation_task(candidates))
        if not result.ok or result.value is None:
            logger.info("payslip interpretation unavailable outcome=%s", result.outcome.value)
            return None, (DocumentIssue.AI_UNAVAILABLE,)
        if result.value.instructions_in_document:
            return None, (DocumentIssue.SUSPICIOUS_CONTENT,)
        return result.value, ()

    # --- review -----------------------------------------------------------------------

    async def get(self, actor: ActorRef, draft_id: UUID) -> PayslipDraft:
        _require_employee(actor)
        return await self._owned(actor, draft_id)

    async def correct(
        self,
        actor: ActorRef,
        draft_id: UUID,
        *,
        expected_draft_version: int,
        corrections: Sequence[Correction],
    ) -> PayslipDraft:
        _require_employee(actor)
        fields = [c.field for c in corrections]
        if not corrections or len(fields) != len(set(fields)):
            raise PayslipError(PayslipErrorCode.INVALID_SELECTION)
        async with self._store.lock(draft_id):
            draft = await self._owned(actor, draft_id)
            self._require_review(draft, expected_draft_version)
            updated = {f.field: f for f in draft.fields}
            for correction in corrections:
                current = updated.get(correction.field)
                if current is None or correction.field not in PAYSLIP_FIELDS:
                    raise PayslipError(
                        PayslipErrorCode.FIELD_NOT_IN_DRAFT, field=correction.field.value
                    )
                try:
                    SetChange(op="set", field=correction.field, value=correction.value)
                    value = Decimal(correction.value)
                except (ValidationError, ArithmeticError):
                    raise PayslipError(
                        PayslipErrorCode.INVALID_FIELD_VALUE, field=correction.field.value
                    ) from None
                updated[correction.field] = replace(
                    current,
                    status=FieldStatus.CORRECTED,
                    value=value,
                    confidence=Confidence.HIGH,
                    issues=(),
                )
            changed = replace(
                draft,
                version=draft.version + 1,
                fields=tuple(updated[f.field] for f in draft.fields),
                last_error=None,
            )
            await self._store.replace(changed)
            return changed

    async def cancel(self, actor: ActorRef, draft_id: UUID) -> None:
        _require_employee(actor)
        async with self._store.lock(draft_id):
            draft = await self._owned(actor, draft_id)
            if draft.status is DraftStatus.APPLYING:
                raise PayslipError(PayslipErrorCode.DRAFT_NOT_EDITABLE)
            await self._store.remove(draft_id)
        logger.info("payslip draft removed draft_id=%s", draft_id)

    # --- approval ---------------------------------------------------------------------

    async def approve(
        self,
        actor: ActorRef,
        draft_id: UUID,
        command: ApproveCommand,
        *,
        trace_id: str | None = None,
    ) -> UpdateInputsResult:
        _require_employee(actor)
        if not command.fields or len(set(command.fields)) != len(command.fields):
            raise PayslipError(PayslipErrorCode.INVALID_SELECTION)
        request_hash = _request_hash(command)
        async with self._store.lock(draft_id):
            draft = await self._owned(actor, draft_id)
            if draft.status is DraftStatus.APPLIED and draft.applied is not None:
                if draft.applied.operation_id != command.operation_id:
                    raise PayslipError(PayslipErrorCode.DRAFT_ALREADY_APPLIED)
                if draft.applied.request_hash != request_hash:
                    raise PayslipError(PayslipErrorCode.OPERATION_PAYLOAD_MISMATCH)
                return draft.applied.result
            self._require_review(draft, command.expected_draft_version)
            changes = self._changes(draft, command.fields)

            await self._store.replace(replace(draft, status=DraftStatus.APPLYING))
            try:
                result = await self._apply(actor, draft, command, changes, trace_id)
            except PayslipError as error:
                await self._store.replace(
                    replace(draft, status=DraftStatus.REVIEW, last_error=error.code.value)
                )
                logger.info("payslip approval failed draft_id=%s code=%s", draft_id, error.code)
                raise
            except Exception as error:
                await self._store.replace(
                    replace(
                        draft,
                        status=DraftStatus.REVIEW,
                        last_error=PayslipErrorCode.BUSINESS_UNAVAILABLE.value,
                    )
                )
                logger.warning(
                    "payslip approval error draft_id=%s type=%s", draft_id, type(error).__name__
                )
                raise PayslipError(PayslipErrorCode.BUSINESS_UNAVAILABLE) from None

            applied = replace(
                draft,
                version=draft.version + 1,
                status=DraftStatus.APPLIED,
                fields=_strip_values(draft.fields),
                last_error=None,
                applied=AppliedOperation(
                    operation_id=command.operation_id,
                    request_hash=request_hash,
                    result=result,
                    applied_at=self._clock.now(),
                ),
            )
            await self._store.replace(applied)
        logger.info(
            "payslip draft applied draft_id=%s fields=%d profile_version=%s",
            draft_id,
            len(command.fields),
            result.new_profile_version,
        )
        return result

    def _changes(self, draft: PayslipDraft, selected: Sequence[FieldPath]) -> list[SetChange]:
        """Re-validate every selected field against the authoritative contract model."""
        changes: list[SetChange] = []
        for path in selected:
            current = draft.field_for(path)
            if current is None:
                raise PayslipError(PayslipErrorCode.FIELD_NOT_IN_DRAFT, field=path.value)
            if current.status not in APPROVABLE or current.value is None:
                raise PayslipError(PayslipErrorCode.FIELD_NOT_APPROVABLE, field=path.value)
            try:
                changes.append(
                    SetChange(op="set", field=path, value=contract_value(path, current.value))
                )
            except ValidationError:
                raise PayslipError(PayslipErrorCode.INVALID_FIELD_VALUE, field=path.value) from None
        return changes

    async def _apply(
        self,
        actor: ActorRef,
        draft: PayslipDraft,
        command: ApproveCommand,
        changes: list[SetChange],
        trace_id: str | None,
    ) -> UpdateInputsResult:
        ctx = await self._tool_context(actor, draft.conversation_id, trace_id)
        context = await self._business.get_conversation_context(ctx)
        if context.error is not None or context.data is None:
            raise self._business_error(context.error.code if context.error else None)
        try:
            request = UpdateInputsRequest(
                mode=UpdateMode.ACTUAL_UPDATE,
                expected_case_version=context.data.case_version,
                expected_profile_version=command.expected_profile_version,
                operation_id=command.operation_id,
                changes=list(changes),
            )
        except ValidationError:
            raise PayslipError(PayslipErrorCode.INVALID_SELECTION) from None
        envelope = await self._business.update_conversation_inputs(ctx, request)
        if envelope.error is not None or envelope.data is None:
            raise self._business_error(envelope.error.code if envelope.error else None)
        return envelope.data

    @staticmethod
    def _business_error(code: ErrorCode | None) -> PayslipError:
        if code is None:
            return PayslipError(PayslipErrorCode.BUSINESS_UNAVAILABLE)
        return PayslipError(_BUSINESS_ERRORS.get(code, PayslipErrorCode.BUSINESS_REJECTED))

    # --- helpers ----------------------------------------------------------------------

    async def _tool_context(
        self, actor: ActorRef, conversation_id: UUID, trace_id: str | None
    ) -> ToolContext:
        try:
            binding = await self._conversations.turn_binding(
                actor, conversation_id, trace_id=trace_id
            )
        except ConversationError as error:
            code = (
                PayslipErrorCode.BUSINESS_UNAVAILABLE
                if error.retryable
                else PayslipErrorCode.CONVERSATION_NOT_FOUND
            )
            raise PayslipError(code) from None
        context = binding.tool_context
        if (
            context.actor.subject_id != actor.subject_id
            or context.conversation_id != conversation_id
        ):
            raise PayslipError(PayslipErrorCode.CONVERSATION_NOT_FOUND)
        return context

    async def _owned(self, actor: ActorRef, draft_id: UUID) -> PayslipDraft:
        draft = await self._store.get(draft_id)
        if draft is None or draft.owner_subject_id != actor.subject_id:
            raise PayslipError(PayslipErrorCode.DRAFT_NOT_FOUND)
        if self._clock.now() >= draft.expires_at:
            await self._store.remove(draft_id)
            raise PayslipError(PayslipErrorCode.DRAFT_EXPIRED)
        return draft

    @staticmethod
    def _require_review(draft: PayslipDraft, expected_version: int) -> None:
        if draft.status is DraftStatus.APPLIED:
            raise PayslipError(PayslipErrorCode.DRAFT_ALREADY_APPLIED)
        if draft.status is not DraftStatus.REVIEW:
            raise PayslipError(PayslipErrorCode.DRAFT_NOT_EDITABLE)
        if draft.version != expected_version:
            raise PayslipError(PayslipErrorCode.DRAFT_VERSION_CONFLICT)
