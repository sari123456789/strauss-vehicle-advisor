"""Migration 0002 on real PostgreSQL: schema contents, drift, and a downgrade/upgrade round
trip that leaves the published 0001 reference data byte-identical.
"""

import pytest
from alembic import command

from app.bootstrap import ImportComponents
from tests.integration.business.database.conftest import IMPORT_OPERATOR
from tests.integration.business.database.db_helpers import (
    CASE_TABLES,
    TABLES,
    alembic_config,
    run,
    sql,
)

pytestmark = pytest.mark.sources

FUNCTIONS = {
    "json_key_count",
    "json_text_matches",
    "json_decimal_string",
    "json_null_or_text",
    "is_valid_tax_key",
    "is_valid_fact_value",
    "is_valid_field_envelope",
    "is_valid_facts_document",
    "is_valid_overrides",
    "reject_snapshot_mutation",
    "reject_delete",
    "reject_reference_data_mutation",
}


def _reference_checksums(url: str) -> dict[str, tuple[int, str]]:
    result = {}
    for table in TABLES:
        rows = sql(
            url,
            f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "
            f"FROM business.{table} t",
        )
        result[table] = (rows[0][0], rows[0][1])
    return result


def _business_objects(url: str) -> tuple[set[str], set[str], set[str]]:
    tables = {
        r[0] for r in sql(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'business'")
    }
    auth = {r[0] for r in sql(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'auth'")}
    functions = {
        r[0]
        for r in sql(
            url,
            "SELECT proname FROM pg_proc WHERE pronamespace = 'business'::regnamespace",
        )
    }
    return tables, auth, functions


def test_schema_contents_and_downgrade_keeps_reference_data_identical(
    components: ImportComponents, clean_database: str
) -> None:
    run(components.service.run(IMPORT_OPERATOR))
    tables, auth, functions = _business_objects(clean_database)
    assert tables == set(TABLES) | set(CASE_TABLES)
    assert auth == {"users", "credentials", "refresh_sessions"}  # 0003 (Step 4D)
    assert functions == FUNCTIONS
    before = _reference_checksums(clean_database)
    assert before["value_of_use"][0] == 77_988

    config = alembic_config(clean_database)
    command.downgrade(config, "0001_reference_data")
    tables, auth, functions = _business_objects(clean_database)
    assert tables == set(TABLES) and auth == set()
    assert functions == {"reject_reference_data_mutation"}
    assert sql(clean_database, "SELECT 1 FROM pg_namespace WHERE nspname = 'auth'") == []
    lineage = "SELECT 1 FROM pg_constraint WHERE conname = 'uq_reference_releases_lineage'"
    assert sql(clean_database, lineage) == []
    assert _reference_checksums(clean_database) == before

    command.upgrade(config, "head")
    assert _reference_checksums(clean_database) == before
    assert sql(clean_database, lineage) == [(1,)]
    assert sql(clean_database, "SELECT version_num FROM public.alembic_version") == [
        ("0003_auth_credentials",)
    ]


def test_check_constraint_names_are_readable(clean_database: str) -> None:
    names = {
        r[0]
        for r in sql(
            clean_database,
            "SELECT conname FROM pg_constraint WHERE contype = 'c' "
            "AND conrelid = 'business.employee_profiles'::regclass",
        )
    }
    assert names == {
        "ck_employee_profiles_version",
        "ck_employee_profiles_fields",
        "ck_employee_profiles_origin",
    }


def test_auth_check_constraint_names_have_one_prefix(clean_database: str) -> None:
    names = {
        r[0]
        for r in sql(
            clean_database,
            "SELECT conname FROM pg_constraint WHERE contype = 'c' "
            "AND connamespace = 'auth'::regnamespace "
            "AND (conname LIKE '%credentials%' OR conname LIKE '%refresh_sessions%')",
        )
    }
    assert names == {
        "ck_credentials_password_hash",
        "ck_refresh_sessions_token_hash",
        "ck_refresh_sessions_expiry",
    }
