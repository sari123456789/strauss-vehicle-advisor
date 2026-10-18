"""Reference-data persistence against real PostgreSQL (separate `_test` database).

Covers: migration round trip, UTF-8, complete import, idempotent re-import, complete-key
lookups, byte-exact Hebrew/whitespace round trip, transaction rollback, version activation,
policy versioning and DB-enforced immutability.
"""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection

from app.bootstrap import ImportComponents
from app.business.application.imports.import_service import ImportStatus
from app.business.application.ports.reference_data import (
    ReferenceDataConflictError,
    ReferenceSourceSet,
)
from app.business.domain.vehicles import VehicleTaxKey
from app.business.infrastructure.database.engine import create_engine
from app.business.infrastructure.database.models import Base
from app.contracts.tool_context import ActorRef, Origin, Role
from tests.integration.business.database.db_helpers import (
    CASE_TABLES,
    TABLES,
    alembic_config,
    libpq_url,
    run,
    sql,
    table_counts,
)
from tests.reference_sources import real_sources

OPERATOR = ActorRef(subject_id=uuid4(), roles=frozenset({Role.IMPORT_OPERATOR}), origin=Origin.CLI)
ATTO3 = VehicleTaxKey("2026", "2025", "1", "01014", "00003")
TANG = VehicleTaxKey("2026", "2025", "2", "01014", "00003")
EXPECTED_COUNTS = {
    "rules_snapshots": 1,
    "catalogue_versions": 1,
    "vehicles": 20,
    "occupation_policies": 14,
    "tax_dataset_versions": 1,
    "value_of_use": 77_988,
    "reference_releases": 1,
}


def _publish(components: ImportComponents, sources: ReferenceSourceSet) -> object:
    return run(components.repository.publish(sources, {"test": True}, OPERATOR.subject_id))


def _with_policy(sources: ReferenceSourceSet, version: str, sha256: str) -> ReferenceSourceSet:
    """A copy of the real set as if the derived policy file had changed."""
    rules = sources.rules
    config = replace(rules.config, derived=replace(rules.config.derived, version=version))
    return replace(
        sources,
        rules=replace(rules, config=config, policy=replace(rules.policy, sha256=sha256)),
        catalogue=replace(sources.catalogue, policy_sha256=sha256),
    )


def test_migrations_round_trip_on_utf8_database(clean_database: str) -> None:
    assert sql(clean_database, "SHOW server_encoding") == [("UTF8",)]
    tables = {
        r[0]
        for r in sql(
            clean_database, "SELECT tablename FROM pg_tables WHERE schemaname = 'business'"
        )
    }
    assert tables == set(TABLES) | set(CASE_TABLES)

    def diff(connection: Connection) -> list[object]:
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "compare_type": True,
                "include_name": lambda n, t, _p: t != "schema" or n in ("business", "auth"),
            },
        )
        return list(compare_metadata(context, Base.metadata))

    async def model_drift() -> list[object]:
        engine = create_engine(clean_database)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(diff)
        finally:
            await engine.dispose()

    assert run(model_drift()) == []

    command.downgrade(alembic_config(clean_database), "base")
    assert sql(clean_database, "SELECT 1 FROM pg_namespace WHERE nspname = 'business'") == []
    command.upgrade(alembic_config(clean_database), "head")
    assert table_counts(clean_database) == dict.fromkeys(TABLES, 0)


def test_complete_import_and_idempotent_reimport(
    components: ImportComponents, clean_database: str
) -> None:
    first = run(components.service.run(OPERATOR))
    assert first.status is ImportStatus.PUBLISHED and first.release is not None
    assert first.release.release_seq >= 1
    assert first.release.derived_policy_version == "derived-demo-v2"
    assert table_counts(clean_database) == EXPECTED_COUNTS

    second = run(components.service.run(OPERATOR))
    assert second.status is ImportStatus.UNCHANGED
    assert second.release == first.release
    assert table_counts(clean_database) == EXPECTED_COUNTS

    latest = run(components.repository.latest_release())
    assert latest == first.release


def test_complete_key_lookups_and_catalogue_reads(components: ImportComponents) -> None:
    run(components.service.run(OPERATOR))
    release = run(components.repository.latest_release())
    assert release is not None
    repo = components.repository

    atto3 = run(repo.get_value_of_use(release.tax_dataset_version_id, ATTO3))
    tang = run(repo.get_value_of_use(release.tax_dataset_version_id, TANG))
    assert atto3 is not None and atto3.value_of_use == Decimal(3130)
    assert tang is not None and tang.value_of_use == Decimal(6950)
    missing = replace(ATTO3, model_code="99999")
    assert missing not in {t.record.key for t in real_sources().tax_dataset.records}
    assert run(repo.get_value_of_use(release.tax_dataset_version_id, missing)) is None

    vehicle = run(repo.get_vehicle(release.catalogue_version_id, "CAR-10"))
    real = next(
        t.vehicle for t in real_sources().catalogue.vehicles if t.vehicle.catalogue_id == "CAR-10"
    )
    assert vehicle == real
    assert vehicle.tax_key.manufacturer_code == "01014" and vehicle.tax_key.model_code == "00003"
    assert len(run(repo.list_vehicles(release.catalogue_version_id))) == 20
    developer = run(repo.get_occupation(release.catalogue_version_id, "OCC-SOFTWARE-DEV"))
    assert developer is not None and developer.name_he == "מפתח/ת תוכנה"
    assert developer.waiver_allowance == Decimal(1500)


def test_hebrew_and_original_whitespace_round_trip_exactly(
    components: ImportComponents, clean_database: str
) -> None:
    run(components.service.run(OPERATOR))
    sources = real_sources()
    original = next(t for t in sources.tax_dataset.records if t.record.key == ATTO3)
    stored = sql(
        clean_database,
        "SELECT source_values, source_row_number, drive_code FROM business.value_of_use "
        "WHERE vehicle_type_code = %s AND manufacturer_code = %s AND model_code = %s "
        "AND registration_year = %s",
        "1",
        "01014",
        "00003",
        "2025",
    )
    assert stored == [(list(original.source.values), original.source.row_number, "3")]
    assert stored[0][0][4] == "בי ווי די      "

    header = sql(clean_database, "SELECT original_header FROM business.tax_dataset_versions")
    assert header == [(list(sources.tax_dataset.original_header),)]
    names = sql(
        clean_database,
        "SELECT manufacturer_name, source_values FROM business.vehicles WHERE catalogue_id = %s",
        "CAR-08",
    )
    assert names[0][0] == "יונדאי צ'כיה"
    car08 = next(t for t in sources.catalogue.vehicles if t.vehicle.catalogue_id == "CAR-08")
    assert names[0][1] == list(car08.source.values)


def test_failed_publication_rolls_back_and_keeps_the_previous_release(
    components: ImportComponents, clean_database: str
) -> None:
    sources = real_sources()
    run(components.service.run(OPERATOR))
    before = run(components.repository.latest_release())
    counts = table_counts(clean_database)

    dataset = sources.tax_dataset
    broken = replace(
        _with_policy(sources, "derived-demo-rollback-test", "a" * 64),
        tax_dataset=replace(
            dataset,
            csv=replace(dataset.csv, sha256="b" * 64),
            records=(*dataset.records, dataset.records[0]),
        ),
    )
    with pytest.raises(Exception, match="pk_value_of_use|duplicate key"):
        _publish(components, broken)

    assert table_counts(clean_database) == counts
    assert run(components.repository.latest_release()) == before


def test_new_policy_version_activates_a_new_release_and_keeps_history(
    components: ImportComponents, clean_database: str
) -> None:
    sources = real_sources()
    first = run(components.service.run(OPERATOR)).release
    assert first is not None

    changed = _with_policy(sources, "derived-demo-v3-test", "c" * 64)
    result = run(components.repository.publish(changed, {"test": True}, OPERATOR.subject_id))
    second = result.release
    assert result.created and second.release_seq > first.release_seq
    assert run(components.repository.latest_release()) == second

    assert second.rules_snapshot_id != first.rules_snapshot_id
    assert second.catalogue_version_id != first.catalogue_version_id
    assert second.tax_dataset_version_id == first.tax_dataset_version_id  # reused, not copied
    assert run(components.repository.get_release(first.release_id)) == first
    assert len(run(components.repository.list_vehicles(first.catalogue_version_id))) == 20
    counts = table_counts(clean_database)
    assert counts["value_of_use"] == 77_988 and counts["reference_releases"] == 2
    assert counts["rules_snapshots"] == 2 and counts["vehicles"] == 40


def test_policy_change_without_version_bump_is_rejected(
    components: ImportComponents, clean_database: str
) -> None:
    run(components.service.run(OPERATOR))
    counts = table_counts(clean_database)
    same_version = _with_policy(real_sources(), "derived-demo-v2", "d" * 64)
    with pytest.raises(ReferenceDataConflictError, match="bump the version"):
        _publish(components, same_version)
    assert table_counts(clean_database) == counts


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE business.vehicles SET list_price = 1 WHERE catalogue_id = 'CAR-10'",
        "UPDATE business.value_of_use SET value_of_use = 6950 WHERE model_code = '00003'",
        "DELETE FROM business.reference_releases",
        "DELETE FROM business.rules_snapshots",
        "TRUNCATE business.value_of_use CASCADE",
    ],
)
def test_published_reference_data_cannot_be_modified(
    components: ImportComponents, clean_database: str, statement: str
) -> None:
    run(components.service.run(OPERATOR))
    with psycopg.connect(libpq_url(clean_database), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RestrictViolation, match="immutable"):
            conn.execute(statement)
    assert table_counts(clean_database) == EXPECTED_COUNTS
