"""`get_representative_request` (G1): the status of one request of the server-bound case.

The request is looked up only inside the authorized case (owner, conversation and role are
checked first); another case's or an invented request ID is `NOT_FOUND`. Read-only: no
Outbox or event is involved. `answer` is the persisted answer (B3a), if any.
"""

from app.business.application.errors import NotFoundError, translate_errors
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.outcomes import Outcome
from app.business.application.ports.unit_of_work import BusinessUnitOfWorkFactory
from app.business.domain.representatives import RepresentativeAnswer
from app.contracts.representatives import (
    AnswerApplication,
    AnswerOutcome,
    AnswerView,
    MyRequestView,
    RepresentativeFieldCode,
    RepresentativeRequestLookup,
    RequestStatus,
    RequestStatusView,
)
from app.contracts.tool_context import ActorRef, ToolContext


class RepresentativeRequestService:
    def __init__(
        self, unit_of_work: BusinessUnitOfWorkFactory, policy: AuthorizationPolicy
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy

    async def list_mine(self, actor: ActorRef) -> list[MyRequestView]:
        """The signed-in employee's own requests and answers, newest first."""
        with translate_errors():
            async with self._unit_of_work() as uow:
                employee = await self._policy.employee(uow, actor)
                requests = await uow.representative_requests.list_for_creator(employee.subject_id)
                views: list[MyRequestView] = []
                for found in requests:
                    answer = await uow.representative_answers.get(found.request_id)
                    general = (
                        found.field_code.value == RepresentativeFieldCode.GENERAL_QUESTION.value
                    )
                    views.append(
                        MyRequestView(
                            request_id=found.request_id,
                            status=RequestStatus(found.status.value),
                            field_code=RepresentativeFieldCode(found.field_code.value),
                            question_text=found.question_text,
                            source="form"
                            if found.subject_snapshot.get("source") == "form"
                            else "chat",
                            created_at=found.created_at,
                            updated_at=found.updated_at,
                            answer=None
                            if answer is None
                            else answer_view(answer, text_answer=general),
                        )
                    )
        return views

    async def get_representative_request(
        self, ctx: ToolContext, request: RepresentativeRequestLookup
    ) -> Outcome[RequestStatusView]:
        with translate_errors():
            async with self._unit_of_work() as uow:
                _actor, case = await self._policy.tool_case(uow, ctx)
                found = await uow.representative_requests.get(case.case_id, request.request_id)
                if found is None:
                    raise NotFoundError()
                answer = await uow.representative_answers.get(found.request_id)
        view = RequestStatusView(
            request_id=found.request_id,
            status=RequestStatus(found.status.value),
            field_code=RepresentativeFieldCode(found.field_code.value),
            option_key=found.option_key,
            answer=None
            if answer is None
            else answer_view(
                answer,
                text_answer=found.field_code.value
                == RepresentativeFieldCode.GENERAL_QUESTION.value,
            ),
            created_at=found.created_at,
            updated_at=found.updated_at,
        )
        return Outcome(view, case_version=case.version)


def answer_view(answer: RepresentativeAnswer, *, text_answer: bool = False) -> AnswerView:
    """The persisted answer as the shared contract shows it. The note stays internal, except for
    a general question, where it is the representative's full text answer (`text`)."""
    return AnswerView(
        outcome=AnswerOutcome(answer.outcome.value),
        value=answer.value,
        unit="ILS" if answer.unit == "ILS" else None,
        effective_from=answer.effective_from,
        effective_to=answer.effective_to,
        answered_at=answer.answered_at,
        application=None
        if answer.application is None
        else AnswerApplication(answer.application.value),
        text=(answer.note or answer.value) if text_answer else None,
    )
