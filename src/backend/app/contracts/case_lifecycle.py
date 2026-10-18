"""Business case creation for a conversation. STATUS: PROVISIONAL (C1, decision D1).

The Business Application owns and commits its own transaction; no database transaction or
session is shared across modules. The call is idempotent by (authenticated actor,
conversationId, operationId): a retry with the same command returns the same case
(`created=false`); the same operation ID with a different payload is
`OPERATION_PAYLOAD_MISMATCH`; a conversation ID already bound to another owner is `NOT_FOUND`.

Coordination with the AI conversation record is recoverable, not atomic: the AI module first
records its conversation (intent), then calls this port, then activates the conversation; on
recovery it repeats the call with the same operation ID. The actor is always established by
trusted server code, never by a model.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import Field

from app.contracts.common import CalculationMonthStr, ContractModel, Envelope
from app.contracts.tool_context import ActorRef


class CreateCaseCommand(ContractModel):
    conversation_id: UUID
    calculation_month: CalculationMonthStr
    operation_id: UUID


class CreateCaseResult(ContractModel):
    case_id: UUID
    case_version: int = Field(ge=0)
    salary_profile_version_used: int = Field(ge=0)
    created: bool
    """False when the same operation was replayed and the existing case was returned."""


@runtime_checkable
class CaseLifecyclePort(Protocol):
    async def create_case_for_conversation(
        self, actor: ActorRef, command: CreateCaseCommand
    ) -> Envelope[CreateCaseResult]: ...
