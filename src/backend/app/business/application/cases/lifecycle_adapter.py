"""`CaseLifecyclePort` implementation (contract C1, decision B6).

Expected business outcomes are returned as `Envelope` values, never raised across the module
boundary. The Business Application owns and commits the transaction.
"""

from collections.abc import Callable
from uuid import uuid4

from app.business.application.cases.case_service import CaseService
from app.business.application.errors import BusinessError, error_envelope
from app.contracts.case_lifecycle import CreateCaseCommand, CreateCaseResult
from app.contracts.common import Envelope, EnvelopeStatus
from app.contracts.tool_context import ActorRef


def _new_trace_id() -> str:
    return uuid4().hex


class CaseLifecycleAdapter:
    def __init__(self, cases: CaseService, trace_id: Callable[[], str] = _new_trace_id) -> None:
        self._cases = cases
        self._trace_id = trace_id

    async def create_case_for_conversation(
        self, actor: ActorRef, command: CreateCaseCommand
    ) -> Envelope[CreateCaseResult]:
        trace_id = self._trace_id()
        try:
            result = await self._cases.create_case_for_conversation(actor, command)
        except BusinessError as error:
            return error_envelope(error, trace_id)
        return Envelope[CreateCaseResult](
            status=EnvelopeStatus.OK,
            data=result,
            case_version=result.case_version,
            profile_version=result.salary_profile_version_used,
            trace_id=trace_id,
        )
