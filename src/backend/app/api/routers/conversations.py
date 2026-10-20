"""Conversation HTTP routes (architecture §19; polling per cloud plan DECISION P) - AI-owned.

The actor comes only from the verified bearer token (`current_actor`, the same dependency as
the Business API), never from the body. Every route delegates to `ConversationService`, which
enforces ownership: another employee's conversation or input is indistinguishable from a
missing one (404). Errors use the Business Problem Details shape through `BusinessRoute`; the
client maps `code` to Hebrew text.

Wiring (shared composition, Business-owned `app.main`): the lifespan stores the AI runtime's
`ConversationService` as `app.state.ai_conversations` and includes `router` under `/api/v1`.

The request/response DTOs below are PROVISIONAL HTTP DTOs awaiting joint approval
(CLAUDE.md: unsettled DTO shapes are not automatically approved).
"""

from datetime import datetime
from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request
from pydantic import Field

from app.ai.application import errors
from app.ai.application.conversation_service import (
    ConversationService,
    ConversationView,
    InputStatusView,
)
from app.ai.application.ports.conversation_store import MessageRecord
from app.ai.application.turn_service import MAX_MESSAGE_CHARS
from app.api.business.dependencies import Actor
from app.api.business.problem import ApiProblem
from app.api.business.route import BusinessRoute
from app.contracts.common import ContractModel, ErrorCode

router = APIRouter(prefix="/conversations", tags=["conversations"], route_class=BusinessRoute)

MAX_PAGE = 200
ConversationId = Annotated[UUID, Path(alias="conversationId")]


# --- DTOs (PROVISIONAL) -------------------------------------------------------------------


class StartConversationRequest(ContractModel):
    client_request_id: UUID
    """Idempotency key: repeating it returns the same conversation and case."""
    calculation_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class ConversationSummary(ContractModel):
    conversation_id: UUID
    case_id: UUID | None
    status: str
    calculation_month: str


class ConversationList(ContractModel):
    conversations: list[ConversationSummary]


class MessageView(ContractModel):
    message_id: UUID
    sequence: int
    role: str
    kind: str
    text: str
    payload: dict[str, Any]
    input_id: UUID | None
    created_at: datetime


class MessagePage(ContractModel):
    messages: list[MessageView]
    last_sequence: int
    """Pass as `afterSequence` on the next poll (unchanged when nothing new arrived)."""
    has_more: bool


class ConversationDetail(ContractModel):
    conversation: ConversationSummary
    history: MessagePage


class SubmitMessageRequest(ContractModel):
    message_id: UUID
    """Client-generated; repeating the same ID and text creates nothing new."""
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class SubmitMessageResponse(ContractModel):
    input_id: UUID
    message_id: UUID
    sequence: int
    duplicate: bool


class InputStatus(ContractModel):
    input_id: UUID
    sequence: int
    status: str
    """pending, processing, retry, completed, dead or needs_reconciliation."""
    attempts: int
    messages: list[MessageView]


# --- helpers ------------------------------------------------------------------------------


def conversation_service(request: Request) -> ConversationService:
    service = getattr(request.app.state, "ai_conversations", None)
    if not isinstance(service, ConversationService):
        raise ApiProblem(ErrorCode.INFRA_UNAVAILABLE, details={"reason": "not_configured"})
    return service


def _problem(error: errors.ConversationError) -> NoReturn:
    details = {"retryable": "true"} if error.retryable else {}
    raise ApiProblem(error.code, details=details) from None


def _summary(view: ConversationView) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=view.conversation_id,
        case_id=view.case_id,
        status=view.status,
        calculation_month=view.calculation_month,
    )


def _message(record: MessageRecord) -> MessageView:
    return MessageView(
        message_id=record.message_id,
        sequence=record.sequence,
        role=record.role,
        kind=record.kind,
        text=record.text,
        payload=record.payload,
        input_id=record.input_id,
        created_at=record.created_at,
    )


def _page(records: list[MessageRecord], after: int, limit: int) -> MessagePage:
    return MessagePage(
        messages=[_message(r) for r in records],
        last_sequence=records[-1].sequence if records else after,
        has_more=len(records) == limit,
    )


def _input(view: InputStatusView) -> InputStatus:
    return InputStatus(
        input_id=view.input_id,
        sequence=view.sequence,
        status=view.status,
        attempts=view.attempts,
        messages=[_message(m) for m in view.messages],
    )


# --- routes -------------------------------------------------------------------------------


@router.post("", response_model=ConversationSummary)
async def start_conversation(
    request: Request, actor: Actor, body: StartConversationRequest
) -> ConversationSummary:
    try:
        view = await conversation_service(request).start_conversation(
            actor,
            client_request_id=body.client_request_id,
            calculation_month=body.calculation_month,
        )
    except errors.ConversationError as error:
        _problem(error)
    return _summary(view)


@router.get("", response_model=ConversationList)
async def list_conversations(request: Request, actor: Actor) -> ConversationList:
    try:
        views = await conversation_service(request).list_conversations(actor)
    except errors.ConversationError as error:
        _problem(error)
    return ConversationList(conversations=[_summary(v) for v in views])


@router.get("/{conversationId}", response_model=ConversationDetail)
async def get_conversation(
    request: Request, actor: Actor, conversation_id: ConversationId
) -> ConversationDetail:
    service = conversation_service(request)
    try:
        view = await service.get_conversation(actor, conversation_id)
        records = await service.history(actor, conversation_id, limit=MAX_PAGE)
    except errors.ConversationError as error:
        _problem(error)
    return ConversationDetail(conversation=_summary(view), history=_page(records, 0, MAX_PAGE))


@router.post("/{conversationId}/messages", status_code=202, response_model=SubmitMessageResponse)
async def submit_message(
    request: Request, actor: Actor, conversation_id: ConversationId, body: SubmitMessageRequest
) -> SubmitMessageResponse:
    try:
        submitted = await conversation_service(request).submit_user_message(
            actor, conversation_id, client_message_id=body.message_id, text=body.text
        )
    except errors.ConversationError as error:
        _problem(error)
    return SubmitMessageResponse(
        input_id=submitted.input_id,
        message_id=submitted.message_id,
        sequence=submitted.message_sequence,
        duplicate=submitted.duplicate,
    )


@router.get("/{conversationId}/messages", response_model=MessagePage)
async def poll_messages(
    request: Request,
    actor: Actor,
    conversation_id: ConversationId,
    after_sequence: Annotated[int, Query(alias="afterSequence", ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = MAX_PAGE,
) -> MessagePage:
    try:
        records = await conversation_service(request).history(
            actor, conversation_id, after_sequence=after_sequence, limit=limit
        )
    except errors.ConversationError as error:
        _problem(error)
    return _page(records, after_sequence, limit)


@router.get("/{conversationId}/inputs/{inputId}", response_model=InputStatus)
async def get_input(
    request: Request,
    actor: Actor,
    conversation_id: ConversationId,
    input_id: Annotated[UUID, Path(alias="inputId")],
) -> InputStatus:
    try:
        view = await conversation_service(request).get_input(actor, conversation_id, input_id)
    except errors.ConversationError as error:
        _problem(error)
    return _input(view)
