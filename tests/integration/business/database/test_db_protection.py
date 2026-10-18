"""Database-level protection of Step 3A tables (real PostgreSQL): triggers, composite foreign
keys and JSONB validation functions. Needs the reference release (data/source/).

Privileges: the local test user is a superuser/owner and could disable triggers; these checks
protect against application errors, not against privileged users (role separation is D8).
"""

import json
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from app.business.application.ports.repositories import OperationKey, ScopeKind
from app.business.infrastructure.database.engine import JsonFloatError
from app.business.infrastructure.database.repositories.codec import encode_facts
from tests.integration.business.database.case_helpers import (
    alternative,
    latest_release,
    new_owner_with_case,
    persistence,
    profile,
    request,
    result_set,
    run_record,
)
from tests.integration.business.database.db_helpers import libpq_url, run

pytestmark = pytest.mark.sources


def _setup(url: str) -> dict[str, Any]:
    async def scenario() -> dict[str, Any]:
        async with persistence(url) as p:
            owner, case = await new_owner_with_case(p)
            _, other_case = await new_owner_with_case(p)
            release = await latest_release(p)
            alt = alternative(case.case_id)
            other_alt = alternative(other_case.case_id)
            rs = result_set(case.case_id, release)
            async with p.uow() as uow:
                await uow.alternatives.add(alt)
                await uow.alternatives.add(other_alt)
                await uow.option_result_sets.add(rs)
                await uow.comparison_runs.add(
                    run_record(case.case_id, release, owner, option_result_set_id=rs.result_set_id)
                )
                await uow.representative_requests.add_or_get_open(request(case.case_id, owner))
                key = OperationKey(owner, ScopeKind.CASE, case.case_id, "update_inputs", uuid4())
                await uow.operations.claim(key, "0" * 64)
                await uow.commit()
            return {
                "owner": owner,
                "case": case,
                "other_case": other_case,
                "release": release,
                "alt": alt,
                "other_alt": other_alt,
                "result_set": rs,
            }

    return run(scenario())


@pytest.fixture(scope="module")
def world(business_db: str) -> dict[str, Any]:
    return _setup(business_db)


def _execute(url: str, statement: str, *params: object) -> None:
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        conn.execute(statement, params or None)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE business.comparison_runs SET include_current = NOT include_current",
        "DELETE FROM business.comparison_runs",
        "TRUNCATE business.comparison_runs",
        "UPDATE business.option_result_sets SET scope_complete = true",
        "DELETE FROM business.option_result_sets",
    ],
)
def test_calculation_snapshots_are_immutable(
    business_db: str, world: object, statement: str
) -> None:
    with pytest.raises(psycopg.errors.RestrictViolation, match="immutable"):
        _execute(business_db, statement)


@pytest.mark.parametrize(
    "table",
    [
        "employee_profiles",
        "cases",
        "alternatives",
        "operations",
        "representative_requests",
    ],
)
def test_business_records_are_never_deleted(business_db: str, world: object, table: str) -> None:
    for statement in (f"DELETE FROM business.{table}", f"TRUNCATE business.{table} CASCADE"):
        with pytest.raises(psycopg.errors.RestrictViolation, match="never deleted"):
            _execute(business_db, statement)


def test_users_cannot_be_removed_while_referenced(business_db: str, world: dict[str, Any]) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _execute(business_db, "DELETE FROM auth.users WHERE id = %s", world["owner"])
    with pytest.raises(psycopg.errors.RestrictViolation):
        _execute(business_db, "TRUNCATE auth.users CASCADE")


def test_updates_are_allowed_on_mutable_records(business_db: str, world: dict[str, Any]) -> None:
    _execute(
        business_db,
        "UPDATE business.cases SET lifecycle_status = 'active' WHERE id = %s",
        world["case"].case_id,
    )


def _insert_run(url: str, world: dict[str, Any], **changes: Any) -> None:
    release = world["release"]
    record = run_record(world["case"].case_id, release, world["owner"])
    values = {
        "id": record.run_id,
        "case_id": record.case_id,
        "kind": "regular",
        "calculation_month": "2026-10",
        "case_version_used": 0,
        "profile_version_used": 1,
        "reference_release_id": release.release_id,
        "rules_snapshot_id": release.rules_snapshot_id,
        "catalogue_version_id": release.catalogue_version_id,
        "tax_dataset_version_id": release.tax_dataset_version_id,
        "option_result_set_id": None,
        "selection": "{}",
        "include_current": False,
        "input_snapshot": "{}",
        "result": "{}",
        "operation_id": uuid4(),
        "created_by": world["owner"],
    }
    values.update(changes)
    columns = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    _execute(
        url,
        f"INSERT INTO business.comparison_runs ({columns}) VALUES ({placeholders})",
        *values.values(),
    )


def test_run_source_versions_must_belong_to_one_release(
    business_db: str, world: dict[str, Any]
) -> None:
    _insert_run(business_db, world)  # consistent lineage is accepted
    release = world["release"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert_run(business_db, world, rules_snapshot_id=release.catalogue_version_id)


def test_run_result_set_must_belong_to_the_same_case_and_release(
    business_db: str, world: dict[str, Any]
) -> None:
    rs = world["result_set"]
    _insert_run(business_db, world, option_result_set_id=rs.result_set_id)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert_run(
            business_db,
            world,
            case_id=world["other_case"].case_id,
            option_result_set_id=rs.result_set_id,
        )
    second_release = uuid4()
    release = world["release"]
    _execute(
        business_db,
        "INSERT INTO business.reference_releases (id, rules_snapshot_id, catalogue_version_id, "
        "tax_dataset_version_id, challenge_pdf_sha256, challenge_pdf_page_count, "
        "consistency_report, published_by) SELECT %s, rules_snapshot_id, catalogue_version_id, "
        "tax_dataset_version_id, challenge_pdf_sha256, challenge_pdf_page_count, "
        "consistency_report, published_by FROM business.reference_releases WHERE id = %s",
        second_release,
        release.release_id,
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert_run(
            business_db,
            world,
            reference_release_id=second_release,
            option_result_set_id=rs.result_set_id,
        )


def test_request_alternative_must_belong_to_the_same_case(
    business_db: str, world: dict[str, Any]
) -> None:
    async def scenario() -> None:
        async with persistence(business_db) as p, p.uow() as uow:
            foreign = request(world["case"].case_id, world["owner"], alt=world["other_alt"])
            await uow.representative_requests.add_or_get_open(foreign)

    with pytest.raises(Exception, match="fk_representative_requests_alternative"):
        run(scenario())


def _profile_document(world: dict[str, Any]) -> dict[str, Any]:
    return encode_facts(profile(world["owner"]))


def _mutate(document: dict[str, Any], path: str, **fields: Any) -> dict[str, Any]:
    changed = json.loads(json.dumps(document))
    changed[path].update(fields)
    return changed


INVALID_DOCUMENTS = [
    lambda d: {k: v for k, v in d.items() if k != "payroll.bonusGross"},
    lambda d: {**d, "extra": 1},
    lambda d: {**d, "schemaVersion": 2},
    lambda d: _mutate(d, "payroll.baseGross", value=18500.5),
    lambda d: _mutate(d, "payroll.baseGross", value="-1"),
    lambda d: _mutate(d, "payroll.bonusGross", value="1"),
    lambda d: _mutate(d, "payroll.baseGross", value=None),
    lambda d: _mutate(d, "payroll.pensionEmployeeRate", value="1.5"),
    lambda d: _mutate(d, "employee.entitlementGroup", value="ה"),
    lambda d: _mutate(d, "employee.qualifyingChildrenCount", status="usable", value="2"),
    lambda d: _mutate(d, "currentArrangement.vehicle", value={"catalogueId": "CAR-9X"}),
    lambda d: _mutate(d, "payroll.baseGross", status="known"),
    lambda d: _mutate(d, "payroll.baseGross", revision=-1),
    lambda d: _mutate(d, "payroll.baseGross", sourceMessageId="not-a-uuid"),
]


@pytest.mark.parametrize("change", INVALID_DOCUMENTS)
def test_database_rejects_invalid_facts_documents(
    business_db: str, world: dict[str, Any], change: Any
) -> None:
    document = change(_profile_document(world))
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_employee_profiles_fields"):
        _execute(
            business_db,
            "UPDATE business.employee_profiles SET fields = %s::jsonb WHERE employee_id = %s",
            json.dumps(document, ensure_ascii=False),
            world["owner"],
        )


def test_database_accepts_the_codec_output(business_db: str, world: dict[str, Any]) -> None:
    _execute(
        business_db,
        "UPDATE business.employee_profiles SET fields = %s::jsonb WHERE employee_id = %s",
        json.dumps(_profile_document(world), ensure_ascii=False),
        world["owner"],
    )


@pytest.mark.parametrize(
    "overrides",
    [
        [{"path": "payroll.baseGross", "value": "1", "sourceMessageId": None, "createdAt": "x"}]
        * 2,
        [{"path": "payroll.baseGross", "value": None, "sourceMessageId": None, "createdAt": "x"}],
        [{"path": "payroll.salary", "value": "1", "sourceMessageId": None, "createdAt": "x"}],
        [{"path": "payroll.baseGross", "value": 1.5, "sourceMessageId": None, "createdAt": "x"}],
        {"path": "payroll.baseGross"},
    ],
)
def test_database_rejects_invalid_hypothetical_overrides(
    business_db: str, world: dict[str, Any], overrides: object
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_cases_hypothetical_overrides"):
        _execute(
            business_db,
            "UPDATE business.cases SET hypothetical_overrides = %s::jsonb WHERE id = %s",
            json.dumps(overrides),
            world["case"].case_id,
        )


def test_json_floats_are_never_read_back(business_db: str, world: dict[str, Any]) -> None:
    run_id = uuid4()
    _insert_run(business_db, world, id=run_id, result='{"net": 1.5}')

    async def scenario() -> None:
        async with persistence(business_db) as p, p.uow() as uow:
            await uow.comparison_runs.get(world["case"].case_id, run_id)

    with pytest.raises(JsonFloatError):
        run(scenario())
