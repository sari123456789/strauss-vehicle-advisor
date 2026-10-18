"""Repository scoping on real PostgreSQL: an identifier alone never retrieves a resource.

This supports, but does not replace, Application authorization (3B): foreign resources are
simply not found through the repositories.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.business.application.ports.repositories import DuplicateResourceError
from app.business.domain.cases import FactPath, HypotheticalOverride
from tests.integration.business.database.case_helpers import (
    NOW,
    alternative,
    case,
    latest_release,
    new_owner_with_case,
    persistence,
    request,
    result_set,
    run_record,
)
from tests.integration.business.database.db_helpers import run

pytestmark = pytest.mark.sources


def test_cases_are_scoped_by_owner(business_db: str) -> None:
    async def scenario() -> None:
        async with persistence(business_db) as p:
            owner, mine = await new_owner_with_case(p)
            stranger, _ = await new_owner_with_case(p)
            async with p.uow() as uow:
                assert await uow.cases.get(mine.case_id, owner_subject_id=owner) == mine
                assert await uow.cases.get(mine.case_id, owner_subject_id=stranger) is None
                assert (
                    await uow.cases.get_by_conversation(
                        mine.conversation_id, owner_subject_id=stranger
                    )
                    is None
                )
                assert (
                    await uow.cases.get_by_conversation(
                        mine.conversation_id, owner_subject_id=owner
                    )
                    == mine
                )
                with pytest.raises(DuplicateResourceError):
                    await uow.cases.add(case(owner, conversation_id=mine.conversation_id))

    run(scenario())


def test_case_scoped_records_are_not_found_through_another_case(business_db: str) -> None:
    async def scenario() -> None:
        async with persistence(business_db) as p:
            owner, mine = await new_owner_with_case(p)
            _, other = await new_owner_with_case(p)
            release = await latest_release(p)
            alt = alternative(mine.case_id)
            rs = result_set(mine.case_id, release)
            record = run_record(mine.case_id, release, owner)
            req = request(mine.case_id, owner)
            async with p.uow() as uow:
                await uow.alternatives.add(alt)
                await uow.option_result_sets.add(rs)
                await uow.comparison_runs.add(record)
                await uow.representative_requests.add_or_get_open(req)
                await uow.commit()
            async with p.uow() as uow:
                assert await uow.alternatives.get(mine.case_id, alt.alternative_id) == alt
                assert await uow.alternatives.get(other.case_id, alt.alternative_id) is None
                assert await uow.option_result_sets.get(mine.case_id, rs.result_set_id) == rs
                assert await uow.option_result_sets.get(other.case_id, rs.result_set_id) is None
                assert await uow.comparison_runs.get(mine.case_id, record.run_id) == record
                assert await uow.comparison_runs.get(other.case_id, record.run_id) is None
                assert await uow.representative_requests.get(mine.case_id, req.request_id) == req
                assert await uow.representative_requests.get(other.case_id, req.request_id) is None
                assert await uow.comparison_runs.list_recent(other.case_id, 10) == ()

    run(scenario())


def test_case_round_trip_keeps_overrides_and_actual_facts_separate(business_db: str) -> None:
    async def scenario() -> None:
        async with persistence(business_db) as p:
            owner, stored = await new_owner_with_case(p)
            override = HypotheticalOverride(FactPath.BASE_GROSS, Decimal("20000.00"), NOW)
            async with p.uow() as uow:
                updated = await uow.cases.update(
                    replace(stored, hypothetical_overrides=(override,))
                )
                await uow.commit()
            async with p.uow() as uow:
                loaded = await uow.cases.get(stored.case_id, owner_subject_id=owner)
                profile = await uow.profiles.get(owner)
            assert loaded == updated and loaded.version == stored.version + 1
            assert loaded.hypothetical_overrides == (override,)
            assert loaded.actual_facts.salary.base_gross.value == Decimal("18500.00")
            assert profile is not None and profile.salary.base_gross.value == Decimal("18500.00")

    run(scenario())


def test_alternatives_are_deactivated_not_deleted(business_db: str) -> None:
    async def scenario() -> None:
        async with persistence(business_db) as p:
            _, mine = await new_owner_with_case(p)
            first = alternative(mine.case_id, "CAR-11")
            async with p.uow() as uow:
                await uow.alternatives.add(first)
                with pytest.raises(DuplicateResourceError):
                    await uow.alternatives.add(alternative(mine.case_id, "CAR-11"))
                await uow.commit()
            async with p.uow() as uow:
                await uow.alternatives.update(replace(first, active=False, deactivated_at=NOW))
                second = alternative(mine.case_id, "CAR-11")
                await uow.alternatives.add(second)
                await uow.commit()
            async with p.uow() as uow:
                active = await uow.alternatives.list_for_case(mine.case_id)
                every = await uow.alternatives.list_for_case(mine.case_id, active_only=False)
                assert [a.alternative_id for a in active] == [second.alternative_id]
                assert len(every) == 2
                assert await uow.alternatives.find_active(mine.case_id, "vehicle:CAR-11") == second

    run(scenario())


def test_runs_are_listed_newest_first_with_exact_decimal_strings(business_db: str) -> None:
    async def scenario() -> None:
        async with persistence(business_db) as p:
            owner, mine = await new_owner_with_case(p)
            release = await latest_release(p)
            older = run_record(mine.case_id, release, owner)
            newer = run_record(mine.case_id, release, owner, created_at=NOW.replace(hour=10))
            async with p.uow() as uow:
                await uow.comparison_runs.add(older)
                await uow.comparison_runs.add(newer)
                await uow.commit()
            async with p.uow() as uow:
                listed = await uow.comparison_runs.list_recent(mine.case_id, 5)
            assert [r.run_id for r in listed] == [newer.run_id, older.run_id]
            assert listed[0].result["net"]["raw"] == "11692.817000982123456789"  # type: ignore[index]

    run(scenario())
