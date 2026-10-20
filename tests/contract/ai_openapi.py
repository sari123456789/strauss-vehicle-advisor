"""Export of the AI conversation API OpenAPI document (`contracts/openapi/ai-api.json`).

Regenerate (from src/backend):
    PYTHONPATH=../.. .venv/Scripts/python.exe -m tests.contract.ai_openapi
The conversation routes (architecture §19, polling per decision P) are AI-owned; the Angular
client generates its TypeScript types from this document and `business-api.json`.
`test_ai_openapi.py` fails on drift.
"""

import json
import sys
from typing import Any

from fastapi import FastAPI

from app.api.routers import conversations
from tests.paths import REPO_ROOT

OPENAPI_PATH = REPO_ROOT / "contracts" / "openapi" / "ai-api.json"
OPENAPI_TITLE = "Strauss Vehicle Advisor - Conversation API"
OPENAPI_VERSION = "0.1.0"


def ai_openapi() -> dict[str, Any]:
    app = FastAPI(title=OPENAPI_TITLE, version=OPENAPI_VERSION)
    app.include_router(conversations.router, prefix="/api/v1")
    return app.openapi()


def rendered() -> str:
    return json.dumps(ai_openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPENAPI_PATH.write_text(rendered(), encoding="utf-8", newline="\n")
    print(f"wrote {OPENAPI_PATH.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
