"""Israel Tax Authority vehicle value-of-use dataset (CSV, Windows-1255).

Import contract:
- the whole file is decoded strictly as cp1255; any undecodable byte fails the import;
- the header, trimmed, must equal the 19 expected Hebrew column names in order;
- every record has exactly 19 fields; values are trimmed of surrounding whitespace only;
- codes stay strings (leading zeros kept) and are validated through the Domain tax key;
- value of use is a plain non-negative decimal string converted to `Decimal` (never float);
- the effective date is `dd/mm/yyyy`; the drive code is kept raw and uninterpreted (SRC-04);
- a duplicate complete five-part key fails the import.
Original field strings (untrimmed) are kept per record for traceability.
"""

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal

from app.business.application.ports.reference_data import (
    ImportReport,
    SourceRow,
    TaxDatasetSource,
    TracedTaxRecord,
)
from app.business.domain.errors import DomainError
from app.business.domain.vehicles import TaxRecord, VehicleTaxKey
from app.business.infrastructure.imports.source_document import SourceDocument

SOURCE_ENCODING = "cp1255"

TAX_YEAR = "שנת מס"
REGISTRATION_YEAR = "שנת רישום"
VEHICLE_TYPE = "קוד סוג רכב"
MANUFACTURER_CODE = "קוד תוצר"
MODEL_CODE = "קוד דגם"
DRIVE_CODE = "1-חשמלי.2-היברידי.3-פלג-אין"
EFFECTIVE_DATE = "תאריך תחולה"
VALUE_OF_USE = "שווי שימוש"

EXPECTED_HEADER: tuple[str, ...] = (
    TAX_YEAR,
    REGISTRATION_YEAR,
    VEHICLE_TYPE,
    MANUFACTURER_CODE,
    "תוצר",
    MODEL_CODE,
    "תאור דגם",
    "סוג דלק",
    "כינוי מסחרי",
    "לא-0.כן-1 אוטומט",
    DRIVE_CODE,
    "ציון ירוק",
    "דרגת זיהום",
    "נפח מנוע",
    "משקל",
    EFFECTIVE_DATE,
    "מחיר מחירון",
    "מחיר מתואם",
    VALUE_OF_USE,
)
KEY_COLUMNS: tuple[str, ...] = (
    TAX_YEAR,
    REGISTRATION_YEAR,
    VEHICLE_TYPE,
    MANUFACTURER_CODE,
    MODEL_CODE,
)

_PLAIN_DECIMAL = re.compile(r"^\d+(\.\d+)?$")
_DRIVE_CODE = re.compile(r"^\d$")
_COLUMN = {name: index for index, name in enumerate(EXPECTED_HEADER)}


class TaxDatasetFormatError(Exception):
    """The CSV does not satisfy the import contract. Nothing is imported."""

    code = "SOURCE_FORMAT"


def _decode(document: SourceDocument) -> str:
    try:
        return document.raw.decode(SOURCE_ENCODING, errors="strict")
    except UnicodeDecodeError as error:
        raise TaxDatasetFormatError(
            f"{document.fingerprint.path}: byte {error.start} is not valid {SOURCE_ENCODING}"
        ) from error


def _parse_date(value: str, row: int) -> date:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        raise TaxDatasetFormatError(f"row {row}: invalid {EFFECTIVE_DATE} {value!r}") from None


def _parse_record(fields: list[str], row: int) -> TracedTaxRecord:
    if len(fields) != len(EXPECTED_HEADER):
        raise TaxDatasetFormatError(
            f"row {row}: expected {len(EXPECTED_HEADER)} fields, found {len(fields)}"
        )
    value = [f.strip() for f in fields]
    try:
        key = VehicleTaxKey(*(value[_COLUMN[c]] for c in KEY_COLUMNS))
    except DomainError as error:
        raise TaxDatasetFormatError(f"row {row}: {error}") from error
    amount = value[_COLUMN[VALUE_OF_USE]]
    if not _PLAIN_DECIMAL.fullmatch(amount):
        raise TaxDatasetFormatError(f"row {row}: invalid {VALUE_OF_USE} {amount!r}")
    drive = value[_COLUMN[DRIVE_CODE]]
    if not _DRIVE_CODE.fullmatch(drive):
        raise TaxDatasetFormatError(f"row {row}: invalid drive code {drive!r}")
    return TracedTaxRecord(
        record=TaxRecord(
            key=key,
            value_of_use=Decimal(amount),
            effective_date=_parse_date(value[_COLUMN[EFFECTIVE_DATE]], row),
        ),
        drive_code=drive,
        source=SourceRow(row_number=row, values=tuple(fields)),
    )


def read_tax_dataset(document: SourceDocument) -> TaxDatasetSource:
    text = _decode(document)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration:
        raise TaxDatasetFormatError("the CSV is empty") from None
    trimmed = tuple(h.strip() for h in header)
    if trimmed != EXPECTED_HEADER:
        missing = [h for h in EXPECTED_HEADER if h not in trimmed]
        unexpected = [h for h in trimmed if h not in EXPECTED_HEADER]
        raise TaxDatasetFormatError(
            f"unexpected CSV header (missing {missing}, unexpected {unexpected}, or reordered)"
        )

    records: list[TracedTaxRecord] = []
    first_row: dict[VehicleTaxKey, int] = {}
    try:
        for fields in reader:
            row = reader.line_num
            traced = _parse_record(fields, row)
            previous = first_row.setdefault(traced.record.key, row)
            if previous != row:
                raise TaxDatasetFormatError(
                    f"duplicate complete key {traced.record.key} in rows {previous} and {row}"
                )
            records.append(traced)
    except csv.Error as error:
        raise TaxDatasetFormatError(f"row {reader.line_num}: malformed CSV ({error})") from error
    if not records:
        raise TaxDatasetFormatError("the CSV contains no records")

    tax_years = tuple(sorted({r.record.key.tax_year for r in records}))
    fingerprint = document.fingerprint
    return TaxDatasetSource(
        csv=fingerprint,
        original_header=tuple(header),
        tax_years=tax_years,
        records=tuple(records),
        report=ImportReport(
            kind=fingerprint.kind,
            sha256=fingerprint.sha256,
            record_count=len(records),
            details={"encoding": SOURCE_ENCODING, "columns": len(header), "taxYears": tax_years},
        ),
    )
