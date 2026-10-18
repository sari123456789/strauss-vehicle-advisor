"""Explicit reference-data import/report command (architecture A7). Does not start a worker.

Usage (from src/backend, after `python -m alembic upgrade head`):
    .venv\\Scripts\\python.exe -m app.cli.import_data --dry-run   # validate only, no DB writes
    .venv\\Scripts\\python.exe -m app.cli.import_data             # validate and publish

The CLI acts as a local `import_operator`; its subject ID is derived from the OS user name.
Exit codes: 0 success, 1 validation/publication failure.
"""

import argparse
import getpass
import io
import json
import sys
import uuid
from typing import Any

from app.bootstrap import build_import_components
from app.business.application.imports.import_service import ImportOutcome
from app.contracts.tool_context import ActorRef, Origin, Role
from app.runtime import run_async
from app.settings import get_settings


def _cli_actor() -> ActorRef:
    subject = uuid.uuid5(uuid.NAMESPACE_URL, f"strauss-cli-import-operator:{getpass.getuser()}")
    return ActorRef(subject_id=subject, roles=frozenset({Role.IMPORT_OPERATOR}), origin=Origin.CLI)


def _summary(outcome: ImportOutcome) -> dict[str, Any]:
    release = outcome.release
    return {
        "status": outcome.status.value,
        "identity": {
            "rulesSha256": outcome.identity.rules_sha256,
            "derivedPolicySha256": outcome.identity.policy_sha256,
            "workbookSha256": outcome.identity.workbook_sha256,
            "taxDatasetSha256": outcome.identity.tax_dataset_sha256,
            "challengePdfSha256": outcome.identity.challenge_pdf_sha256,
        },
        "consistency": outcome.consistency.as_dict(),
        "challengePdfPages": outcome.pdf_page_count,
        "warnings": [w for r in outcome.reports for w in r.warnings],
        "release": None
        if release is None
        else {
            "releaseId": str(release.release_id),
            "releaseSeq": release.release_seq,
            "rulesSnapshotId": str(release.rules_snapshot_id),
            "catalogueVersionId": str(release.catalogue_version_id),
            "taxDatasetVersionId": str(release.tax_dataset_version_id),
            "derivedPolicyVersion": release.derived_policy_version,
            "publishedAt": release.published_at.isoformat(),
        },
    }


async def _main(dry_run: bool) -> ImportOutcome:
    components = build_import_components(get_settings().database_url.get_secret_value())
    try:
        return await components.service.run(_cli_actor(), dry_run=dry_run)
    finally:
        await components.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and publish reference data.")
    parser.add_argument("--dry-run", action="store_true", help="validate only; write nothing")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        outcome = run_async(_main(args.dry_run))
    except Exception as error:  # report every failure explicitly, with its stable code
        code = getattr(error, "code", type(error).__name__)
        failure = {"status": "failed", "code": code, "message": str(error)}
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(_summary(outcome), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
