"""Step 4A on real PostgreSQL and the supplied sources: listing, eligibility, vehicle
resolution, value of use, occupation resolution (G2), representative request status (G1),
field confirmation (G3) and the case-relative confirmation hint (G4).

Scenarios: T01-T05, T07, T20-T29, T44, AC11; authorization (foreign/invented -> NOT_FOUND,
representative -> FORBIDDEN, inactive -> UNAUTHENTICATED); immutability and dedupe of result
sets (A12); closed case (A9).
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.business.application.errors import (
    ForbiddenError,
    InvalidStateTransition,
    NotFoundError,
    UnauthenticatedError,
    VersionConflict,
)
from app.business.domain.cases import FactPath
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.provenance import FieldValue
from app.business.domain.rules import EntitlementGroup
from app.contracts.case_inputs import ConfirmProfileFieldsRequest
from app.contracts.catalogue import (
    EligibilityReason,
    EligibilityRequest,
    EligibilityStatus,
    ListOptionsRequest,
    ResolveOccupationRequest,
    ResolveStatus,
    ResolveVehicleRequest,
    TaxValueRequest,
)
from app.contracts.common import EnvelopeStatus, ErrorCode, FieldPath, IssueCode, OptionCategory
from app.contracts.representatives import RepresentativeRequestLookup
from app.contracts.tool_context import ActorRef, Role, ToolContext
from tests.integration.business.database.case_helpers import new_user, profile, request, usable
from tests.integration.business.database.db_helpers import run, sql
from tests.integration.business.database.service_helpers import (
    Services,
    actor,
    new_case,
    services,
    set_,
    update,
)

pytestmark = pytest.mark.sources

ALL = [OptionCategory.REGULAR, OptionCategory.FAMILY]


async def employee(
    s: Services,
    *,
    group: EntitlementGroup | None = EntitlementGroup.BET,
    children: int | None = None,
    occupation: str | None = None,
) -> tuple[ActorRef, ToolContext]:
    subject = await new_user(s.p)
    facts: EmployeeProfile = replace(
        profile(subject),
        entitlement_group=FieldValue() if group is None else usable(group),
        qualifying_children_count=FieldValue() if children is None else usable(children),
        occupation_id=FieldValue() if occupation is None else usable(occupation),
    )
    async with s.p.uow() as uow:
        await uow.profiles.add(facts)
        await uow.commit()
    who = actor(subject)
    ctx, _ = await new_case(s, who)
    return who, ctx


def listing(*categories: OptionCategory, **body: Any) -> ListOptionsRequest:
    return ListOptionsRequest.model_validate({"categories": list(categories or ALL), **body})


def ids(items: list[Any]) -> list[str | None]:
    return [item.catalogue_id for item in items]


def cars(*numbers: int) -> list[str]:
    return [f"CAR-{n:02d}" for n in numbers]


# --- list_eligible_options --------------------------------------------------------------


def test_regular_listing_follows_own_group_or_lower(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            for group, last in (
                (EntitlementGroup.BET, 8),
                (EntitlementGroup.GIMEL, 13),
                (EntitlementGroup.DALET, 18),
            ):
                _, ctx = await employee(s, group=group)
                out = await s.catalogue.list_eligible_options(ctx, listing(OptionCategory.REGULAR))
                result = out.data
                assert ids(result.items) == cars(*range(1, last + 1))  # T01-T03, no duplicates
                assert all(i.eligibility is EligibilityStatus.ELIGIBLE for i in result.items)
                assert result.scope_complete and result.unchecked_categories == []
                assert out.status is EnvelopeStatus.OK and out.issues == ()
            item = result.items[9]  # CAR-10 for group ד: lower group, catalogue values
            assert item.reasons == [EligibilityReason.GROUP_LOWER]
            assert item.value_of_use is not None and Decimal(item.value_of_use) == Decimal("3130")
            assert item.group_budget is not None and item.track is not None

    run(scenario())


def test_family_track_needs_four_children_and_unknown_count_is_unchecked(
    business_db: str,
) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, four = await employee(s, group=EntitlementGroup.BET, children=4)
            result = (await s.catalogue.list_eligible_options(four, listing())).data
            assert ids(result.items) == cars(*range(1, 9), 19, 20)  # T04
            assert result.scope_complete
            _, three = await employee(s, group=EntitlementGroup.BET, children=3)
            result = (await s.catalogue.list_eligible_options(three, listing())).data
            assert ids(result.items) == cars(*range(1, 9))  # T05: no family eligibility
            _, unknown = await employee(s, group=EntitlementGroup.GIMEL, children=None)
            out = await s.catalogue.list_eligible_options(unknown, listing())
            result = out.data
            regular = [i for i in result.items if i.category is OptionCategory.REGULAR]
            family = [i for i in result.items if i.category is OptionCategory.FAMILY]
            assert ids(regular) == cars(*range(1, 14))  # T07
            assert all(i.eligibility is EligibilityStatus.NEEDS_INFORMATION for i in family)
            assert result.unchecked_categories == [OptionCategory.FAMILY]
            assert not result.scope_complete and out.status is EnvelopeStatus.PARTIAL
            assert [(i.code, i.field) for i in result.missing_information] == [
                (IssueCode.MISSING_INPUT, FieldPath.QUALIFYING_CHILDREN_COUNT)
            ]  # reported once, not per vehicle; salary is never asked

    run(scenario())


def test_filters_and_hypothetical_group_override(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who, ctx = await employee(s, group=EntitlementGroup.BET)
            electric = {"filters": {"propulsion": ["electric"]}}
            result = (
                await s.catalogue.list_eligible_options(
                    ctx, listing(OptionCategory.REGULAR, **electric)
                )
            ).data
            assert ids(result.items) == cars(3, 6, 8)  # T44 for ב
            await s.cases.update_inputs(
                ctx, update("hypothetical", 0, [set_("employee.entitlementGroup", "ג")])
            )
            result = (
                await s.catalogue.list_eligible_options(
                    ctx, listing(OptionCategory.REGULAR, **electric)
                )
            ).data
            assert ids(result.items) == cars(3, 6, 8, 10, 11)  # T44 for ג (scenario only)
            stored = await s.profiles.get_profile(who)
            assert stored.entitlement_group.value == "ב"  # the profile is never touched
            groups = {"filters": {"groups": ["א"]}}
            result = (await s.catalogue.list_eligible_options(ctx, listing(**groups))).data
            assert ids(result.items) == cars(1, 2, 3, 4, 19, 20)  # family is not a ranked group
            family = result.items[-2:]
            assert all(i.eligibility is EligibilityStatus.NEEDS_INFORMATION for i in family)

    run(scenario())


def test_result_sets_are_immutable_and_identical_retries_are_deduplicated(
    business_db: str,
) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s, group=EntitlementGroup.BET)
            body = listing(OptionCategory.REGULAR)
            first = (await s.catalogue.list_eligible_options(ctx, body)).data
            again = (await s.catalogue.list_eligible_options(ctx, body)).data
            assert again.result_set_id == first.result_set_id and again.items == first.items
            concurrent = await asyncio.gather(
                *(
                    s.catalogue.list_eligible_options(
                        ctx, listing(OptionCategory.FAMILY, OptionCategory.REGULAR)
                    )
                    for _ in range(4)
                )
            )
            assert len({o.data.result_set_id for o in concurrent}) == 1
            other = (await s.catalogue.list_eligible_options(ctx, listing())).data
            assert other.result_set_id != first.result_set_id
            await s.cases.update_inputs(
                ctx, update("hypothetical", 0, [set_("employee.entitlementGroup", "ג")])
            )
            changed = (await s.catalogue.list_eligible_options(ctx, body)).data
            assert changed.result_set_id != first.result_set_id  # new case version, new set
            async with s.p.uow() as uow:
                old = await uow.option_result_sets.get(ctx.case_id, first.result_set_id)
            assert old is not None and len(old.items) == 8  # the displayed list is kept
            with pytest.raises(VersionConflict):
                await s.catalogue.list_eligible_options(
                    ctx, listing(OptionCategory.REGULAR, expectedCaseVersion=0)
                )
            count = sql(
                business_db,
                "SELECT count(*) FROM business.option_result_sets WHERE case_id = %s",
                ctx.case_id,
            )
            assert count == [(4,)]

    run(scenario())


def test_closed_case_cannot_list(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s)
            sql(
                business_db,
                "UPDATE business.cases SET lifecycle_status = 'closed' WHERE id = %s",
                ctx.case_id,
            )
            with pytest.raises(InvalidStateTransition):  # A9
                await s.catalogue.list_eligible_options(ctx, listing())

    run(scenario())


# --- waiver and eligibility -------------------------------------------------------------


def test_waiver_follows_the_occupation_table(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            waiver = listing(OptionCategory.WAIVER)
            _, mandatory = await employee(s, occupation="OCC-FIELD-SALES-REP")
            item = (await s.catalogue.list_eligible_options(mandatory, waiver)).data.items[0]
            assert item.eligibility is EligibilityStatus.INELIGIBLE  # T21: still shown
            assert item.reasons == [EligibilityReason.WAIVER_MANDATORY_CAR]
            assert item.waiver_allowance is None
            _, dev = await employee(s, occupation="OCC-SOFTWARE-DEV")
            item = (await s.catalogue.list_eligible_options(dev, waiver)).data.items[0]
            assert item.eligibility is EligibilityStatus.ELIGIBLE
            assert item.waiver_allowance is not None and Decimal(item.waiver_allowance) == 1500
            _, unknown = await employee(s, occupation=None)
            out = await s.catalogue.check_eligibility(
                unknown, EligibilityRequest(expected_case_version=0, option_key="waiver")
            )
            assert out.data.status is EligibilityStatus.NEEDS_INFORMATION  # T23: no guess
            assert out.data.reasons == [EligibilityReason.OCCUPATION_UNKNOWN]
            assert out.status is EnvelopeStatus.NEEDS_INFORMATION

    run(scenario())


def test_check_eligibility_reasons_and_conflicts(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, gimel = await employee(s, group=EntitlementGroup.GIMEL)
            out = await s.catalogue.check_eligibility(
                gimel, EligibilityRequest(expected_case_version=0, option_key="vehicle:CAR-14")
            )
            assert out.data.status is EligibilityStatus.INELIGIBLE  # AC11
            assert out.data.reasons == [EligibilityReason.GROUP_HIGHER]
            # T20: the table says ב for a software developer, the employee reports ג
            _, conflict = await employee(
                s, group=EntitlementGroup.GIMEL, occupation="OCC-SOFTWARE-DEV"
            )
            out = await s.catalogue.check_eligibility(
                conflict,
                EligibilityRequest(expected_case_version=0, option_key="vehicle:CAR-10"),
            )
            assert out.data.status is EligibilityStatus.NEEDS_INFORMATION
            assert out.data.reasons == [EligibilityReason.OCCUPATION_GROUP_CONFLICT]
            out = await s.catalogue.check_eligibility(
                conflict,
                EligibilityRequest(expected_case_version=0, option_key="vehicle:CAR-05"),
            )
            assert out.data.status is EligibilityStatus.ELIGIBLE  # eligible under both
            with pytest.raises(NotFoundError) as caught:  # A7
                await s.catalogue.check_eligibility(
                    gimel,
                    EligibilityRequest(expected_case_version=0, option_key="vehicle:CAR-99"),
                )
            assert [i.code for i in caught.value.issues] == [IssueCode.VEHICLE_NOT_FOUND]
            with pytest.raises(VersionConflict):
                await s.catalogue.check_eligibility(
                    gimel, EligibilityRequest(expected_case_version=5, option_key="waiver")
                )

    run(scenario())


# --- resolve_vehicle / get_vehicle_tax_value --------------------------------------------


def resolve(purpose: str, **selector: Any) -> ResolveVehicleRequest:
    return ResolveVehicleRequest.model_validate({"purpose": purpose, **selector})


ATTO3 = {
    "taxYear": "2026",
    "registrationYear": "2025",
    "vehicleTypeCode": "1",
    "manufacturerCode": "01014",
    "modelCode": "00003",
}


def test_vehicle_resolution_never_selects_from_free_text(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s)
            out = await s.valuation.resolve_vehicle(
                ctx, resolve("current", query={"commercialName": "corolla"})
            )
            assert out.data.status is ResolveStatus.AMBIGUOUS  # T24: never auto-selected
            assert len(out.data.candidates) == 20 and out.data.truncated
            out = await s.valuation.resolve_vehicle(
                ctx, resolve("alternative", query={"commercialName": "corolla"})
            )
            assert [c.catalogue_id for c in out.data.candidates] == ["CAR-07", "CAR-09"]
            assert out.data.status is ResolveStatus.AMBIGUOUS  # COROLLA vs COROLLA CROSS
            assert out.issues[0].code is IssueCode.AMBIGUOUS_INPUT
            codes = {
                "commercialName": "ATTO 3",
                "manufacturerCode": "01014",
                "modelCode": "00003",
                "registrationYear": "2025",
            }
            out = await s.valuation.resolve_vehicle(ctx, resolve("current", query=codes))
            assert out.data.status is ResolveStatus.AMBIGUOUS  # T25: type code missing
            assert [c.tax_key.vehicle_type_code for c in out.data.candidates] == ["1"]
            assert out.issues[0].code is IssueCode.TAX_KEY_AMBIGUOUS  # TANG shares the codes
            assert {c.tax_key.manufacturer_code for c in out.data.candidates} == {"01014"}  # T27
            out = await s.valuation.resolve_vehicle(
                ctx, resolve("alternative", query={"commercialName": "atto"})
            )
            assert out.data.status is ResolveStatus.AMBIGUOUS  # A1: a unique one is confirmed
            assert [c.catalogue_id for c in out.data.candidates] == ["CAR-10"]
            out = await s.valuation.resolve_vehicle(
                ctx, resolve("alternative", catalogueId="CAR-10")
            )
            assert out.data.status is ResolveStatus.RESOLVED
            out = await s.valuation.resolve_vehicle(ctx, resolve("current", catalogueId="CAR-77"))
            assert out.data.status is ResolveStatus.NOT_FOUND
            assert out.issues[0].code is IssueCode.VEHICLE_NOT_FOUND
            out = await s.valuation.resolve_vehicle(
                ctx, resolve("current", query={"commercialName": "%"})
            )
            assert out.data.status is ResolveStatus.NOT_FOUND  # LIKE wildcards are literal

    run(scenario())


def test_value_of_use_by_complete_key_only(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s)
            out = await s.valuation.get_vehicle_tax_value(
                ctx, TaxValueRequest(catalogue_id="CAR-10")
            )
            assert Decimal(out.data.value_of_use) == Decimal("3130")  # T26 (type 1), not 6950
            assert out.data.tax_key.vehicle_type_code == "1"
            assert out.data.tax_key.manufacturer_code == "01014"  # T27: leading zeros kept
            assert out.data.tax_key.model_code == "00003"
            by_key = await s.valuation.get_vehicle_tax_value(
                ctx, TaxValueRequest.model_validate({"taxKey": ATTO3})
            )
            assert by_key.data == out.data and by_key.data.catalogue_id == "CAR-10"
            # T28/T29: a valid key outside the catalogue is a current car, not an alternative
            outside = await s.valuation.resolve_vehicle(
                ctx,
                resolve("current", query={"commercialName": "ATTO 3", "registrationYear": "2025"}),
            )
            foreign = next(c for c in outside.data.candidates if c.catalogue_id is None)
            key = foreign.tax_key.model_dump(mode="json", by_alias=True)
            value = await s.valuation.get_vehicle_tax_value(
                ctx, TaxValueRequest.model_validate({"taxKey": key})
            )
            assert value.data.value_of_use == foreign.value_of_use
            current = await s.valuation.resolve_vehicle(ctx, resolve("current", taxKey=key))
            assert current.data.status is ResolveStatus.RESOLVED
            alternative = await s.valuation.resolve_vehicle(ctx, resolve("alternative", taxKey=key))
            assert alternative.data.status is ResolveStatus.NOT_FOUND
            assert alternative.issues[0].code is IssueCode.UNSUPPORTED_INPUT
            missing = dict(ATTO3, modelCode="99999")
            with pytest.raises(NotFoundError) as caught:  # A7
                await s.valuation.get_vehicle_tax_value(
                    ctx, TaxValueRequest.model_validate({"taxKey": missing})
                )
            assert [i.code for i in caught.value.issues] == [IssueCode.VEHICLE_NOT_FOUND]

    run(scenario())


# --- resolve_occupation (G2) ------------------------------------------------------------


def test_occupation_resolution_is_exact_or_a_choice(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s)

            async def go(query: str | None) -> Any:
                return await s.occupations.resolve_occupation(
                    ctx, ResolveOccupationRequest(query=query)
                )

            for query in (
                "OCC-SOFTWARE-DEV",
                " occ-software-dev ",
                "מפתח/ת תוכנה",
                "מפתחת  תוכנה",
                "מפתח תוכנה",
            ):
                out = await go(query)
                assert out.data.status is ResolveStatus.RESOLVED, query
                assert [c.occupation_id for c in out.data.candidates] == ["OCC-SOFTWARE-DEV"]
            out = await go("מנהל")
            assert out.data.status is ResolveStatus.AMBIGUOUS
            assert len(out.data.candidates) == 6 and out.status is EnvelopeStatus.NEEDS_INFORMATION
            out = await go("מפתח")
            assert out.data.status is ResolveStatus.AMBIGUOUS  # a single partial match
            assert (await go(None)).data.status is ResolveStatus.AMBIGUOUS
            assert len((await go(None)).data.candidates) == 14
            out = await go("מתכנת")  # no synonyms
            assert out.data.status is ResolveStatus.NOT_FOUND and out.data.candidates == []
            assert (await go("OCC-NOT-REAL")).data.status is ResolveStatus.NOT_FOUND

    run(scenario())


# --- get_representative_request (G1) ----------------------------------------------------


def test_representative_request_is_visible_only_in_its_case(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who, ctx = await employee(s)
            stored = request(ctx.case_id, who.subject_id)
            async with s.p.uow() as uow:
                await uow.representative_requests.add_or_get_open(stored)
                await uow.commit()
            lookup = RepresentativeRequestLookup(request_id=stored.request_id)
            out = await s.requests.get_representative_request(ctx, lookup)
            assert out.data.status.value == "open" and out.data.answer is None
            assert out.data.field_code.value == "alternative.participation"
            other_ctx, _ = await new_case(s, who)  # same owner, other case
            with pytest.raises(NotFoundError):
                await s.requests.get_representative_request(other_ctx, lookup)
            with pytest.raises(NotFoundError):
                await s.requests.get_representative_request(
                    ctx, RepresentativeRequestLookup(request_id=uuid4())
                )

    run(scenario())


# --- confirm_profile_fields (G3) and fieldsRequiringConfirmation (G4) -------------------


def confirm(
    case_version: int, profile_version: int, *fields: str, operation_id: Any = None
) -> ConfirmProfileFieldsRequest:
    return ConfirmProfileFieldsRequest.model_validate(
        {
            "expectedCaseVersion": case_version,
            "expectedProfileVersion": profile_version,
            "operationId": str(operation_id or uuid4()),
            "sourceMessageId": str(uuid4()),
            "fields": list(fields),
        }
    )


def test_confirmation_updates_metadata_only_and_is_idempotent(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who, ctx = await employee(s)
            context = await s.cases.get_context(ctx)
            required = context.fields_requiring_confirmation
            assert FieldPath.BASE_GROSS in required  # G4: seeded before the case
            assert FieldPath.BONUS_GROSS not in required  # missing facts are asked, not confirmed
            before = await s.profiles.get_profile(who)
            op = uuid4()
            body = confirm(0, 1, "payroll.baseGross", "payroll.bonusGross", operation_id=op)
            result = await s.cases.confirm_profile_fields(ctx, body)
            assert result.confirmed_fields == [FieldPath.BASE_GROSS]
            assert [(i.code, i.field) for i in result.not_confirmable] == [
                (IssueCode.MISSING_INPUT, FieldPath.BONUS_GROSS)
            ]
            assert (result.new_case_version, result.new_profile_version) == (1, 2)
            after = await s.profiles.get_profile(who)
            assert after.base_gross.value == before.base_gross.value  # value unchanged
            assert after.base_gross.updated_at == before.base_gross.updated_at
            assert after.base_gross.last_confirmed_at is not None
            assert after.base_gross.revision == before.base_gross.revision + 1
            assert await s.cases.confirm_profile_fields(ctx, body) == result  # replay
            context = await s.cases.get_context(ctx)
            assert FieldPath.BASE_GROSS not in context.fields_requiring_confirmation
            assert FieldPath.ENTITLEMENT_GROUP in context.fields_requiring_confirmation
            with pytest.raises(VersionConflict):
                await s.cases.confirm_profile_fields(ctx, confirm(1, 1, "payroll.baseGross"))
            with pytest.raises(VersionConflict):
                await s.cases.confirm_profile_fields(ctx, confirm(0, 2, "payroll.baseGross"))
            await s.cases.update_inputs(
                ctx,
                update(
                    "actual_update", 1, [set_("employee.entitlementGroup", "ג")], profile_version=2
                ),
            )
            context = await s.cases.get_context(ctx)
            assert FieldPath.ENTITLEMENT_GROUP not in context.fields_requiring_confirmation
            new_ctx, _ = await new_case(s, who)  # a later conversation asks again when relevant
            later = await s.cases.get_context(new_ctx)
            assert FieldPath.BASE_GROSS in later.fields_requiring_confirmation

    run(scenario())


def test_concurrent_confirmations_do_not_both_win(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s)
            outcomes = await asyncio.gather(
                s.cases.confirm_profile_fields(ctx, confirm(0, 1, "payroll.baseGross")),
                s.cases.confirm_profile_fields(
                    ctx, confirm(0, 1, "payroll.creditPoints", "payroll.baseGross")
                ),
                return_exceptions=True,
            )
            wins = [o for o in outcomes if not isinstance(o, BaseException)]
            assert len(wins) == 1
            assert all(isinstance(o, VersionConflict) for o in outcomes if o not in wins)

    run(scenario())


# --- authorization ----------------------------------------------------------------------


def test_every_4a_operation_is_authorized_by_the_tool_context(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who, ctx = await employee(s)
            _, stranger_ctx = await employee(s)
            calls: list[Callable[[ToolContext], Awaitable[object]]] = [
                lambda c: s.catalogue.list_eligible_options(c, listing()),
                lambda c: s.catalogue.check_eligibility(
                    c, EligibilityRequest(expected_case_version=0, option_key="waiver")
                ),
                lambda c: s.valuation.resolve_vehicle(c, resolve("current", catalogueId="CAR-10")),
                lambda c: s.valuation.get_vehicle_tax_value(
                    c, TaxValueRequest(catalogue_id="CAR-10")
                ),
                lambda c: s.occupations.resolve_occupation(c, ResolveOccupationRequest()),
                lambda c: s.requests.get_representative_request(
                    c, RepresentativeRequestLookup(request_id=uuid4())
                ),
                lambda c: s.cases.confirm_profile_fields(c, confirm(0, 1, "payroll.baseGross")),
            ]
            probes = [
                ctx.model_copy(update={"case_id": stranger_ctx.case_id}),  # foreign case
                ctx.model_copy(update={"case_id": uuid4()}),  # invented case
                ctx.model_copy(update={"conversation_id": uuid4()}),  # wrong conversation
            ]
            rep_id = await new_user(s.p, Role.REPRESENTATIVE)
            representative = ctx.model_copy(update={"actor": actor(rep_id, Role.REPRESENTATIVE)})
            for call in calls:
                for probe in probes:
                    with pytest.raises(NotFoundError) as caught:
                        await call(probe)
                    assert caught.value.issues == () and caught.value.safe_details == {}
                with pytest.raises(ForbiddenError):
                    await call(representative)
            sql(business_db, "UPDATE auth.users SET active = false WHERE id = %s", who.subject_id)
            for call in calls:
                with pytest.raises(UnauthenticatedError) as error:
                    await call(ctx)
                assert error.value.code is ErrorCode.UNAUTHENTICATED

    run(scenario())


def test_fact_paths_match_the_contract_field_paths() -> None:
    """G3/G4 map contract field paths to profile facts one to one."""
    assert [p.value for p in FactPath] == [p.value for p in FieldPath]
