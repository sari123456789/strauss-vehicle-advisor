"""Shared, cached access to the real supplied sources for import tests (read-only)."""

import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Any

from app.bootstrap import build_reference_source_reader
from app.business.application.ports.reference_data import ReferenceSourceSet, SourceFingerprint
from app.business.infrastructure.imports.source_document import SourceDocument
from tests.paths import REPO_ROOT

POLICY_PATH = REPO_ROOT / "data" / "rules" / "derived-demo-policy.json"
CSV_PATH = REPO_ROOT / "data" / "source" / "vehicle-tax-2026.csv"
WORKBOOK_PATH = REPO_ROOT / "data" / "source" / "challenge-data.xlsx"
PDF_PATH = REPO_ROOT / "data" / "source" / "challenge-instructions.pdf"


@cache
def real_sources() -> ReferenceSourceSet:
    return build_reference_source_reader(REPO_ROOT).read_all()


def policy_content() -> dict[str, Any]:
    content: dict[str, Any] = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    return content


def document(raw: bytes, kind: str = "test", path: str = "test-source") -> SourceDocument:
    """A source document built from bytes (for mutated copies of the real sources)."""
    fingerprint = SourceFingerprint(
        kind=kind, path=path, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)
    )
    return SourceDocument(fingerprint=fingerprint, raw=raw)


def file_document(path: Path, kind: str) -> SourceDocument:
    return document(path.read_bytes(), kind, path.relative_to(REPO_ROOT).as_posix())
