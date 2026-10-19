"""Shared Stage B helpers: the production Business composition and the AI-side entry points.

`business()` builds `bootstrap.build_business_services(settings)` - the same composition the
AI worker will receive - so `services.tools` is the real `BusinessToolsFacade`. Nothing here
imports LangGraph, so the gateway-level tests run wherever the Business stack runs.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr

from app.bootstrap import BusinessServices, build_business_services, build_demo_seed_reader
from app.business.application.seeding.demo_seed_service import employee_subject_id
from app.contracts.case_inputs import ConfirmProfileFieldsRequest
from app.contracts.case_lifecycle import CreateCaseCommand
from app.contracts.common import EnvelopeStatus, RunKind
from app.contracts.comparisons import CompareRequest
from app.contracts.readiness import Selection, SelectionMode
from app.contracts.tool_context import Origin, ToolContext
from app.settings import Settings
from tests.integration.business.database.db_helpers import sql


class RecordingPort:
    """Pass-through to the real facade that records which tools were called."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._real, name)

        async def call(*args: Any) -> Any:
            self.calls.append(name)
            return await target(*args)

        return call


@asynccontextmanager
async def business(url: str) -> AsyncIterator[BusinessServices]:
    services = build_business_services(Settings(database_url=SecretStr(url)))
    try:
        yield services
    finally:
        await services.engine.dispose()


def demo_subject(ref: str) -> UUID:
    return employee_subject_id(build_demo_seed_reader().read().uuid_namespace, ref)


async def open_case(services: BusinessServices, subject: UUID) -> ToolContext:
    """What the AI does for a new conversation: trusted actor + `CaseLifecyclePort`."""
    actor = await services.identity.resolve(subject, Origin.WORKER)
    conversation = uuid4()
    created = await services.lifecycle.create_case_for_conversation(
        actor,
        CreateCaseCommand(
            conversation_id=conversation, calculation_month="2026-10", operation_id=uuid4()
        ),
    )
    assert created.status is EnvelopeStatus.OK and created.data is not None
    return ToolContext(
        actor=actor,
        conversation_id=conversation,
        case_id=created.data.case_id,
        trace_id=f"stage-b-{uuid4().hex[:8]}",
    )


def runs_of(url: str, ctx: ToolContext) -> int:
    rows = sql(url, "SELECT count(*) FROM business.comparison_runs WHERE case_id = %s", ctx.case_id)
    return int(rows[0][0])  # type: ignore[call-overload]


def compare(
    option_keys: list[str],
    *,
    case_version: int,
    profile_version: int | None,
    include_current: bool = False,
    kind: RunKind = RunKind.REGULAR,
    operation_id: UUID | None = None,
) -> CompareRequest:
    return CompareRequest(
        selection=Selection(mode=SelectionMode.EXPLICIT, option_keys=option_keys),
        include_current=include_current,
        kind=kind,
        expected_case_version=case_version,
        expected_profile_version=profile_version,
        operation_id=operation_id or uuid4(),
    )


async def confirm_current_arrangement(services: BusinessServices, ctx: ToolContext) -> None:
    """The employee confirms the saved current arrangement (G3), directly on the facade so the
    test does not depend on how the AI workflow asks for confirmation."""
    context = (await services.tools.get_conversation_context(ctx)).data
    assert context is not None
    fields = [
        f.value
        for f in context.fields_requiring_confirmation
        if f.value.startswith("currentArrangement.")
    ]
    confirmed = await services.tools.confirm_profile_fields(
        ctx,
        ConfirmProfileFieldsRequest.model_validate(
            {
                "expectedCaseVersion": context.case_version,
                "expectedProfileVersion": context.latest_profile_version,
                "operationId": str(uuid4()),
                "fields": fields,
            }
        ),
    )
    assert confirmed.status is EnvelopeStatus.OK, confirmed
