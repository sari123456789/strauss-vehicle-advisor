"""Seed the fictional demo accounts and profiles. Does not start a worker.

Usage (from src/backend, after `alembic upgrade head` and `app.cli.import_data`):
    .venv\\Scripts\\python.exe -m app.cli.seed_demo --dry-run   # validate; write nothing
    .venv\\Scripts\\python.exe -m app.cli.seed_demo             # create missing accounts/profiles

Idempotent: existing accounts and profiles are never overwritten. Requires the challenge
workbook under data/source/ (not in Git). Exit codes: 0 success, 1 failure.
"""

import argparse
import io
import json
import sys
from dataclasses import asdict
from typing import Any

from app.bootstrap import build_seed_components
from app.business.application.seeding.demo_seed_service import DemoSeedReport
from app.cli.import_data import _cli_actor
from app.runtime import run_async
from app.settings import get_settings


def _summary(report: DemoSeedReport) -> dict[str, Any]:
    data = asdict(report)
    data["reference_release_id"] = str(report.reference_release_id)
    return {"status": "validated" if report.dry_run else "seeded", **data}


async def _main(dry_run: bool) -> DemoSeedReport:
    components = build_seed_components(get_settings().database_url.get_secret_value())
    try:
        return await components.service.seed(_cli_actor(), dry_run=dry_run)
    finally:
        await components.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed fictional demo accounts and profiles.")
    parser.add_argument("--dry-run", action="store_true", help="validate only; write nothing")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = run_async(_main(args.dry_run))
    except Exception as error:  # report every failure explicitly, with its stable code
        code = getattr(error, "code", type(error).__name__)
        failure = {"status": "failed", "code": code, "message": str(error)}
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(_summary(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
