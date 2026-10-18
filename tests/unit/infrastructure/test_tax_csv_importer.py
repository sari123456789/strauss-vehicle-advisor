"""Tax dataset CSV import contract: cp1255, exact header, complete keys, leading zeros (AC29)."""

from decimal import Decimal

import pytest

from app.domain.vehicles import VehicleTaxKey
from app.infrastructure.imports.tax_csv_importer import (
    EXPECTED_HEADER,
    TaxDatasetFormatError,
    read_tax_dataset,
)
from tests.reference_sources import document, real_sources

ATTO3_TYPE1 = VehicleTaxKey("2026", "2025", "1", "01014", "00003")
TANG_TYPE2 = VehicleTaxKey("2026", "2025", "2", "01014", "00003")

HEADER = ",".join(f"{h} " for h in EXPECTED_HEADER)
ATTO3_ROW = (
    "2026,2025,1,01014,בי ווי די      ,00003,       SC2E-SC2E01       ,4,              ATTO 3     ,"
    "1,3,000,01,00000,02160,01/01/2025, 177500  , 181690  ,   3130      "
)
TANG_ROW = (
    "2026,2025,2,01014,בי ווי די      ,00003,         STE2-STE2       ,4,                TANG     ,"
    "1,3,000,01,00000,03204,01/06/2025, 327990  , 335733  ,   6950      "
)


def _csv(*rows: str, header: str = HEADER) -> bytes:
    return ("\r\n".join((header, *rows)) + "\r\n").encode("cp1255")


def _records_by_key() -> dict[VehicleTaxKey, Decimal]:
    return {r.record.key: r.record.value_of_use for r in real_sources().tax_dataset.records}


def test_real_dataset_matches_source_findings() -> None:
    dataset = real_sources().tax_dataset
    assert dataset.report.record_count == 77_988
    assert len(dataset.original_header) == 19
    assert dataset.tax_years == ("2026",)
    assert len({r.record.key for r in dataset.records}) == 77_988


def test_complete_key_distinguishes_atto3_from_tang() -> None:
    """T25/T26: without the vehicle type the partial key is ambiguous; complete keys are exact."""
    values = _records_by_key()
    assert values[ATTO3_TYPE1] == Decimal(3130)
    assert values[TANG_TYPE2] == Decimal(6950)
    partial = [
        k
        for k in values
        if (k.registration_year, k.manufacturer_code, k.model_code) == ("2025", "01014", "00003")
    ]
    assert len(partial) == 2


def test_codes_keep_leading_zeros() -> None:
    """T27: codes are strings, never coerced to integers."""
    record = next(r for r in real_sources().tax_dataset.records if r.record.key == ATTO3_TYPE1)
    assert record.record.key.manufacturer_code == "01014"
    assert record.record.key.model_code == "00003"
    assert record.drive_code == "3"


def test_original_values_and_header_are_kept_untrimmed() -> None:
    dataset = real_sources().tax_dataset
    assert dataset.original_header[0] == "שנת מס "
    assert (
        dataset.original_header[18].startswith("שווי שימוש ")
        and dataset.original_header[18] != "שווי שימוש"
    )
    record = next(r for r in dataset.records if r.record.key == ATTO3_TYPE1)
    assert record.source.values[6] == "       SC2E-SC2E01       "
    assert record.source.values[18].startswith("   3130 ")
    assert record.record.value_of_use == Decimal(3130)


def test_small_valid_file_parses_with_row_numbers() -> None:
    dataset = read_tax_dataset(document(_csv(ATTO3_ROW, TANG_ROW)))
    assert [r.source.row_number for r in dataset.records] == [2, 3]
    assert dataset.records[0].record.effective_date is not None
    assert dataset.records[0].record.effective_date.isoformat() == "2025-01-01"


def test_undecodable_byte_fails() -> None:
    raw = _csv(ATTO3_ROW).replace(b"ATTO", b"AT\xffO")
    with pytest.raises(TaxDatasetFormatError, match="cp1255"):
        read_tax_dataset(document(raw))


def test_utf8_encoded_file_fails_instead_of_being_guessed() -> None:
    raw = ("\r\n".join((HEADER, ATTO3_ROW)) + "\r\n").encode("utf-8")
    with pytest.raises(TaxDatasetFormatError):
        read_tax_dataset(document(raw))


@pytest.mark.parametrize(
    "header",
    [
        HEADER.replace("שווי שימוש", "שווי"),
        ",".join(EXPECTED_HEADER[:-1]),
        ",".join((EXPECTED_HEADER[1], EXPECTED_HEADER[0], *EXPECTED_HEADER[2:])),
    ],
    ids=["renamed", "missing", "reordered"],
)
def test_unexpected_header_fails(header: str) -> None:
    with pytest.raises(TaxDatasetFormatError, match="header"):
        read_tax_dataset(document(_csv(ATTO3_ROW, header=header)))


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (ATTO3_ROW + ",extra", "fields"),
        (ATTO3_ROW.replace(",01014,", ",1014,"), "manufacturerCode"),
        (ATTO3_ROW.replace(",00003,", ",3,"), "modelCode"),
        (ATTO3_ROW.replace("2026,2025,1,", "2026,2025,12,"), "vehicleTypeCode"),
        (ATTO3_ROW.replace("   3130      ", "   3130.5e1  "), "שווי שימוש"),
        (ATTO3_ROW.replace("   3130      ", "            "), "שווי שימוש"),
        (ATTO3_ROW.replace("   3130      ", "  -3130      "), "שווי שימוש"),
        (ATTO3_ROW.replace("01/01/2025", "2025-01-01"), "תאריך תחולה"),
        (ATTO3_ROW.replace(",1,3,000,", ",1,x,000,"), "drive code"),
    ],
    ids=[
        "extra-field",
        "short-manufacturer",
        "short-model",
        "type",
        "exponent",
        "blank-value",
        "negative",
        "date",
        "drive",
    ],
)
def test_invalid_record_fails_the_whole_import(row: str, message: str) -> None:
    with pytest.raises(TaxDatasetFormatError, match=message):
        read_tax_dataset(document(_csv(TANG_ROW, row)))


def test_duplicate_complete_key_fails_with_both_rows() -> None:
    with pytest.raises(TaxDatasetFormatError, match="rows 2 and 3"):
        read_tax_dataset(document(_csv(ATTO3_ROW, ATTO3_ROW)))


def test_empty_dataset_fails() -> None:
    with pytest.raises(TaxDatasetFormatError, match="no records"):
        read_tax_dataset(document(_csv()))
