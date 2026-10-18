"""Export of the Business API OpenAPI document (`contracts/openapi/business-api.json`).

Regenerate (from src/backend):
    PYTHONPATH=../.. .venv/Scripts/python.exe -m tests.contract.business_openapi
Lives on the test side because only `app.main` may import `app.api` (architecture rule);
`test_business_openapi.py` fails on drift.
"""

import json
import sys

from app.api.business.installer import business_openapi
from tests.paths import REPO_ROOT

OPENAPI_PATH = REPO_ROOT / "contracts" / "openapi" / "business-api.json"


def rendered() -> str:
    return json.dumps(business_openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPENAPI_PATH.write_text(rendered(), encoding="utf-8", newline="\n")
    print(f"wrote {OPENAPI_PATH.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
