"""PostgreSQL implementation of `ReferenceDataRepository`.

`publish` is one transaction serialized by an advisory lock: identical existing versions are
reused, new ones are inserted with all their records, and a release row is appended. Any
failure rolls everything back, so the previous release stays the latest. Nothing is updated
or deleted (the schema rejects it).
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.application.ports.reference_data import (
    CatalogueSource,
    ImportReport,
    JsonScalar,
    PublicationResult,
    PublishedRelease,
    ReferenceDataConflictError,
    ReferenceSourceSet,
    ReleaseIdentity,
    RulesSource,
    TaxDatasetSource,
    TaxRecordDetails,
)
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.rules import EntitlementGroup, RulesConfig
from app.business.domain.vehicles import (
    CatalogueVehicle,
    Propulsion,
    TaxRecord,
    Track,
    VehicleTaxKey,
)
from app.business.infrastructure.database.models.reference_data import (
    CatalogueVersionRow,
    OccupationPolicyRow,
    ReferenceReleaseRow,
    RulesSnapshotRow,
    TaxDatasetVersionRow,
    ValueOfUseRow,
    VehicleRow,
)
from app.business.infrastructure.imports.rules_json_loader import JsonDocument
from app.business.infrastructure.imports.rules_loader import build_rules_config
from app.business.infrastructure.imports.tax_csv_importer import EXPECTED_HEADER

PUBLICATION_LOCK_KEY = 0x5354524155535301
"""Advisory lock serializing reference-data publications ("STRAUSS" + 01)."""
VALUE_OF_USE_COLUMNS = (
    "tax_dataset_version_id",
    "tax_year",
    "registration_year",
    "vehicle_type_code",
    "manufacturer_code",
    "model_code",
    "value_of_use",
    "effective_date",
    "drive_code",
    "source_row_number",
    "source_values",
)


def _report(report: ImportReport) -> dict[str, Any]:
    return {
        "kind": report.kind,
        "sha256": report.sha256,
        "recordCount": report.record_count,
        "warnings": list(report.warnings),
        "details": {
            k: list(v) if isinstance(v, tuple) else v for k, v in (report.details or {}).items()
        },
    }


_COMMERCIAL_NAME = EXPECTED_HEADER.index("כינוי מסחרי")
_MANUFACTURER_NAME = EXPECTED_HEADER.index("תוצר")
_MODEL_DESCRIPTION = EXPECTED_HEADER.index("תאור דגם")


def _occupation(row: OccupationPolicyRow) -> OccupationPolicy:
    return OccupationPolicy(
        occupation_id=row.occupation_id,
        name_he=row.name_he,
        group=EntitlementGroup(row.entitlement_group),
        mandatory_car=row.mandatory_car,
        waiver_allowed=row.waiver_allowed,
        waiver_allowance=row.waiver_allowance,
    )


def _tax_details(row: ValueOfUseRow) -> TaxRecordDetails:
    """Descriptive columns from the original CSV values (trimmed for display/matching)."""
    values = row.source_values
    key = VehicleTaxKey(
        row.tax_year,
        row.registration_year,
        row.vehicle_type_code,
        row.manufacturer_code,
        row.model_code,
    )
    return TaxRecordDetails(
        record=TaxRecord(key=key, value_of_use=row.value_of_use, effective_date=row.effective_date),
        manufacturer_name=str(values[_MANUFACTURER_NAME]).strip(),
        commercial_name=str(values[_COMMERCIAL_NAME]).strip(),
        model_description=str(values[_MODEL_DESCRIPTION]).strip(),
    )


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _vehicle(row: VehicleRow) -> CatalogueVehicle:
    return CatalogueVehicle(
        catalogue_id=row.catalogue_id,
        track=Track(row.track),
        group=None if row.entitlement_group is None else EntitlementGroup(row.entitlement_group),
        manufacturer_name=row.manufacturer_name,
        commercial_name=row.commercial_name,
        model_description=row.model_description,
        tax_key=VehicleTaxKey(
            row.tax_year,
            row.registration_year,
            row.vehicle_type_code,
            row.manufacturer_code,
            row.model_code,
        ),
        propulsion=Propulsion(row.propulsion),
        list_price=row.list_price,
        catalogue_value_of_use=row.value_of_use,
        monthly_company_cost=row.monthly_company_cost,
        default_participation=row.default_participation,
    )


class SqlReferenceDataRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # --- reads -------------------------------------------------------------------------------

    async def latest_release(self) -> PublishedRelease | None:
        async with self._sessions() as session:
            return await self._release(session, None)

    async def get_release(self, release_id: UUID) -> PublishedRelease | None:
        async with self._sessions() as session:
            return await self._release(session, release_id)

    async def get_rules(self, rules_snapshot_id: UUID) -> RulesConfig | None:
        async with self._sessions() as session:
            row = await session.get(RulesSnapshotRow, rules_snapshot_id)
        if row is None:
            return None
        return build_rules_config(
            JsonDocument(
                Path("rules_snapshots.rules_content"), row.rules_sha256, row.rules_content
            ),
            JsonDocument(
                Path("rules_snapshots.policy_content"), row.policy_sha256, row.policy_content
            ),
        )

    async def get_vehicle(
        self, catalogue_version_id: UUID, catalogue_id: str
    ) -> CatalogueVehicle | None:
        async with self._sessions() as session:
            row = await session.get(VehicleRow, (catalogue_version_id, catalogue_id))
            return None if row is None else _vehicle(row)

    async def list_vehicles(self, catalogue_version_id: UUID) -> tuple[CatalogueVehicle, ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(VehicleRow)
                .where(VehicleRow.catalogue_version_id == catalogue_version_id)
                .order_by(VehicleRow.catalogue_id)
            )
            return tuple(_vehicle(r) for r in rows)

    async def get_occupation(
        self, catalogue_version_id: UUID, occupation_id: str
    ) -> OccupationPolicy | None:
        async with self._sessions() as session:
            row = await session.get(OccupationPolicyRow, (catalogue_version_id, occupation_id))
            return None if row is None else _occupation(row)

    async def get_value_of_use(
        self, tax_dataset_version_id: UUID, key: VehicleTaxKey
    ) -> TaxRecord | None:
        async with self._sessions() as session:
            row = await session.get(
                ValueOfUseRow,
                (
                    tax_dataset_version_id,
                    key.tax_year,
                    key.registration_year,
                    key.vehicle_type_code,
                    key.manufacturer_code,
                    key.model_code,
                ),
            )
            if row is None:
                return None
            return TaxRecord(
                key=key, value_of_use=row.value_of_use, effective_date=row.effective_date
            )

    async def list_occupations(self, catalogue_version_id: UUID) -> tuple[OccupationPolicy, ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(OccupationPolicyRow)
                .where(OccupationPolicyRow.catalogue_version_id == catalogue_version_id)
                .order_by(OccupationPolicyRow.occupation_id)
            )
            return tuple(_occupation(r) for r in rows)

    async def get_tax_record_details(
        self, tax_dataset_version_id: UUID, key: VehicleTaxKey
    ) -> TaxRecordDetails | None:
        async with self._sessions() as session:
            row = await session.get(
                ValueOfUseRow,
                (
                    tax_dataset_version_id,
                    key.tax_year,
                    key.registration_year,
                    key.vehicle_type_code,
                    key.manufacturer_code,
                    key.model_code,
                ),
            )
            return None if row is None else _tax_details(row)

    async def find_tax_records(
        self,
        tax_dataset_version_id: UUID,
        *,
        commercial_name: str,
        registration_year: str | None = None,
        manufacturer_code: str | None = None,
        model_code: str | None = None,
        limit: int,
    ) -> tuple[TaxRecordDetails, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        pattern = "%" + _escape_like(commercial_name.strip()) + "%"
        name = func.trim(ValueOfUseRow.source_values[_COMMERCIAL_NAME].astext)
        statement = select(ValueOfUseRow).where(
            ValueOfUseRow.tax_dataset_version_id == tax_dataset_version_id,
            name.ilike(pattern, escape="\\"),
        )
        for column, value in (
            (ValueOfUseRow.registration_year, registration_year),
            (ValueOfUseRow.manufacturer_code, manufacturer_code),
            (ValueOfUseRow.model_code, model_code),
        ):
            if value is not None:
                statement = statement.where(column == value)
        statement = statement.order_by(
            ValueOfUseRow.tax_year,
            ValueOfUseRow.registration_year,
            ValueOfUseRow.vehicle_type_code,
            ValueOfUseRow.manufacturer_code,
            ValueOfUseRow.model_code,
        ).limit(limit)
        async with self._sessions() as session:
            return tuple(_tax_details(r) for r in await session.scalars(statement))

    async def _release(
        self, session: AsyncSession, release_id: UUID | None
    ) -> PublishedRelease | None:
        query = (
            select(ReferenceReleaseRow, RulesSnapshotRow, CatalogueVersionRow, TaxDatasetVersionRow)
            .join(RulesSnapshotRow, RulesSnapshotRow.id == ReferenceReleaseRow.rules_snapshot_id)
            .join(
                CatalogueVersionRow,
                CatalogueVersionRow.id == ReferenceReleaseRow.catalogue_version_id,
            )
            .join(
                TaxDatasetVersionRow,
                TaxDatasetVersionRow.id == ReferenceReleaseRow.tax_dataset_version_id,
            )
        )
        if release_id is None:
            query = query.order_by(ReferenceReleaseRow.release_seq.desc()).limit(1)
        else:
            query = query.where(ReferenceReleaseRow.id == release_id)
        found = (await session.execute(query)).first()
        if found is None:
            return None
        release, rules, catalogue, tax = found
        return PublishedRelease(
            release_id=release.id,
            release_seq=release.release_seq,
            rules_snapshot_id=rules.id,
            catalogue_version_id=catalogue.id,
            tax_dataset_version_id=tax.id,
            identity=ReleaseIdentity(
                rules_sha256=rules.rules_sha256,
                policy_sha256=rules.policy_sha256,
                workbook_sha256=catalogue.workbook_sha256,
                tax_dataset_sha256=tax.csv_sha256,
                challenge_pdf_sha256=release.challenge_pdf_sha256,
            ),
            derived_policy_version=rules.derived_policy_version,
            published_at=release.published_at,
        )

    # --- publication -------------------------------------------------------------------------

    async def publish(
        self,
        sources: ReferenceSourceSet,
        consistency_report: Mapping[str, JsonScalar | Sequence[JsonScalar]],
        published_by: UUID,
    ) -> PublicationResult:
        identity = ReleaseIdentity.of(sources)
        async with self._sessions() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": PUBLICATION_LOCK_KEY}
            )
            latest = await self._release(session, None)
            if latest is not None and latest.identity == identity:
                return PublicationResult(release=latest, created=False)

            rules_id = await self._rules_snapshot(session, sources.rules)
            catalogue_id = await self._catalogue_version(session, sources.catalogue)
            tax_id = await self._tax_dataset_version(session, sources.tax_dataset)
            release_id = uuid4()
            await session.execute(
                insert(ReferenceReleaseRow).values(
                    id=release_id,
                    rules_snapshot_id=rules_id,
                    catalogue_version_id=catalogue_id,
                    tax_dataset_version_id=tax_id,
                    challenge_pdf_sha256=sources.challenge_pdf.pdf.sha256,
                    challenge_pdf_page_count=sources.challenge_pdf.page_count,
                    consistency_report=dict(consistency_report),
                    published_by=published_by,
                )
            )
            release = await self._release(session, release_id)
        assert release is not None
        return PublicationResult(release=release, created=True)

    async def _rules_snapshot(self, session: AsyncSession, rules: RulesSource) -> UUID:
        existing = await session.scalar(
            select(RulesSnapshotRow).where(
                RulesSnapshotRow.rules_sha256 == rules.rules.sha256,
                RulesSnapshotRow.policy_sha256 == rules.policy.sha256,
            )
        )
        if existing is not None:
            return existing.id
        reused = await session.scalar(
            select(RulesSnapshotRow).where(
                RulesSnapshotRow.derived_policy_version == rules.derived_policy_version
            )
        )
        if reused is not None:
            raise ReferenceDataConflictError(
                f"derived policy version {rules.derived_policy_version!r} is already published "
                f"with different content (sha256 {reused.policy_sha256}); bump the version and "
                "record the semantic change"
            )
        snapshot_id = uuid4()
        await session.execute(
            insert(RulesSnapshotRow).values(
                id=snapshot_id,
                supplied_version=rules.config.source.supplied_version,
                rules_sha256=rules.rules.sha256,
                derived_policy_version=rules.derived_policy_version,
                policy_sha256=rules.policy.sha256,
                rules_content=dict(rules.rules_content),
                policy_content=dict(rules.policy_content),
            )
        )
        return snapshot_id

    async def _catalogue_version(self, session: AsyncSession, catalogue: CatalogueSource) -> UUID:
        existing = await session.scalar(
            select(CatalogueVersionRow.id).where(
                CatalogueVersionRow.workbook_sha256 == catalogue.workbook.sha256,
                CatalogueVersionRow.policy_sha256 == catalogue.policy_sha256,
            )
        )
        if existing is not None:
            return existing
        version_id = uuid4()
        await session.execute(
            insert(CatalogueVersionRow).values(
                id=version_id,
                workbook_sha256=catalogue.workbook.sha256,
                policy_sha256=catalogue.policy_sha256,
                report=_report(catalogue.report),
            )
        )
        await session.execute(
            insert(VehicleRow),
            [
                {
                    "catalogue_version_id": version_id,
                    "catalogue_id": v.catalogue_id,
                    "track": v.track.value,
                    "entitlement_group": None if v.group is None else v.group.value,
                    "manufacturer_name": v.manufacturer_name,
                    "commercial_name": v.commercial_name,
                    "model_description": v.model_description,
                    "tax_year": v.tax_key.tax_year,
                    "registration_year": v.tax_key.registration_year,
                    "vehicle_type_code": v.tax_key.vehicle_type_code,
                    "manufacturer_code": v.tax_key.manufacturer_code,
                    "model_code": v.tax_key.model_code,
                    "propulsion": v.propulsion.value,
                    "list_price": v.list_price,
                    "value_of_use": v.catalogue_value_of_use,
                    "monthly_company_cost": v.monthly_company_cost,
                    "default_participation": v.default_participation,
                    "source_row_number": t.source.row_number,
                    "source_values": list(t.source.values),
                }
                for t in catalogue.vehicles
                for v in (t.vehicle,)
            ],
        )
        await session.execute(
            insert(OccupationPolicyRow),
            [
                {
                    "catalogue_version_id": version_id,
                    "occupation_id": o.occupation_id,
                    "name_he": o.name_he,
                    "entitlement_group": o.group.value,
                    "mandatory_car": o.mandatory_car,
                    "waiver_allowed": o.waiver_allowed,
                    "waiver_allowance": o.waiver_allowance,
                    "source_row_number": t.source.row_number,
                    "source_values": list(t.source.values),
                }
                for t in catalogue.occupations
                for o in (t.occupation,)
            ],
        )
        return version_id

    async def _tax_dataset_version(self, session: AsyncSession, dataset: TaxDatasetSource) -> UUID:
        existing = await session.scalar(
            select(TaxDatasetVersionRow.id).where(
                TaxDatasetVersionRow.csv_sha256 == dataset.csv.sha256
            )
        )
        if existing is not None:
            return existing
        version_id = uuid4()
        await session.execute(
            insert(TaxDatasetVersionRow).values(
                id=version_id,
                csv_sha256=dataset.csv.sha256,
                tax_years=list(dataset.tax_years),
                record_count=len(dataset.records),
                original_header=list(dataset.original_header),
                report=_report(dataset.report),
            )
        )
        # COPY on the session's own connection: same transaction, rolled back with it.
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        driver: psycopg.AsyncConnection[Any] = raw.driver_connection  # type: ignore[assignment]
        columns = ", ".join(VALUE_OF_USE_COLUMNS)
        async with driver.cursor() as cursor:
            async with cursor.copy(f"COPY business.value_of_use ({columns}) FROM STDIN") as copy:
                for r in dataset.records:
                    key = r.record.key
                    await copy.write_row(
                        (
                            version_id,
                            key.tax_year,
                            key.registration_year,
                            key.vehicle_type_code,
                            key.manufacturer_code,
                            key.model_code,
                            r.record.value_of_use,
                            r.record.effective_date,
                            r.drive_code,
                            r.source.row_number,
                            Jsonb(list(r.source.values)),
                        )
                    )
        return version_id
