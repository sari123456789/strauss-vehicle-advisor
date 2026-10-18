"""Representative requests on real PostgreSQL: idempotent creation, one open request per
subject, optimistic status updates and Hebrew question text.
"""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from app.business.application.ports.repositories import VersionConflictError
from app.business.domain.representatives import RequestStatus
from tests.integration.business.database.case_helpers import (
    NOW,
    alternative,
    new_owner_with_case,
    persistence,
    request,
)
from tests.integration.business.database.db_helpers import run, sql


def test_creation_is_idempotent_and_deduplicates_open_subjects(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            owner, mine = await new_owner_with_case(p)
            key = uuid4()
            first = request(mine.case_id, owner, key=key)
            async with p.uow() as uow:
                stored, created = await uow.representative_requests.add_or_get_open(first)
                assert created and stored == first
                again, created = await uow.representative_requests.add_or_get_open(
                    request(mine.case_id, owner, key=key)
                )
                assert not created and again == first
                same_subject, created = await uow.representative_requests.add_or_get_open(
                    request(mine.case_id, owner)
                )
                assert not created and same_subject == first
                other, created = await uow.representative_requests.add_or_get_open(
                    request(mine.case_id, owner, subject="CAR-11")
                )
                assert created and other.request_id != first.request_id
                await uow.commit()

    run(scenario())


def test_concurrent_equivalent_requests_create_one_open_request(migrated_db: str) -> None:
    async def scenario() -> tuple[object, object]:
        async with persistence(migrated_db) as p:
            owner, mine = await new_owner_with_case(p)

            async def create() -> bool:
                async with p.uow() as uow:
                    _, created = await uow.representative_requests.add_or_get_open(
                        request(mine.case_id, owner, subject="CAR-14")
                    )
                    await asyncio.sleep(0.2)
                    await uow.commit()
                    return created

            return tuple(await asyncio.gather(create(), create()))  # type: ignore[return-value]

    outcomes = run(scenario())
    assert sorted(outcomes) == [False, True]  # type: ignore[type-var]


def test_status_updates_are_versioned_and_reopen_a_new_request(migrated_db: str) -> None:
    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            owner, mine = await new_owner_with_case(p)
            alt = alternative(mine.case_id)
            original = request(mine.case_id, owner, alt=alt)
            async with p.uow() as uow:
                await uow.alternatives.add(alt)
                await uow.representative_requests.add_or_get_open(original)
                await uow.commit()
            async with p.uow() as uow:
                cancelled = await uow.representative_requests.update(
                    original.with_status(RequestStatus.CANCELLED, at=NOW)
                )
                await uow.commit()
            assert cancelled.request_version == original.request_version + 1
            async with p.uow() as uow:
                with pytest.raises(VersionConflictError):
                    await uow.representative_requests.update(
                        replace(original, status=RequestStatus.CANCELLED)
                    )
            async with p.uow() as uow:
                reopened, created = await uow.representative_requests.add_or_get_open(
                    request(mine.case_id, owner, alt=alt)
                )
                assert created
                await uow.commit()
            async with p.uow() as uow:
                open_only = await uow.representative_requests.list_for_case(
                    mine.case_id, [RequestStatus.OPEN]
                )
                every = await uow.representative_requests.list_for_case(mine.case_id)
            assert [r.request_id for r in open_only] == [reopened.request_id]
            assert len(every) == 2

    run(scenario())


def test_hebrew_question_text_round_trips_exactly(migrated_db: str) -> None:
    question = "  שלום! מה ההשתתפות בקרניבל (CAR-19)? ₪ 1,150 — בדיקה  "

    async def scenario() -> None:
        async with persistence(migrated_db) as p:
            owner, mine = await new_owner_with_case(p)
            stored = request(mine.case_id, owner, question=question)
            async with p.uow() as uow:
                await uow.representative_requests.add_or_get_open(stored)
                await uow.commit()
            async with p.uow() as uow:
                loaded = await uow.representative_requests.get(mine.case_id, stored.request_id)
            assert loaded is not None and loaded.question_text == question

    run(scenario())
    assert sql(migrated_db, "SHOW server_encoding") == [("UTF8",)]
