"""`ReferenceSourceReader` implementation: reads and format-validates all supplied sources.

Each source is verified against `data/source-manifest.json` before parsing. Business
consistency across sources is checked by BL, invoked from the Application import service.
"""

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.application.ports.reference_data import (
    ReferenceSourceSet,
    RulesSource,
    SourceFingerprint,
)
from app.domain.vehicles import Propulsion
from app.infrastructure.imports.pdf_verifier import verify_pdf
from app.infrastructure.imports.rules_json_loader import (
    RulesFormatError,
    parse_json_document,
    read_json_document,
)
from app.infrastructure.imports.rules_loader import build_rules_config
from app.infrastructure.imports.source_document import SourceManifest
from app.infrastructure.imports.tax_csv_importer import KEY_COLUMNS, read_tax_dataset
from app.infrastructure.imports.workbook_importer import PROPULSION_BY_LABEL, read_catalogue

RULES_KIND = "business_rules_json"
WORKBOOK_KIND = "challenge_workbook_xlsx"
TAX_CSV_KIND = "vehicle_tax_dataset_csv"
PDF_KIND = "challenge_instructions_pdf"
POLICY_KIND = "derived_demo_policy_json"


def json_safe(value: Any) -> Any:
    """Copy of parsed JSON with Decimals as strings (never floats) for JSONB storage."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    return value


def _drive_field(rules: Mapping[str, Any]) -> dict[str, Propulsion]:
    raw = rules.get("valueOfUse", {}).get("driveField")
    if not isinstance(raw, Mapping) or not raw:
        raise RulesFormatError("valueOfUse.driveField: expected a non-empty object")
    mapping: dict[str, Propulsion] = {}
    for code, label in raw.items():
        propulsion = PROPULSION_BY_LABEL.get(label) if isinstance(label, str) else None
        if propulsion is None:
            raise RulesFormatError(f"valueOfUse.driveField[{code}]: unknown label {label!r}")
        mapping[str(code)] = propulsion
    return mapping


class FileReferenceSourceReader:
    def __init__(self, repo_root: Path, manifest_path: Path, policy_path: Path) -> None:
        self._repo_root = repo_root
        self._manifest_path = manifest_path
        self._policy_path = policy_path

    def read_all(self) -> ReferenceSourceSet:
        manifest = SourceManifest.load(self._repo_root, self._manifest_path)

        rules_doc = manifest.read(RULES_KIND)
        rules_json = parse_json_document(
            self._repo_root / rules_doc.fingerprint.path, rules_doc.raw
        )
        policy_json = read_json_document(self._policy_path)
        config = build_rules_config(rules_json, policy_json)
        if config.value_of_use_key != KEY_COLUMNS:
            raise RulesFormatError(
                f"valueOfUse.key {list(config.value_of_use_key)} differs from the CSV import "
                f"key {list(KEY_COLUMNS)}"
            )
        policy_fingerprint = SourceFingerprint(
            kind=POLICY_KIND,
            path=self._policy_path.relative_to(self._repo_root).as_posix(),
            sha256=policy_json.sha256,
            size_bytes=self._policy_path.stat().st_size,
        )
        rules = RulesSource(
            config=config,
            rules=rules_doc.fingerprint,
            policy=policy_fingerprint,
            rules_content=json_safe(rules_json.content),
            policy_content=json_safe(policy_json.content),
            drive_field=_drive_field(rules_json.content),
        )
        return ReferenceSourceSet(
            rules=rules,
            catalogue=read_catalogue(
                manifest.read(WORKBOOK_KIND), policy_json.content, policy_json.sha256
            ),
            tax_dataset=read_tax_dataset(manifest.read(TAX_CSV_KIND)),
            challenge_pdf=verify_pdf(manifest.read(PDF_KIND)),
        )
