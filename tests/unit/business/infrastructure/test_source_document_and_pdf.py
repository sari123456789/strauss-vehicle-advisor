"""Manifest integrity for every source; the PDF is verified for integrity/readability only."""

import json
import shutil
from pathlib import Path

import pytest

from app.business.infrastructure.imports.pdf_verifier import verify_pdf
from app.business.infrastructure.imports.source_document import SourceIntegrityError, SourceManifest
from tests.paths import REPO_ROOT
from tests.reference_sources import PDF_PATH, document, file_document, real_sources

MANIFEST = REPO_ROOT / "data" / "source-manifest.json"


def test_every_source_matches_the_manifest() -> None:
    manifest = SourceManifest.load(REPO_ROOT, MANIFEST)
    for kind in (
        "business_rules_json",
        "challenge_workbook_xlsx",
        "vehicle_tax_dataset_csv",
        "challenge_instructions_pdf",
    ):
        doc = manifest.read(kind)
        assert doc.fingerprint.sha256 == manifest.entry(kind).sha256


def _copy_repo(tmp_path: Path) -> Path:
    content = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for entry in content["sources"]:
        target = tmp_path / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / entry["path"], target)
    shutil.copyfile(MANIFEST, tmp_path / "source-manifest.json")
    return tmp_path


def test_changed_source_edition_fails_until_the_manifest_is_updated(tmp_path: Path) -> None:
    root = _copy_repo(tmp_path)
    rules = root / "data" / "source" / "business-rules.json"
    rules.write_bytes(rules.read_bytes() + b"\n")
    manifest = SourceManifest.load(root, root / "source-manifest.json")
    with pytest.raises(SourceIntegrityError, match="explicit manifest update"):
        manifest.read("business_rules_json")


def test_missing_source_fails(tmp_path: Path) -> None:
    root = _copy_repo(tmp_path)
    (root / "data" / "source" / "challenge-instructions.pdf").unlink()
    manifest = SourceManifest.load(root, root / "source-manifest.json")
    with pytest.raises(SourceIntegrityError, match="cannot read"):
        manifest.read("challenge_instructions_pdf")


def test_challenge_pdf_is_readable_and_hash_verified() -> None:
    pdf = real_sources().challenge_pdf
    assert pdf.page_count == 5
    assert pdf.pdf.sha256 == file_document(PDF_PATH, "pdf").fingerprint.sha256


def test_non_pdf_or_corrupt_pdf_fails() -> None:
    with pytest.raises(SourceIntegrityError, match="not a PDF"):
        verify_pdf(document(b"plain text"))
    with pytest.raises(SourceIntegrityError):
        verify_pdf(document(b"%PDF-1.7\n garbage without objects"))


def test_pdf_verification_derives_no_rules() -> None:
    """The PDF result carries only integrity facts - no text, no rule values."""
    pdf = real_sources().challenge_pdf
    assert set(pdf.__dataclass_fields__) == {"pdf", "page_count"}
