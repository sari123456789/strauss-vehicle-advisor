"""`get_representative_request` (G1): the status of one request of the server-bound case.

The request is looked up only inside the authorized case (owner, conversation and role are
checked first); another case's or an invented request ID is `NOT_FOUND`. Read-only: no
Outbox or event is involved. `answer` stays empty until the representative-answer stage
stores answers.
"""

from app.business.application.errors import NotFoundError, translate_errors
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.outcomes import Outcome
from app.business.application.ports.unit_of_work import BusinessUnitOfWorkFactory
from app.contracts.representatives import (
    RepresentativeFieldCode,
    RepresentativeRequestLookup,
    RequestStatus,
    RequestStatusView,
)
from app.contracts.tool_context import ToolContext


class RepresentativeRequestService:
    def __init__(
        self, unit_of_work: BusinessUnitOfWorkFactory, policy: AuthorizationPolicy
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy

    async def get_representative_request(
        self, ctx: ToolContext, request: RepresentativeRequestLookup
    ) -> Outcome[RequestStatusView]:
        with translate_errors():
            async with self._unit_of_work() as uow:
                _actor, case = await self._policy.tool_case(uow, ctx)
                found = await uow.representative_requests.get(case.case_id, request.request_id)
                if found is None:
                    raise NotFoundError()
        view = RequestStatusView(
            request_id=found.request_id,
            status=RequestStatus(found.status.value),
            field_code=RepresentativeFieldCode(found.field_code.value),
            option_key=found.option_key,
            answer=None,
            created_at=found.created_at,
            updated_at=found.updated_at,
        )
        return Outcome(view, case_version=case.version)
