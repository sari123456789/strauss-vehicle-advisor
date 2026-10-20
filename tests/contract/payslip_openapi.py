"""Export of the payslip-import API OpenAPI document (`contracts/openapi/payslip-api.json`).

Regenerate (from src/backend):
    PYTHONPATH=../.. .venv/Scripts/python.exe -m tests.contract.payslip_openapi
A separate document (same convention as `ai-api.json`) so the approved conversation contract is
not redefined. The Angular payslip models are checked against it by `test_payslip_openapi.py`.
"""

import json
import sys
from typing import Any

from fastapi import FastAPI

from app.api.routers import payslip_imports
from tests.paths import REPO_ROOT

OPENAPI_PATH = REPO_ROOT / "contracts" / "openapi" / "payslip-api.json"
OPENAPI_TITLE = "Strauss Vehicle Advisor - Payslip Import API"
OPENAPI_VERSION = "0.1.0"


def payslip_openapi() -> dict[str, Any]:
    app = FastAPI(title=OPENAPI_TITLE, version=OPENAPI_VERSION)
    app.include_router(payslip_imports.router, prefix="/api/v1")
    return app.openapi()


def rendered() -> str:
    return json.dumps(payslip_openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPENAPI_PATH.write_text(rendered(), encoding="utf-8", newline="\n")
    print(f"wrote {OPENAPI_PATH.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
