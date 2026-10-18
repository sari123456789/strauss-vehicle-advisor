"""Optimistic concurrency on real PostgreSQL: stale writes fail, concurrent writes from the same
version never both succeed (AC16), and a failed case write rolls back the profile write.
"""

import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest

from app.business.application.ports.repositories import VersionConflictError
from app.business.domain.cases import FactPath, with_profile_fact
from app.business.domain.employee_profiles import EmployeeProfile
from tests.integration.business.database.case_helpers import (
    Persistence,
    new_owner_with_case,
    persistence,
    usable,
)
from tests.integration.business.database.db_helpers import run


def _with_base(profile: EmployeeProfile, amount: str) -> EmployeeProfile:
    return with_profile_fact(profile, FactPath.BASE_GROSS, usable(Decimal(amount)))


async def _profile(p: Persistence, owner: object) -> EmployeeProfile:
    async with p.uow() as uow:
        stored = await uow.profiles.get(owner)  # type: ignore[arg-type]
    assert stored is not None
    return stored


def test_stale_profile_write_is_rejected_and_changes_nothing(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            owner, _ = await new_owner_with_case(p)
            original = await _profile(p, owner)
            async with p.uow() as uow:
                updated = await uow.profiles.update(_with_base(original, "19000.00"))
                await uow.commit()
            assert updated.version == original.version + 1
            async with p.uow() as uow:
                with pytest.raises(VersionConflictError):
                    await uow.profiles.update(_with_base(original, "20000.00"))
            current = await _profile(p, owner)
            assert current.version == updated.version
            assert current.salary.base_gross.value == Decimal("19000.00")

    run(scenario())


def test_concurrent_updates_from_the_same_version_never_both_succeed(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            owner, _ = await new_owner_with_case(p)
            read = await _profile(p, owner)
            both_read = asyncio.Barrier(2)

            async def write(amount: str) -> str:
                async with p.uow() as uow:
                    await both_read.wait()
                    try:
                        await uow.profiles.update(_with_base(read, amount))
                    except VersionConflictError:
                        return "conflict"
                    await asyncio.sleep(0.2)  # hold the row lock while the other waits
                    await uow.commit()
                    return amount

            outcomes = await asyncio.gather(write("21000.00"), write("22000.00"))
            assert sorted(outcomes, key=str)[-1] == "conflict"
            winner = next(o for o in outcomes if o != "conflict")
            final = await _profile(p, owner)
            assert final.version == read.version + 1
            assert final.salary.base_gross.value == Decimal(winner)

    run(scenario())


def test_case_conflict_rolls_back_the_profile_update(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            owner, stored_case = await new_owner_with_case(p)
            profile = await _profile(p, owner)
            async with p.uow() as uow:
                await uow.cases.update(stored_case)
                await uow.commit()
            with pytest.raises(VersionConflictError):
                async with p.uow() as uow:
                    new_profile = await uow.profiles.update(_with_base(profile, "25000.00"))
                    await uow.cases.update(replace(stored_case, actual_facts=new_profile))
                    await uow.commit()
            assert (await _profile(p, owner)) == profile

    run(scenario())


def test_profile_then_case_lock_order_has_no_deadlock(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            owner, stored_case = await new_owner_with_case(p)
            profile = await _profile(p, owner)
            start = asyncio.Barrier(2)

            async def actual_update(amount: str) -> str:
                async with p.uow() as uow:
                    await start.wait()
                    try:
                        new_profile = await uow.profiles.update(_with_base(profile, amount))
                        await asyncio.sleep(0.1)
                        await uow.cases.update(replace(stored_case, actual_facts=new_profile))
                    except VersionConflictError:
                        return "conflict"
                    await uow.commit()
                    return amount

            outcomes = await asyncio.wait_for(
                asyncio.gather(actual_update("30000.00"), actual_update("31000.00")), timeout=20
            )
            assert outcomes.count("conflict") == 1
            async with p.uow() as uow:
                saved = await uow.cases.get(stored_case.case_id, owner_subject_id=owner)
            assert saved is not None and saved.version == stored_case.version + 1
            assert saved.salary_profile_version_used == profile.version + 1

    run(scenario())
