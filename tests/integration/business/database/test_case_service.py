"""Step 3B CaseService / EmployeeProfileService on real PostgreSQL.

Covers AC15 (correction -> new versions, old runs stale), AC16 (concurrent updates), AC26/AC27
(foreign or invented identifiers -> NOT_FOUND before any business operation), AC31 (operation
payload mismatch), mandatory check 3 (actual persists across conversations, hypothetical never
touches the profile), B2/B4/B5/B7 and worker revalidation.
"""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from app.business.application.errors import (
    ForbiddenError,
    InvalidInput,
    InvalidStateTransition,
    NotFoundError,
    OutOfSupportedRange,
    PayloadMismatch,
    UnauthenticatedError,
    VersionConflict,
)
from app.business.domain.cases import FactPath
from app.business.domain.provenance import FieldStatus, SourceType
from app.contracts.case_lifecycle import CreateCaseCommand
from app.contracts.common import EnvelopeStatus, ErrorCode, Freshness, IssueCode
from app.contracts.tool_context import Origin, Role
from tests.integration.business.database.case_helpers import (
    latest_release,
    new_user,
    run_record,
)
from tests.integration.business.database.db_helpers import run, sql
from tests.integration.business.database.service_helpers import (
    actor,
    clear,
    employee_with_profile,
    new_case,
    services,
    set_,
    update,
)

pytestmark = pytest.mark.sources


def test_case_creation_is_idempotent_and_copies_the_latest_profile(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            command = CreateCaseCommand(
                conversation_id=uuid4(), calculation_month="2026-10", operation_id=uuid4()
            )
            first = await s.cases.create_case_for_conversation(who, command)
            again = await s.cases.create_case_for_conversation(who, command)
            other_op = await s.cases.create_case_for_conversation(
                who, command.model_copy(update={"operation_id": uuid4()})
            )
            assert first.created and first.salary_profile_version_used == 1
            assert again.case_id == other_op.case_id == first.case_id
            assert not again.created and not other_op.created
            with pytest.raises(PayloadMismatch):  # AC31
                await s.cases.create_case_for_conversation(
                    who, command.model_copy(update={"calculation_month": "2026-11"})
                )
            stranger = await employee_with_profile(s)
            with pytest.raises(NotFoundError):  # B7: foreign conversation
                await s.cases.create_case_for_conversation(
                    stranger, command.model_copy(update={"operation_id": uuid4()})
                )
            ctx, _ = await new_case(s, who)
            context = await s.cases.get_context(ctx)
            assert context.reference_date.isoformat() == "2026-10-01"
            assert context.profile.base_gross.value == "18500.00"

    run(scenario())


def test_lifecycle_adapter_returns_envelopes(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            command = CreateCaseCommand(
                conversation_id=uuid4(), calculation_month="2026-10", operation_id=uuid4()
            )
            ok = await s.lifecycle.create_case_for_conversation(who, command)
            assert ok.status is EnvelopeStatus.OK and ok.data is not None and ok.data.created
            rep = actor(await new_user(s.p, Role.REPRESENTATIVE), Role.REPRESENTATIVE)
            denied = await s.lifecycle.create_case_for_conversation(rep, command)
            assert denied.status is EnvelopeStatus.ERROR and denied.data is None
            assert denied.error is not None and denied.error.code is ErrorCode.FORBIDDEN

    run(scenario())


def test_actual_update_persists_across_conversations_and_stales_runs(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            old_ctx, _ = await new_case(s, who)
            ctx, created = await new_case(s, who)
            release = await latest_release(s.p)
            record = run_record(created.case_id, release, who.subject_id)
            async with s.p.uow() as uow:
                await uow.comparison_runs.add(record)
                await uow.commit()
            before = await s.cases.get_context(ctx)
            assert [r.freshness for r in before.recent_runs] == [Freshness.CURRENT]

            result = await s.cases.update_inputs(
                ctx,
                update(
                    "actual_update",
                    0,
                    [
                        set_("payroll.baseGross", "19000.00"),
                        set_("payroll.studyFundEmployeeRate", "0"),
                    ],
                    profile_version=1,
                ),
            )
            assert (result.new_case_version, result.new_profile_version) == (1, 2)
            assert result.stale_run_ids == [record.run_id]  # AC15
            assert result.conflicts == [] and result.missing_information == []

            stored = await s.profiles.get_profile(who)
            assert stored.profile_version == 2 and stored.base_gross.value == "19000.00"
            assert stored.study_fund_employee_rate.value == "0"  # AC06: zero is valid
            assert stored.base_gross.last_confirmed_at is None
            after = await s.cases.get_context(ctx)
            assert after.salary_profile_version_used == 2 and not after.profile_mismatch
            assert [r.freshness for r in after.recent_runs] == [Freshness.STALE]
            older = await s.cases.get_context(old_ctx)  # older conversation keeps its copy
            assert older.profile_mismatch and older.salary_profile_version_used == 1
            fresh_ctx, fresh = await new_case(s, who)  # mandatory check 3
            assert fresh.salary_profile_version_used == 2
            assert (await s.cases.get_context(fresh_ctx)).profile.base_gross.value == "19000.00"

    run(scenario())


def test_hypothetical_updates_never_touch_the_profile(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            ctx, _ = await new_case(s, who)
            result = await s.cases.update_inputs(
                ctx,
                update("hypothetical", 0, [set_("payroll.baseGross", "25000.00")]),
            )
            assert result.new_case_version == 1 and result.new_profile_version is None
            profile = await s.profiles.get_profile(who)
            assert profile.profile_version == 1 and profile.base_gross.value == "18500.00"
            context = await s.cases.get_context(ctx)
            assert [(o.field.value, o.value) for o in context.hypothetical_overrides] == [
                ("payroll.baseGross", "25000.00")
            ]
            cleared = await s.cases.update_inputs(
                ctx, update("hypothetical", 1, [clear("payroll.baseGross")])
            )
            assert cleared.new_case_version == 2
            assert (await s.cases.get_context(ctx)).hypothetical_overrides == []  # B4

    run(scenario())


def test_clear_makes_a_fact_missing_never_zero(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            ctx, _ = await new_case(s, who)
            result = await s.cases.update_inputs(
                ctx,
                update("actual_update", 0, [clear("payroll.commissionsGross")], profile_version=1),
            )
            assert [(i.code, i.field) for i in result.missing_information] == [
                (IssueCode.MISSING_INPUT, "payroll.commissionsGross")
            ]
            async with s.p.uow() as uow:
                stored = await uow.profiles.get(who.subject_id)
            assert stored is not None
            field = stored.salary.commissions_gross
            assert field.status is FieldStatus.MISSING and field.value is None
            assert field.source is SourceType.EMPLOYEE and field.revision == 2

    run(scenario())


def test_new_employee_profile_starts_at_version_zero(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = actor(await new_user(s.p))
            empty = await s.profiles.get_profile(who)  # B2
            assert empty.profile_version == 0 and empty.base_gross.status.value == "missing"
            ctx, created = await new_case(s, who)
            assert created.salary_profile_version_used == 0
            result = await s.cases.update_inputs(
                ctx,
                update("actual_update", 0, [set_("payroll.baseGross", "9800")], profile_version=0),
            )
            assert result.new_profile_version == 1
            assert (await s.profiles.get_profile(who)).base_gross.value == "9800"

    run(scenario())


def test_stale_versions_and_concurrent_updates_conflict(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            ctx, _ = await new_case(s, who)
            with pytest.raises(VersionConflict):
                await s.cases.update_inputs(
                    ctx,
                    update(
                        "actual_update", 5, [set_("payroll.bonusGross", "1")], profile_version=1
                    ),
                )
            with pytest.raises(VersionConflict):
                await s.cases.update_inputs(
                    ctx,
                    update(
                        "actual_update", 0, [set_("payroll.bonusGross", "1")], profile_version=9
                    ),
                )

            async def attempt(amount: str) -> str:
                try:
                    await s.cases.update_inputs(
                        ctx,
                        update(
                            "actual_update",
                            0,
                            [set_("payroll.baseGross", amount)],
                            profile_version=1,
                        ),
                    )
                except VersionConflict:
                    return "conflict"
                return amount

            outcomes = await asyncio.gather(attempt("21000"), attempt("22000"))  # AC16
            assert outcomes.count("conflict") == 1
            profile = await s.profiles.get_profile(who)
            assert profile.profile_version == 2
            assert profile.base_gross.value == next(o for o in outcomes if o != "conflict")

    run(scenario())


def test_operation_replay_and_payload_mismatch(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            ctx, _ = await new_case(s, who)
            operation = uuid4()
            request = update(
                "actual_update",
                0,
                [set_("payroll.bonusGross", "500")],
                profile_version=1,
                operation_id=operation,
            )
            first, second = await asyncio.gather(
                s.cases.update_inputs(ctx, request), s.cases.update_inputs(ctx, request)
            )
            assert first == second  # one execution, the other is a replay
            assert (await s.profiles.get_profile(who)).profile_version == 2
            assert await s.cases.update_inputs(ctx, request) == first
            with pytest.raises(PayloadMismatch):  # AC31
                await s.cases.update_inputs(
                    ctx,
                    update(
                        "actual_update",
                        0,
                        [set_("payroll.bonusGross", "600")],
                        profile_version=1,
                        operation_id=operation,
                    ),
                )

    run(scenario())


def test_foreign_or_invented_identifiers_are_not_found(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            owner = await employee_with_profile(s)
            ctx, _ = await new_case(s, owner)
            stranger = await employee_with_profile(s)
            probes = [
                ctx.model_copy(update={"actor": stranger}),  # AC26
                ctx.model_copy(update={"case_id": uuid4()}),  # AC27
                ctx.model_copy(update={"conversation_id": uuid4()}),
            ]
            for probe in probes:
                with pytest.raises(NotFoundError) as caught:
                    await s.cases.get_context(probe)
                assert caught.value.safe_details == {}
                with pytest.raises(NotFoundError):
                    await s.cases.update_inputs(
                        probe,
                        update("hypothetical", 0, [set_("payroll.baseGross", "1")]),
                    )
            assert (await s.cases.get_context(ctx)).case_version == 0  # nothing changed

    run(scenario())


def test_non_employees_and_inactive_or_demoted_accounts_are_rejected(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            ctx, _ = await new_case(s, who)
            for role in (Role.REPRESENTATIVE, Role.IMPORT_OPERATOR):
                other = actor(await new_user(s.p, role), role)
                with pytest.raises(ForbiddenError):
                    await s.profiles.get_profile(other)
            worker = await s.identity.resolve(who.subject_id, Origin.WORKER)
            assert worker.origin is Origin.WORKER and worker.roles == frozenset({Role.EMPLOYEE})
            worker_ctx = ctx.model_copy(update={"actor": worker})
            assert (await s.cases.get_context(worker_ctx)).case_id == ctx.case_id
            sql(
                business_db,
                "UPDATE auth.users SET roles = ARRAY['representative'] WHERE id = %s",
                who.subject_id,
            )
            with pytest.raises(ForbiddenError):  # the stale employee claim is ignored
                await s.cases.get_context(worker_ctx)
            sql(
                business_db,
                "UPDATE auth.users SET roles = ARRAY['employee'], active = false WHERE id = %s",
                who.subject_id,
            )
            with pytest.raises(UnauthenticatedError):  # revalidated on every operation
                await s.cases.get_context(worker_ctx)
            with pytest.raises(UnauthenticatedError):
                await s.identity.resolve(who.subject_id, Origin.WORKER)

    run(scenario())


def test_invalid_identifiers_or_values_reject_the_whole_list(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            ctx, _ = await new_case(s, who)
            invalid_lists = [
                [set_("payroll.bonusGross", "100"), set_("employee.occupationId", "OCC-UNKNOWN")],
                [
                    set_("payroll.bonusGross", "100"),
                    set_("currentArrangement.vehicle", {"catalogueId": "CAR-99"}),
                ],
                [
                    set_("payroll.bonusGross", "100"),
                    set_(
                        "currentArrangement.vehicle",
                        {
                            "taxKey": {
                                "taxYear": "2026",
                                "registrationYear": "2025",
                                "vehicleTypeCode": "9",
                                "manufacturerCode": "01014",
                                "modelCode": "00003",
                            }
                        },
                    ),
                ],
            ]
            for changes in invalid_lists:
                with pytest.raises(InvalidInput):
                    await s.cases.update_inputs(
                        ctx, update("actual_update", 0, changes, profile_version=1)
                    )
            with pytest.raises(OutOfSupportedRange):
                await s.cases.update_inputs(
                    ctx,
                    update(
                        "actual_update",
                        0,
                        [set_("payroll.baseGross", "1000001")],
                        profile_version=1,
                    ),
                )
            profile = await s.profiles.get_profile(who)
            assert profile.profile_version == 1 and profile.bonus_gross.status.value == "missing"
            ok = await s.cases.update_inputs(
                ctx,
                update(
                    "actual_update",
                    0,
                    [
                        set_(
                            "currentArrangement.vehicle",
                            {
                                "taxKey": {
                                    "taxYear": "2026",
                                    "registrationYear": "2025",
                                    "vehicleTypeCode": "1",
                                    "manufacturerCode": "01014",
                                    "modelCode": "00003",
                                }
                            },
                        )
                    ],
                    profile_version=1,
                ),
            )
            assert ok.new_profile_version == 2
            vehicle = (await s.profiles.get_profile(who)).current_arrangement.vehicle.value
            assert vehicle is not None and vehicle.tax_key is not None
            assert vehicle.tax_key.manufacturer_code == "01014"  # leading zeros kept

    run(scenario())


def test_occupation_group_conflict_is_reported_not_overwritten(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)  # group ג
            ctx, _ = await new_case(s, who)
            result = await s.cases.update_inputs(
                ctx,
                update(
                    "actual_update",
                    0,
                    [set_("employee.occupationId", "OCC-SOFTWARE-DEV")],  # group ב in the table
                    profile_version=1,
                ),
            )
            assert [(c.code, c.reason) for c in result.conflicts] == [
                (IssueCode.SOURCE_CONFLICT, "occupation_group_conflict")
            ]
            profile = await s.profiles.get_profile(who)
            assert profile.entitlement_group.value == "ג"
            assert profile.occupation_id.value == "OCC-SOFTWARE-DEV"

    run(scenario())


def test_closed_cases_are_read_only(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            ctx, _ = await new_case(s, who)
            sql(
                business_db,
                "UPDATE business.cases SET lifecycle_status = 'closed' WHERE id = %s",
                ctx.case_id,
            )
            with pytest.raises(InvalidStateTransition):
                await s.cases.update_inputs(
                    ctx, update("hypothetical", 0, [set_("payroll.baseGross", "1")])
                )
            assert (await s.cases.get_context(ctx)).case_id == ctx.case_id

    run(scenario())


def test_decimal_precision_is_preserved_end_to_end(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who = await employee_with_profile(s)
            ctx, _ = await new_case(s, who)
            await s.cases.update_inputs(
                ctx,
                update(
                    "actual_update",
                    0,
                    [
                        set_("payroll.baseGross", "18500.123456789"),
                        set_("payroll.pensionEmployeeRate", "0.0655"),
                    ],
                    profile_version=1,
                ),
            )
            async with s.p.uow() as uow:
                stored = await uow.profiles.get(who.subject_id)
            assert stored is not None
            assert stored.salary.base_gross.value == Decimal("18500.123456789")
            assert stored.salary.pension_employee_rate.value == Decimal("0.0655")
            context = await s.cases.get_context(ctx)
            assert context.profile.base_gross.value == "18500.123456789"
            assert FactPath.BASE_GROSS.value == "payroll.baseGross"

    run(scenario())
