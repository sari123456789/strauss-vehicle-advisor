"""Alternative selection inside an already authorized case (internal; used by comparisons, 4B).

Callers pass a case obtained through `AuthorizationPolicy` in the same Unit of Work; these
helpers never look a case up by ID themselves. Only real catalogue vehicles (or the waiver)
become alternatives - never a list proposed by a model. Alternatives are deactivated, never
deleted; deactivation cancels open representative requests tied to that alternative, and also
cancels any active assignment on those requests atomically.
"""

from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

from app.business.application.errors import InvalidInput, NotFoundError
from app.business.application.ports.reference_data import PublishedRelease, ReferenceDataRepository
from app.business.application.ports.unit_of_work import BusinessUnitOfWork
from app.business.application.representatives.assignment_service import cancel_active_assignment
from app.business.domain.arrangements import WAIVER_OPTION_KEY
from app.business.domain.cases import Alternative, AlternativeKind, ComparisonCase
from app.business.domain.representatives import RequestStatus
from app.contracts.tool_context import ActorRef

VEHICLE_PREFIX = "vehicle:"


async def ensure_alternatives(
    uow: BusinessUnitOfWork,
    reference_data: ReferenceDataRepository,
    release: PublishedRelease,
    case: ComparisonCase,
    option_keys: tuple[str, ...],
    *,
    now: datetime,
) -> tuple[Alternative, ...]:
    """Return the active alternative for each option key, creating missing ones (idempotent)."""
    result: list[Alternative] = []
    for option_key in option_keys:
        existing = await uow.alternatives.find_active(case.case_id, option_key)
        if existing is not None:
            result.append(existing)
            continue
        if option_key == WAIVER_OPTION_KEY:
            alternative = Alternative(
                alternative_id=uuid4(),
                case_id=case.case_id,
                version=0,
                kind=AlternativeKind.WAIVER,
                option_key=option_key,
                catalogue_id=None,
                track=None,
                active=True,
                created_at=now,
                updated_at=now,
            )
        elif option_key.startswith(VEHICLE_PREFIX):
            catalogue_id = option_key.removeprefix(VEHICLE_PREFIX)
            vehicle = await reference_data.get_vehicle(release.catalogue_version_id, catalogue_id)
            if vehicle is None:
                raise InvalidInput("unknown catalogue vehicle", safe_details={"field": option_key})
            alternative = Alternative(
                alternative_id=uuid4(),
                case_id=case.case_id,
                version=0,
                kind=AlternativeKind.VEHICLE,
                option_key=option_key,
                catalogue_id=catalogue_id,
                track=vehicle.track,
                active=True,
                created_at=now,
                updated_at=now,
            )
        else:
            raise InvalidInput("unknown option key", safe_details={"field": option_key})
        await uow.alternatives.add(alternative)
        result.append(alternative)
    return tuple(result)


async def deactivate_alternative(
    uow: BusinessUnitOfWork,
    case: ComparisonCase,
    alternative_id: UUID,
    *,
    now: datetime,
    actor: ActorRef,
) -> Alternative:
    """Lock order (global): the caller must already hold the case lock (`cases.lock`). Each open
    request of the alternative is then locked, cancelled, and its active assignment (if any) is
    cancelled in the same transaction. Not called by any use case yet."""
    alternative = await uow.alternatives.get(case.case_id, alternative_id)
    if alternative is None:
        raise NotFoundError()
    if not alternative.active:
        return alternative
    stored = await uow.alternatives.update(
        replace(alternative, active=False, deactivated_at=now, updated_at=now)
    )
    for request in await uow.representative_requests.list_for_case(
        case.case_id, [RequestStatus.OPEN]
    ):
        if request.alternative_id != alternative_id:
            continue
        locked = await uow.representative_requests.lock(request.request_id)
        if locked is None or locked.status is not RequestStatus.OPEN:
            continue
        cancelled = locked.with_status(RequestStatus.CANCELLED, at=now)
        await uow.representative_requests.update(cancelled)
        await cancel_active_assignment(uow, actor, request.request_id, case.case_id, now=now)
    return stored
