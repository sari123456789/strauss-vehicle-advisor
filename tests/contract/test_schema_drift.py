"""Exported contract JSON Schemas (for Angular type generation) must match the Python models."""

from app.cli.export_contract_schemas import SCHEMA_DIR, find_drift
from tests.paths import REPO_ROOT


def test_schema_directory_is_in_repository_contracts_folder() -> None:
    assert SCHEMA_DIR == REPO_ROOT / "contracts" / "schemas"


def test_no_schema_drift() -> None:
    assert find_drift() == [], "run: uv run python -m app.cli.export_contract_schemas"
