"""The committed payslip OpenAPI document matches the code, the routes are bearer-protected, and
the Angular payslip models use exactly the schema's property names (frontend drift check)."""

import json
import re

from tests.contract.payslip_openapi import OPENAPI_PATH, rendered
from tests.paths import REPO_ROOT

EXPECTED = {
    ("/api/v1/conversations/{conversationId}/payslip-imports", "post"),
    ("/api/v1/payslip-imports/{draftId}", "get"),
    ("/api/v1/payslip-imports/{draftId}", "patch"),
    ("/api/v1/payslip-imports/{draftId}", "delete"),
    ("/api/v1/payslip-imports/{draftId}/approve", "post"),
}
ANGULAR_MODELS = (
    REPO_ROOT / "drive-angular" / "src" / "app" / "features" / "payslip" / "payslip.models.ts"
)
# TypeScript interface -> OpenAPI schema it mirrors.
MIRRORED = {
    "PayslipEvidence": "EvidenceView",
    "PayslipDraftField": "DraftFieldView",
    "PayslipDraft": "DraftView",
    "PayslipCorrection": "CorrectionItem",
    "CorrectDraftRequest": "CorrectDraftRequest",
    "ApproveDraftRequest": "ApproveDraftRequest",
}


def test_payslip_openapi_has_no_drift() -> None:
    assert OPENAPI_PATH.is_file(), "run: python -m tests.contract.payslip_openapi"
    assert OPENAPI_PATH.read_text(encoding="utf-8") == rendered()


def test_payslip_routes_are_complete_and_bearer_protected() -> None:
    document = json.loads(rendered())
    found = {(p, m) for p, ops in document["paths"].items() for m in ops}
    assert found == EXPECTED
    for path, method in found:
        assert document["paths"][path][method].get("security") == [{"HTTPBearer": []}]
    upload = document["paths"]["/api/v1/conversations/{conversationId}/payslip-imports"]["post"]
    assert "application/pdf" in upload["requestBody"]["content"]


def _ts_properties(source: str, interface: str) -> set[str]:
    match = re.search(rf"export interface {interface} \{{(.*?)\n\}}", source, re.S)
    assert match, interface
    return set(re.findall(r"^\s+(\w+)\??:", match.group(1), re.M))


def test_angular_models_match_the_schema() -> None:
    schemas = json.loads(rendered())["components"]["schemas"]
    source = ANGULAR_MODELS.read_text(encoding="utf-8")
    for interface, schema in MIRRORED.items():
        assert _ts_properties(source, interface) == set(schemas[schema]["properties"]), interface
