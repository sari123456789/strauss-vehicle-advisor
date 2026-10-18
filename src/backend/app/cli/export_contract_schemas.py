"""Export JSON Schemas of the shared provisional contracts to `contracts/schemas/`.

Used for early Angular type generation until the full OpenAPI document exists.
Usage:
    uv run python -m app.cli.export_contract_schemas          # write files
    uv run python -m app.cli.export_contract_schemas --check  # exit 1 on drift
"""

import json
import sys
from pathlib import Path

from pydantic import BaseModel, JsonValue

from app.contracts import case_inputs, cases, catalogue, comparisons, profile, readiness
from app.contracts import representatives as reps
from app.contracts.case_lifecycle import CreateCaseCommand, CreateCaseResult
from app.contracts.common import CONTRACT_STATUS, CONTRACT_VERSION, Envelope

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"

EXPORTED_MODELS: dict[str, type[BaseModel]] = {
    "Envelope": Envelope[JsonValue],
    "EmployeeProfileView": profile.EmployeeProfileView,
    "CaseContext": cases.CaseContext,
    "UpdateInputsRequest": case_inputs.UpdateInputsRequest,
    "UpdateInputsResult": case_inputs.UpdateInputsResult,
    "ListOptionsRequest": catalogue.ListOptionsRequest,
    "ListOptionsResult": catalogue.ListOptionsResult,
    "ResolveVehicleRequest": catalogue.ResolveVehicleRequest,
    "ResolveVehicleResult": catalogue.ResolveVehicleResult,
    "TaxValueRequest": catalogue.TaxValueRequest,
    "TaxValueResult": catalogue.TaxValueResult,
    "EligibilityRequest": catalogue.EligibilityRequest,
    "EligibilityResult": catalogue.EligibilityResult,
    "ReadinessRequest": readiness.ReadinessRequest,
    "ReadinessResult": readiness.ReadinessResult,
    "CompareRequest": comparisons.CompareRequest,
    "ComparisonResult": comparisons.ComparisonResult,
    "ComparisonDetailsRequest": comparisons.ComparisonDetailsRequest,
    "ComparisonDetails": comparisons.ComparisonDetails,
    "CreateQuestionRequest": reps.CreateQuestionRequest,
    "CreateQuestionResult": reps.CreateQuestionResult,
    "RequestStatusView": reps.RequestStatusView,
    "RepresentativeRequestView": reps.RepresentativeRequestView,
    "AnswerSubmit": reps.AnswerSubmit,
    "RepresentativeAnsweredEvent": reps.RepresentativeAnsweredEvent,
    "CreateCaseCommand": CreateCaseCommand,
    "CreateCaseResult": CreateCaseResult,
}


def render_schemas() -> dict[str, str]:
    """Return file name -> deterministic JSON text (UTF-8, LF, sorted keys)."""
    files: dict[str, str] = {}
    for name, model in EXPORTED_MODELS.items():
        schema = model.model_json_schema(by_alias=True, mode="validation")
        files[f"{name}.schema.json"] = (
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
    index = {
        "contractStatus": CONTRACT_STATUS,
        "contractVersion": CONTRACT_VERSION,
        "schemas": sorted(files),
    }
    files["index.json"] = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return files


def find_drift(directory: Path = SCHEMA_DIR) -> list[str]:
    expected = render_schemas()
    drift = [
        name
        for name, text in expected.items()
        if not (directory / name).is_file()
        or (directory / name).read_text(encoding="utf-8") != text
    ]
    extra = (
        {p.name for p in directory.glob("*.json")} - set(expected) if directory.is_dir() else set()
    )
    return sorted(drift + list(extra))


def main(argv: list[str]) -> int:
    if "--check" in argv:
        drift = find_drift()
        for name in drift:
            print(f"schema drift: {name}", file=sys.stderr)
        return 1 if drift else 0
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in render_schemas().items():
        (SCHEMA_DIR / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(EXPORTED_MODELS) + 1} files to {SCHEMA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
