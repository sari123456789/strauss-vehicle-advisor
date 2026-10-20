"""Create or migrate the LangGraph checkpointer tables in the `agent` schema (deployment step).

Usage (from src/backend, after `alembic -c alembic_ai.ini upgrade head`):
    .venv\\Scripts\\python.exe -m app.cli.setup_checkpointer

Idempotent. Uses the AI migration identity (`STRAUSS_AI_MIGRATION_DATABASE_URL`). The API
never runs this on startup; it only verifies that the tables exist.
Exit codes: 0 success, 1 failure (the message never includes connection details).
"""

import json
import sys

from app.ai.infrastructure.checkpoints.postgres import setup_checkpointer
from app.ai.infrastructure.persistence.settings import load_database_settings
from app.runtime import run_async


def main() -> int:
    url = load_database_settings().migration_database_url.get_secret_value()
    try:
        run_async(setup_checkpointer(url))
    except Exception as error:  # report every failure explicitly, without the URL
        failure = {"status": "failed", "code": type(error).__name__}
        print(json.dumps(failure), file=sys.stderr)
        return 1
    print(json.dumps({"status": "ready"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
