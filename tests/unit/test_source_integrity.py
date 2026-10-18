"""Original challenge sources are byte-for-byte unchanged and readable with the mandated readers.

Full schema/record validation belongs to the import phase; these checks only guard integrity
and the encoding contract (CSV cp1255 strict, JSON UTF-8, workbook via openpyxl).
"""

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from tests.paths import REPO_ROOT

MANIFEST: dict[str, Any] = json.loads(
    (REPO_ROOT / "data" / "source-manifest.json").read_text(encoding="utf-8")
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


SOURCES = MANIFEST["sources"]
DOCUMENTS = MANIFEST["documents"]


# Immutable challenge sources: the normalized copy, the original and the manifest are identical.
@pytest.mark.parametrize("entry", SOURCES, ids=[e["path"] for e in SOURCES])
def test_normalized_source_matches_manifest_hash(entry: dict[str, Any]) -> None:
    assert _sha256(REPO_ROOT / entry["path"]) == entry["sha256"]


@pytest.mark.parametrize("entry", SOURCES, ids=[e["path"] for e in SOURCES])
def test_original_source_is_still_identical(entry: dict[str, Any]) -> None:
    original = REPO_ROOT / entry["originalPath"]
    if not original.exists():
        pytest.skip("original removed after normalization")
    assert _sha256(original) == entry["sha256"]


# Documentation copies may evolve through review: the committed doc must equal the reviewed
# baseline (any unreviewed edit fails); the supplied edition is kept as provenance only.
@pytest.mark.parametrize("entry", DOCUMENTS, ids=[e["path"] for e in DOCUMENTS])
def test_document_matches_reviewed_baseline(entry: dict[str, Any]) -> None:
    assert _sha256(REPO_ROOT / entry["path"]) == entry["sha256"]


@pytest.mark.parametrize("entry", DOCUMENTS, ids=[e["path"] for e in DOCUMENTS])
def test_document_provenance_is_recorded(entry: dict[str, Any]) -> None:
    assert entry["revisionNote"]
    original = REPO_ROOT / entry["originalPath"]
    if not original.exists():
        pytest.skip("supplied original edition is not part of the repository")
    assert _sha256(original) == entry["originalSha256"]


def test_csv_header_decodes_strictly_as_cp1255() -> None:
    path = REPO_ROOT / "data" / "source" / "vehicle-tax-2026.csv"
    with path.open("rb") as handle:
        first_line = handle.readline()
    header = next(csv.reader(io.StringIO(first_line.decode("cp1255", errors="strict"))))
    stripped = [h.strip() for h in header]
    assert len(stripped) == 19
    for key in ("שנת מס", "שנת רישום", "קוד סוג רכב", "קוד תוצר", "קוד דגם", "שווי שימוש"):
        assert key in stripped


def test_rules_json_is_utf8_with_supplied_version() -> None:
    path = REPO_ROOT / "data" / "source" / "business-rules.json"
    rules = json.loads(path.read_text(encoding="utf-8"))
    assert rules["version"] == "mock-2026-10-12"
    assert list(rules["budgets"]) == ["א", "ב", "ג", "ד"]


def test_workbook_opens_with_openpyxl() -> None:
    path = REPO_ROOT / "data" / "source" / "challenge-data.xlsx"
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        assert workbook.sheetnames == [
            "קרא אותי",
            "כללים ונתונים",
            "קטלוג רכבים",
            "עיסוקים",
            "עובדים לדוגמה",
            "חישובים",
        ]
    finally:
        workbook.close()
