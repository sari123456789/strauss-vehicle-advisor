"""In-process `CaseLifecyclePort` fake that follows contract C1 exactly.

Idempotent by (actor, conversationId, operationId): a replay returns the same case with
`created=false`; the same operation ID with a different payload is
`OPERATION_PAYLOAD_MISMATCH`; a conversation ID bound to another owner is `NOT_FOUND`.
Failure injection simulates outages (error envelope or raised exception) and slow calls.
"""

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from app.contracts.case_lifecycle import CreateCaseCommand, CreateCaseResult
from app.contracts.common import Envelope, EnvelopeStatus, ErrorCode, ErrorInfo
from app.contracts.tool_context import ActorRef


@dataclass
class FakeCaseLifecycle:
    fail_next: list[ErrorCode | type[Exception]] = field(default_factory=list)
    delay_seconds: float = 0.0
    calls: list[tuple[UUID, CreateCaseCommand]] = field(default_factory=list)
    _operations: dict[tuple[UUID, UUID], tuple[CreateCaseCommand, UUID]] = field(
        default_factory=dict
    )
    _conversations: dict[UUID, tuple[UUID, UUID]] = field(default_factory=dict)
    """conversation ID -> (owner, case ID)."""

    def case_for(self, conversation_id: UUID) -> UUID | None:
        bound = self._conversations.get(conversation_id)
        return None if bound is None else bound[1]

    async def create_case_for_conversation(
        self, actor: ActorRef, command: CreateCaseCommand
    ) -> Envelope[CreateCaseResult]:
        self.calls.append((actor.subject_id, command))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail_next:
            failure = self.fail_next.pop(0)
            if isinstance(failure, ErrorCode):
                return self._error(failure)
            raise failure("injected failure")
        key = (actor.subject_id, command.operation_id)
        if key in self._operations:
            stored, case_id = self._operations[key]
            if stored != command:
                return self._error(ErrorCode.OPERATION_PAYLOAD_MISMATCH)
            return self._ok(case_id, created=False)
        bound = self._conversations.get(command.conversation_id)
        if bound is not None and bound[0] != actor.subject_id:
            return self._error(ErrorCode.NOT_FOUND)
        case_id = bound[1] if bound is not None else uuid4()
        self._conversations[command.conversation_id] = (actor.subject_id, case_id)
        self._operations[key] = (command, case_id)
        return self._ok(case_id, created=bound is None)

    @staticmethod
    def _ok(case_id: UUID, *, created: bool) -> Envelope[CreateCaseResult]:
        result = CreateCaseResult(
            case_id=case_id, case_version=0, salary_profile_version_used=1, created=created
        )
        return Envelope[CreateCaseResult](
            status=EnvelopeStatus.OK, data=result, case_version=0, trace_id="fake"
        )

    @staticmethod
    def _error(code: ErrorCode) -> Envelope[CreateCaseResult]:
        return Envelope[CreateCaseResult](
            status=EnvelopeStatus.ERROR,
            error=ErrorInfo(code=code, trace_id="fake"),
            trace_id="fake",
        )
