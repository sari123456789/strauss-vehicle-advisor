"""Server-injected execution context. STATUS: PROVISIONAL.

These values are established by the server (authentication / persisted conversation ownership).
They are NEVER model-selected tool arguments and never taken from LLM output.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.contracts.common import ContractModel


class Role(StrEnum):
    EMPLOYEE = "employee"
    REPRESENTATIVE = "representative"
    IMPORT_OPERATOR = "import_operator"


class Origin(StrEnum):
    """Which runtime entry point originated the call (for authorization and diagnostics)."""

    HTTP = "http"
    AGENT = "agent"
    WORKER = "worker"
    CLI = "cli"


class ActorRef(ContractModel):
    subject_id: UUID
    roles: frozenset[Role] = Field(min_length=1)
    origin: Origin


class ToolContext(ContractModel):
    """Bound to one authorized conversation and its business case."""

    actor: ActorRef
    conversation_id: UUID
    case_id: UUID
    trace_id: str
