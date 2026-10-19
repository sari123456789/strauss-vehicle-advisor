"""Stage B fixtures: real Business reference release + demo data for AI <-> Business tests.

Safety (unchanged from the Business DB tests): the database is the configured `_test`
database (name must end in `_test` and differ from the development database); it is reset
only there. Source integrity is never bypassed: every canonical source must match
`data/source-manifest.json` before anything is imported, otherwise the tests fail.
"""

import hashlib
import json
from typing import Any

import pytest

from app.bootstrap import build_seed_components
from tests.integration.business.database.conftest import (  # noqa: F401 - shared fixtures
    IMPORT_OPERATOR,
    business_db,
    migrated_db,
    test_database_url,
)
from tests.integration.business.database.db_helpers import run
from tests.paths import REPO_ROOT

MANIFEST = REPO_ROOT / "data" / "source-manifest.json"


def canonical_sources() -> list[dict[str, Any]]:
    """Verify every canonical source against the manifest; never repair or skip a mismatch."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checked = []
    for entry in manifest["sources"]:
        path = REPO_ROOT / entry["path"]
        if not path.is_file():
            pytest.fail(f"canonical source missing: {entry['path']} (provide it; do not bypass)")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            pytest.fail(f"canonical source mismatch: {entry['path']} (wrong edition)")
        checked.append({**entry, "verified": digest})
    return checked


@pytest.fixture(scope="module")
def verified_sources() -> list[dict[str, Any]]:
    return canonical_sources()


@pytest.fixture(scope="module")
def stage_b_db(verified_sources: list[dict[str, Any]], business_db: str) -> str:  # noqa: F811
    """Migrated `_test` database + one published release + the fictional demo accounts."""

    async def seed() -> None:
        components = build_seed_components(business_db)
        try:
            await components.service.seed(IMPORT_OPERATOR, dry_run=False)
        finally:
            await components.engine.dispose()

    run(seed())
    return business_db
