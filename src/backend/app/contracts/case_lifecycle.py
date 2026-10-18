"""Case creation inside a caller-owned transaction. STATUS: PROVISIONAL.

Future atomic boundary (architecture Sections 18-19, decision D4):
the AI Engine's conversation service opens a Unit of Work, calls
`create_case_for_conversation(uow, ...)` (which writes the business case but does NOT commit),
writes its own conversation row through its own store bound to the same transaction, and then
commits once. The Business Application never writes AI tables directly.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import Field

from app.contracts.common import CalculationMonthStr, ContractModel
from app.contracts.tool_context import ActorRef


@runtime_checkable
class TransactionHandle(Protocol):
    """Opaque handle to an open Unit of Work. The caller that opened it commits it."""


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
        self, uow: TransactionHandle, actor: ActorRef, command: CreateCaseCommand
    ) -> CreateCaseResult: ...
