"""Representative request claiming on real PostgreSQL (marker `db`, sources required).

Covers the business rules of claiming: one handler at a time, release back to the queue, answer
only by the handler (completing the assignment in the same transaction), cancellation of an
active assignment with its request, and no claim of a cancelled request. Concurrency tests run
the real races on separate transactions and check the invariants afterwards; they never mock
the database. Lock order under test: case -> request -> assignment (see ports/repositories.py).

Each request is created in its own case: an open request with the same case, alternative and
field is returned instead of a second one (idempotent creation).
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from app.business.application.cases.alternatives import deactivate_alternative
from app.business.application.errors import (
    ForbiddenError,
    InfrastructureUnavailable,
    InvalidInput,
    InvalidStateTransition,
    NotFoundError,
    VersionConflict,
)
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.representatives.answer_service import RepresentativeAnswerService
from app.business.application.representatives.assignment_service import AssignmentService
from app.business.infrastructure.clock import SystemClock
from app.contracts.representatives import (
    AssignmentFilter,
    ClaimRequest,
    ClaimResult,
    ReleaseRequest,
    RequestStatus,
)
from app.contracts.tool_context import Role
from tests.integration.business.database.case_helpers import new_user
from tests.integration.business.database.db_helpers import run, sql
from tests.integration.business.database.service_helpers import actor, services
from tests.integration.business.database.test_comparison_services import employee
from tests.integration.business.database.test_representative_answers import (
    PARTICIPATION,
    World,
    _FailingOutbox,
    ask,
    money,
    world,
)

pytestmark = pytest.mark.sources

OPTION = "vehicle:CAR-10"
EXPECTED = (InvalidStateTransition, ForbiddenError, VersionConflict)


async def _request(s: Any, w: World, claimed: bool = False) -> tuple[Any, Any, UUID]:
    """A new case with one open participation request (own employee, own case)."""
    who, ctx = await employee(s)
    request_id = await ask(w, ctx, PARTICIPATION, OPTION, claimed=claimed)
    return who, ctx, request_id


async def _new_rep(s: Any, name: str) -> Any:
    return actor(await new_user(s.p, Role.REPRESENTATIVE, name), Role.REPRESENTATIVE)


def _claim(service: AssignmentService, rep: Any, request_id: UUID, version: int = 0) -> Any:
    return service.claim_request(
        rep,
        request_id,
        ClaimRequest.model_validate(
            {"operationId": str(uuid4()), "expectedRequestVersion": version}
        ),
    )


def _release(service: AssignmentService, rep: Any, request_id: UUID, version: int) -> Any:
    return service.release_request(
        rep,
        request_id,
        ReleaseRequest.model_validate(
            {"operationId": str(uuid4()), "expectedAssignmentVersion": version}
        ),
    )


async def _cancel_option(s: Any, ctx: Any, who: Any) -> None:
    async with s.p.uow() as uow:
        case = await uow.cases.lock_by_id(ctx.case_id)
        assert case is not None
        alternative = await uow.alternatives.find_active(ctx.case_id, OPTION)
        assert alternative is not None
        await deactivate_alternative(
            uow, case, alternative.alternative_id, now=SystemClock().now(), actor=who
        )
        await uow.commit()


def _assignments(url: str, request_id: UUID) -> list[tuple[str, UUID]]:
    return sql(
        url,
        "SELECT status, claimed_by FROM business.request_assignments "
        "WHERE request_id = %s ORDER BY claimed_at, id",
        str(request_id),
    )


def _statuses(url: str, request_id: UUID) -> list[str]:
    return [status for status, _ in _assignments(url, request_id)]


def _request_status(url: str, request_id: UUID) -> str:
    return sql(
        url, "SELECT status FROM business.representative_requests WHERE id = %s", str(request_id)
    )[0][0]


def _assert_consistent(url: str, request_id: UUID) -> None:
    """The invariants every race must leave behind."""
    statuses = _statuses(url, request_id)
    assert statuses.count("active") <= 1, statuses
    state = _request_status(url, request_id)
    if state != RequestStatus.OPEN.value:
        assert "active" not in statuses, (state, statuses)
    if state == RequestStatus.ANSWERED.value:
        assert statuses[-1] == "completed", statuses


def _assert_only_expected_failures(results: list[object]) -> None:
    for result in results:
        if isinstance(result, BaseException):
            assert isinstance(result, EXPECTED), repr(result)  # never a deadlock or infra error
            assert not isinstance(result, InfrastructureUnavailable)


# --- business rules ---------------------------------------------------------------------


def test_claim_gives_one_handler_and_others_see_the_handler(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, _ctx, request_id = await _request(s, w)
            other = await _new_rep(s, "נציג/ת אחר")

            claimed = await _claim(w.claims, w.representative, request_id)
            assert isinstance(claimed, ClaimResult) and claimed.assignment_version == 0
            mine = await w.answers.get_request(w.representative, request_id)
            assert mine.handling_status == "assigned_to_me" and mine.can_answer
            assert mine.can_release and not mine.can_claim
            theirs = await w.answers.get_request(other, request_id)
            assert theirs.handling_status == "assigned_to_other" and not theirs.can_answer
            assert theirs.handler_display_name == "נציג/ת" and not theirs.can_claim
            with pytest.raises(InvalidStateTransition):
                await _claim(w.claims, other, request_id)
            assert _statuses(business_db, request_id) == ["active"]

    run(go())


def test_only_the_handler_can_answer_or_release(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, _ctx, request_id = await _request(s, w)
            other = await _new_rep(s, "נציג/ת אחר")
            await _claim(w.claims, w.representative, request_id)

            with pytest.raises(ForbiddenError):
                await w.answers.submit_answer(other, request_id, money("350"))
            with pytest.raises(ForbiddenError):
                await _release(w.claims, other, request_id, version=0)
            assert _statuses(business_db, request_id) == ["active"]
            assert _request_status(business_db, request_id) == "open"

    run(go())


def test_answering_an_unclaimed_request_is_rejected_and_stores_nothing(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, _ctx, request_id = await _request(s, w)
            with pytest.raises(InvalidStateTransition) as raised:
                await w.answers.submit_answer(w.representative, request_id, money("350"))
            assert raised.value.safe_details == {"reason": "must_claim_before_answering"}
            assert _assignments(business_db, request_id) == []
            assert _request_status(business_db, request_id) == "open"
            assert sql(
                business_db,
                "SELECT count(*) FROM business.representative_answers WHERE request_id = %s",
                str(request_id),
            ) == [(0,)]

    run(go())


def test_release_returns_the_request_to_the_queue_and_keeps_history(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, _ctx, request_id = await _request(s, w)
            other = await _new_rep(s, "נציג/ת אחר")
            claimed = await _claim(w.claims, w.representative, request_id)
            released = await _release(
                w.claims, w.representative, request_id, claimed.assignment_version
            )
            assert released.assignment_id == claimed.assignment_id
            assert _statuses(business_db, request_id) == ["released"]

            queue = await w.answers.list_requests(
                other,
                assignment_filter=AssignmentFilter.UNCLAIMED,
                search=str(request_id),
            )
            assert [v.request_id for v in queue] == [request_id]

            await _claim(w.claims, other, request_id)
            assert _statuses(business_db, request_id) == ["released", "active"]
            _assert_consistent(business_db, request_id)

    run(go())


def test_stale_assignment_version_and_double_release_are_rejected(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, _ctx, request_id = await _request(s, w)
            await _claim(w.claims, w.representative, request_id)
            with pytest.raises(VersionConflict):
                await _release(w.claims, w.representative, request_id, version=7)
            await _release(w.claims, w.representative, request_id, version=0)
            with pytest.raises(InvalidStateTransition):
                await _release(w.claims, w.representative, request_id, version=0)

    run(go())


def test_cancelled_request_cannot_be_claimed_and_the_cancel_is_audited(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            who, ctx, request_id = await _request(s, w)
            await _claim(w.claims, w.representative, request_id)
            await _cancel_option(s, ctx, who)

            assert _request_status(business_db, request_id) == "cancelled"
            assert _statuses(business_db, request_id) == ["cancelled"]
            with pytest.raises(InvalidStateTransition):
                await _claim(w.claims, await _new_rep(s, "נציג/ת אחר"), request_id)
            audit = sql(
                business_db,
                "SELECT actor_role, actor_subject_id FROM business.audit_entries "
                "WHERE request_id = %s AND action = 'representative_assignment.cancelled'",
                str(request_id),
            )
            assert audit == [("employee", who.subject_id)]
            _assert_consistent(business_db, request_id)

    run(go())


def test_claim_and_release_are_audited_with_the_representative_role(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, _ctx, request_id = await _request(s, w)
            claimed = await _claim(w.claims, w.representative, request_id)
            await _release(w.claims, w.representative, request_id, claimed.assignment_version)
            rows = sql(
                business_db,
                "SELECT action, actor_role, actor_subject_id FROM business.audit_entries "
                "WHERE request_id = %s ORDER BY occurred_at",
                str(request_id),
            )
            subject = w.representative.subject_id
            assert rows == [
                ("representative_assignment.claimed", "representative", subject),
                ("representative_assignment.released", "representative", subject),
            ]

    run(go())


def test_failed_answer_keeps_the_claim_and_success_completes_it(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, _ctx, request_id = await _request(s, w, claimed=True)

            def failing() -> Any:
                uow = s.p.uow()
                uow.outbox = _FailingOutbox()
                return uow

            broken = RepresentativeAnswerService(
                failing, AuthorizationPolicy(), s.p.reference, SystemClock()
            )
            with pytest.raises(RuntimeError, match="injected"):
                await broken.submit_answer(w.representative, request_id, money("350"))
            assert _statuses(business_db, request_id) == ["active"]
            assert _request_status(business_db, request_id) == "open"

            await w.answers.submit_answer(w.representative, request_id, money("350"))
            assert _statuses(business_db, request_id) == ["completed"]
            assert _request_status(business_db, request_id) == "answered"
            _assert_consistent(business_db, request_id)

    run(go())


def test_the_database_allows_only_one_active_assignment_per_request(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, _ctx, request_id = await _request(s, w, claimed=True)
            other = await _new_rep(s, "נציג/ת אחר")
            with pytest.raises(psycopg.errors.UniqueViolation):
                sql(
                    business_db,
                    "INSERT INTO business.request_assignments "
                    "(id, request_id, claimed_by, claimed_at, assignment_version, status) "
                    "VALUES (%s, %s, %s, now(), 0, 'active')",
                    str(uuid4()),
                    str(request_id),
                    str(other.subject_id),
                )

    run(go())


# --- concurrency -------------------------------------------------------------------------


def test_concurrent_claims_by_three_representatives_have_exactly_one_winner(
    business_db: str,
) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, _ctx, request_id = await _request(s, w)
            reps = [w.representative, await _new_rep(s, "ב"), await _new_rep(s, "ג")]
            results = await asyncio.gather(
                *(_claim(w.claims, rep, request_id) for rep in reps), return_exceptions=True
            )
            _assert_only_expected_failures(results)
            winners = [r for r in results if isinstance(r, ClaimResult)]
            losers = [r for r in results if isinstance(r, BaseException)]
            assert len(winners) == 1 and len(losers) == 2
            assert all(isinstance(r, InvalidStateTransition) for r in losers)
            assert _statuses(business_db, request_id) == ["active"]
            _assert_consistent(business_db, request_id)

    run(go())


def test_duplicate_claims_with_one_operation_id_return_one_assignment(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, _ctx, request_id = await _request(s, w)
            body = ClaimRequest.model_validate(
                {"operationId": str(uuid4()), "expectedRequestVersion": 0}
            )
            results = await asyncio.gather(
                *(w.claims.claim_request(w.representative, request_id, body) for _ in range(3)),
                return_exceptions=True,
            )
            assert all(isinstance(r, ClaimResult) for r in results), results
            assert len({r.assignment_id for r in results if isinstance(r, ClaimResult)}) == 1
            assert len(_assignments(business_db, request_id)) == 1

    run(go())


def test_claim_and_cancel_never_leave_an_active_assignment_on_a_cancelled_request(
    business_db: str,
) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            for _ in range(4):  # a fresh case per round: each round races a new request
                who, ctx, request_id = await _request(s, w)
                results = await asyncio.gather(
                    _claim(w.claims, w.representative, request_id),
                    _cancel_option(s, ctx, who),
                    return_exceptions=True,
                )
                assert not isinstance(results[1], BaseException), results[1]
                _assert_only_expected_failures(results[:1])
                assert _request_status(business_db, request_id) == "cancelled"
                assert "active" not in _statuses(business_db, request_id)
                _assert_consistent(business_db, request_id)

    run(go())


def test_answer_and_release_race_has_exactly_one_outcome(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            for _ in range(4):
                _who, _ctx, request_id = await _request(s, w, claimed=True)
                claimed_version = _claimed_version(business_db, request_id)
                results = await asyncio.gather(
                    w.answers.submit_answer(w.representative, request_id, money("350")),
                    _release(w.claims, w.representative, request_id, claimed_version),
                    return_exceptions=True,
                )
                assert sum(not isinstance(r, BaseException) for r in results) == 1, results
                _assert_only_expected_failures(results)
                _assert_consistent(business_db, request_id)
                assert _request_status(business_db, request_id) in ("answered", "open")

    run(go())


def test_release_and_claim_race_leaves_at_most_one_handler(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, _ctx, request_id = await _request(s, w, claimed=True)
            other = await _new_rep(s, "נציג/ת אחר")
            claimed_version = _claimed_version(business_db, request_id)
            results = await asyncio.gather(
                _release(w.claims, w.representative, request_id, claimed_version),
                _claim(w.claims, other, request_id),
                return_exceptions=True,
            )
            assert not isinstance(results[0], BaseException), results[0]  # the handler's release
            _assert_only_expected_failures(results[1:])
            _assert_consistent(business_db, request_id)
            active = [row for row in _assignments(business_db, request_id) if row[0] == "active"]
            assert len(active) == (0 if isinstance(results[1], BaseException) else 1)

    run(go())


def test_claim_and_answer_by_the_same_representative_are_consistent(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            for _ in range(4):
                _who, _ctx, request_id = await _request(s, w)
                results = await asyncio.gather(
                    _claim(w.claims, w.representative, request_id),
                    w.answers.submit_answer(w.representative, request_id, money("350")),
                    return_exceptions=True,
                )
                # The answer is rejected until the claim commits; it is never applied without it.
                _assert_only_expected_failures(results)
                _assert_consistent(business_db, request_id)
                if isinstance(results[1], BaseException):
                    assert _request_status(business_db, request_id) == "open"

    run(go())


def _claimed_version(url: str, request_id: UUID) -> int:
    return int(
        sql(
            url,
            "SELECT assignment_version FROM business.request_assignments "
            "WHERE request_id = %s AND status = 'active'",
            str(request_id),
        )[0][0]
    )


# --- queue views, filters, summary and paging --------------------------------------------


def test_summary_counts_follow_the_queue(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            other = await _new_rep(s, "נציג/ת אחר")
            before = await w.claims.get_summary(w.representative)
            _, _, _unclaimed = await _request(s, w)
            _, _, mine = await _request(s, w)
            _, _, answered = await _request(s, w)
            _, _, foreign = await _request(s, w)
            await _claim(w.claims, w.representative, mine)
            await _claim(w.claims, w.representative, answered)
            await w.answers.submit_answer(w.representative, answered, money("350"))
            await _claim(w.claims, other, foreign)

            after = await w.claims.get_summary(w.representative)
            assert after.total == before.total + 4
            assert after.unclaimed == before.unclaimed + 1
            assert after.assigned_to_me == before.assigned_to_me + 1
            assert after.assigned_to_others == before.assigned_to_others + 1
            assert after.answered == before.answered + 1

    run(go())


def test_queue_filters_search_and_keyset_paging(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            other = await _new_rep(s, "נציג/ת אחר")
            ids: list[UUID] = []
            for index in range(5):
                _, _, request_id = await _request(s, w)
                ids.append(request_id)
                if index % 2 == 0:
                    await _claim(w.claims, w.representative, request_id)
            mine = ids[::2]
            for request_id in ids:
                view = await w.answers.get_request(w.representative, request_id)
                expected = "assigned_to_me" if request_id in mine else "unclaimed"
                assert view.handling_status == expected

            only_mine = await w.answers.list_requests(
                w.representative, assignment_filter=AssignmentFilter.MINE, statuses=None, limit=200
            )
            assert all(v.handling_status == "assigned_to_me" for v in only_mine)
            unclaimed = await w.answers.list_requests(
                other, assignment_filter=AssignmentFilter.UNCLAIMED, statuses=None, limit=200
            )
            assert not {v.request_id for v in unclaimed} & set(mine)

            single = await w.answers.list_requests(other, search=str(ids[0]), statuses=None)
            assert [v.request_id for v in single] == [ids[0]]

            # Keyset paging with page size 2 returns each request once, in queue order.
            paged: list[UUID] = []
            cursor = None
            while True:
                page = await w.answers.list_requests(other, statuses=None, after_id=cursor, limit=2)
                if not page:
                    break
                paged.extend(v.request_id for v in page)
                cursor = page[-1].request_id
            assert len(paged) == len(set(paged))
            positions = [paged.index(request_id) for request_id in ids]
            assert positions == sorted(positions)

            with pytest.raises(InvalidInput):
                await w.answers.list_requests(other, after_id=uuid4(), limit=2)
            with pytest.raises(NotFoundError):
                await w.answers.get_request(other, uuid4())

    run(go())


async def _release(service: AssignmentService, rep: Any, request_id: UUID, version: int) -> Any:
    return await service.release_request(
        rep,
        request_id,
        ReleaseRequest.model_validate(
            {"operationId": str(uuid4()), "expectedAssignmentVersion": version}
        ),
    )
