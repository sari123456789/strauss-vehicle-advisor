"""Alternative selection helpers (used by comparisons in 4B) on real PostgreSQL."""

from uuid import uuid4

import pytest

from app.business.application.cases.alternatives import (
    deactivate_alternative,
    ensure_alternatives,
)
from app.business.application.errors import InvalidInput, NotFoundError
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.domain.cases import AlternativeKind
from app.business.domain.representatives import RequestStatus
from app.business.domain.vehicles import Track
from tests.integration.business.database.case_helpers import NOW, latest_release, request
from tests.integration.business.database.db_helpers import run
from tests.integration.business.database.service_helpers import (
    employee_with_profile,
    new_case,
    services,
)

pytestmark = pytest.mark.sources


def test_ensure_alternatives_is_idempotent_and_uses_real_catalogue_rows(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            ctx, _ = await new_case(s, who)
            release = await latest_release(s.p)
            keys = ("vehicle:CAR-10", "vehicle:CAR-19", "waiver")
            async with s.p.uow() as uow:
                _, case = await AuthorizationPolicy().tool_case(uow, ctx)
                first = await ensure_alternatives(uow, s.p.reference, release, case, keys, now=NOW)
                again = await ensure_alternatives(uow, s.p.reference, release, case, keys, now=NOW)
                await uow.commit()
            assert [a.alternative_id for a in first] == [a.alternative_id for a in again]
            assert [a.track for a in first] == [Track.REGULAR, Track.FAMILY, None]
            assert first[2].kind is AlternativeKind.WAIVER
            for bad in (("vehicle:CAR-99",), ("electric",)):
                async with s.p.uow() as uow:
                    _, case = await AuthorizationPolicy().tool_case(uow, ctx)
                    with pytest.raises(InvalidInput):
                        await ensure_alternatives(uow, s.p.reference, release, case, bad, now=NOW)

    run(scenario())


def test_deactivation_cancels_dependent_open_requests(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            ctx, _ = await new_case(s, who)
            release = await latest_release(s.p)
            async with s.p.uow() as uow:
                _, case = await AuthorizationPolicy().tool_case(uow, ctx)
                (alt,) = await ensure_alternatives(
                    uow, s.p.reference, release, case, ("vehicle:CAR-10",), now=NOW
                )
                dependent = request(case.case_id, who.subject_id, alt=alt)
                unrelated = request(case.case_id, who.subject_id, subject="CAR-14")
                await uow.representative_requests.add_or_get_open(dependent)
                await uow.representative_requests.add_or_get_open(unrelated)
                await uow.commit()
            async with s.p.uow() as uow:
                _, case = await AuthorizationPolicy().tool_case(uow, ctx)
                stored = await deactivate_alternative(uow, case, alt.alternative_id, now=NOW)
                with pytest.raises(NotFoundError):
                    await deactivate_alternative(uow, case, uuid4(), now=NOW)
                await uow.commit()
            assert not stored.active and stored.version == alt.version + 1
            async with s.p.uow() as uow:
                statuses = {
                    r.request_id: r.status
                    for r in await uow.representative_requests.list_for_case(ctx.case_id)
                }
            assert statuses == {
                dependent.request_id: RequestStatus.CANCELLED,
                unrelated.request_id: RequestStatus.OPEN,
            }

    run(scenario())
