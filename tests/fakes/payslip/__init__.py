"""Deterministic fakes and synthetic fixtures for the payslip-import tests (no network, no DB)."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.ai.application.errors import ConversationAccessError
from app.contracts.case_inputs import UpdateInputsRequest, UpdateInputsResult
from app.contracts.cases import CaseContext
from app.contracts.common import Envelope, EnvelopeStatus, ErrorCode, ErrorInfo
from app.contracts.tool_context import ActorRef, Origin, Role, ToolContext

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def employee(subject_id: UUID | None = None) -> ActorRef:
    return ActorRef(
        subject_id=subject_id or uuid4(), roles=frozenset({Role.EMPLOYEE}), origin=Origin.HTTP
    )


def representative() -> ActorRef:
    return ActorRef(subject_id=uuid4(), roles=frozenset({Role.REPRESENTATIVE}), origin=Origin.HTTP)


class ManualClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


@dataclass(frozen=True)
class _Binding:
    tool_context: ToolContext


class FakeConversations:
    """Implements `ConversationBinding`: owner check like `ConversationService.turn_binding`."""

    def __init__(self) -> None:
        self.owners: dict[UUID, UUID] = {}
        self.cases: dict[UUID, UUID] = {}

    def open(self, actor: ActorRef) -> UUID:
        conversation_id = uuid4()
        self.owners[conversation_id] = actor.subject_id
        self.cases[conversation_id] = uuid4()
        return conversation_id

    async def turn_binding(
        self, actor: ActorRef, conversation_id: UUID, *, trace_id: str | None = None
    ) -> _Binding:
        if self.owners.get(conversation_id) != actor.subject_id:
            raise ConversationAccessError("not found")
        return _Binding(
            ToolContext(
                actor=actor,
                conversation_id=conversation_id,
                case_id=self.cases[conversation_id],
                trace_id=trace_id or "trace-payslip-test",
            )
        )


def _error(code: ErrorCode, trace_id: str) -> Envelope[Any]:
    return Envelope(
        status=EnvelopeStatus.ERROR,
        error=ErrorInfo(code=code, trace_id=trace_id),
        trace_id=trace_id,
    )


@dataclass
class FakeBusiness:
    """The two Business tools the approval uses, with Business-like idempotency."""

    case_version: int = 4
    profile_version: int = 2
    update_error: ErrorCode | None = None
    delay: float = 0.0
    updates: list[tuple[ToolContext, UpdateInputsRequest]] = field(default_factory=list)
    applied: dict[UUID, UpdateInputsResult] = field(default_factory=dict)
    context_calls: int = 0

    async def get_conversation_context(self, ctx: ToolContext) -> Envelope[CaseContext]:
        self.context_calls += 1
        data = CaseContext.model_construct(case_version=self.case_version)
        return Envelope(status=EnvelopeStatus.OK, data=data, trace_id=ctx.trace_id)

    async def update_conversation_inputs(
        self, ctx: ToolContext, request: UpdateInputsRequest
    ) -> Envelope[UpdateInputsResult]:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.updates.append((ctx, request))
        if self.update_error is not None:
            return _error(self.update_error, ctx.trace_id)
        if request.operation_id in self.applied:
            return Envelope(
                status=EnvelopeStatus.OK,
                data=self.applied[request.operation_id],
                trace_id=ctx.trace_id,
            )
        if request.expected_profile_version != self.profile_version:
            return _error(ErrorCode.VERSION_CONFLICT, ctx.trace_id)
        self.profile_version += 1
        self.case_version += 1
        result = UpdateInputsResult(
            accepted_fields=[c.field for c in request.changes],
            new_case_version=self.case_version,
            new_profile_version=self.profile_version,
        )
        self.applied[request.operation_id] = result
        return Envelope(status=EnvelopeStatus.OK, data=result, trace_id=ctx.trace_id)
