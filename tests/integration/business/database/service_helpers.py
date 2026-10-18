"""Wiring of the Step 3B/4A Application services over a test database (real PostgreSQL)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.business.application.cases.case_service import CaseService
from app.business.application.cases.lifecycle_adapter import CaseLifecycleAdapter
from app.business.application.comparisons.comparison_service import ComparisonService
from app.business.application.comparisons.readiness_service import ReadinessService
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.identity.identity_service import IdentityService
from app.business.application.occupations.occupation_service import OccupationService
from app.business.application.profiles.profile_service import EmployeeProfileService
from app.business.application.representatives.request_service import (
    RepresentativeRequestService,
)
from app.business.application.vehicles.catalogue_service import CatalogueService
from app.business.application.vehicles.valuation_service import ValuationService
from app.business.infrastructure.clock import SystemClock
from app.contracts.case_inputs import UpdateInputsRequest
from app.contracts.case_lifecycle import CreateCaseCommand, CreateCaseResult
from app.contracts.tool_context import ActorRef, Origin, Role, ToolContext
from tests.integration.business.database.case_helpers import (
    Persistence,
    new_user,
    persistence,
    profile,
)


@dataclass(frozen=True)
class Services:
    p: Persistence
    identity: IdentityService
    profiles: EmployeeProfileService
    cases: CaseService
    lifecycle: CaseLifecycleAdapter
    catalogue: CatalogueService
    valuation: ValuationService
    occupations: OccupationService
    requests: RepresentativeRequestService
    readiness: ReadinessService
    comparisons: ComparisonService


@asynccontextmanager
async def services(url: str) -> AsyncIterator[Services]:
    async with persistence(url) as p:
        clock = SystemClock()
        policy = AuthorizationPolicy()
        profiles = EmployeeProfileService(p.uow, policy, clock)
        cases = CaseService(p.uow, policy, profiles, p.reference, clock)
        yield Services(
            p,
            IdentityService(p.uow),
            profiles,
            cases,
            CaseLifecycleAdapter(cases),
            CatalogueService(p.uow, policy, p.reference, clock),
            ValuationService(p.uow, policy, p.reference),
            OccupationService(p.uow, policy, p.reference),
            RepresentativeRequestService(p.uow, policy),
            ReadinessService(p.uow, policy, p.reference, clock),
            ComparisonService(p.uow, policy, p.reference, clock),
        )


def actor(subject_id: UUID, *roles: Role, origin: Origin = Origin.AGENT) -> ActorRef:
    return ActorRef(subject_id=subject_id, roles=frozenset(roles or {Role.EMPLOYEE}), origin=origin)


async def employee_with_profile(s: Services, *, base: str = "18500.00") -> ActorRef:
    subject = await new_user(s.p)
    async with s.p.uow() as uow:
        await uow.profiles.add(profile(subject, base=base))
        await uow.commit()
    return actor(subject)


async def new_case(s: Services, who: ActorRef) -> tuple[ToolContext, CreateCaseResult]:
    conversation = uuid4()
    result = await s.cases.create_case_for_conversation(
        who,
        CreateCaseCommand(
            conversation_id=conversation, calculation_month="2026-10", operation_id=uuid4()
        ),
    )
    ctx = ToolContext(actor=who, conversation_id=conversation, case_id=result.case_id, trace_id="t")
    return ctx, result


def update(
    mode: str,
    case_version: int,
    changes: list[dict[str, Any]],
    *,
    profile_version: int | None = None,
    operation_id: UUID | None = None,
) -> UpdateInputsRequest:
    body: dict[str, Any] = {
        "mode": mode,
        "expectedCaseVersion": case_version,
        "operationId": str(operation_id or uuid4()),
        "changes": changes,
    }
    if profile_version is not None:
        body["expectedProfileVersion"] = profile_version
    return UpdateInputsRequest.model_validate(body)


def set_(field: str, value: Any) -> dict[str, Any]:
    return {"op": "set", "field": field, "value": value}


def clear(field: str) -> dict[str, Any]:
    return {"op": "clear", "field": field}


def money(value: object) -> Decimal:
    assert isinstance(value, Decimal)
    return value
