import os
from pathlib import Path

import pytest

from tests.paths import REPO_ROOT

SOURCE_FILES = (
    "data/source/business-rules.json",
    "data/source/challenge-data.xlsx",
    "data/source/challenge-instructions.pdf",
    "data/source/vehicle-tax-2026.csv",
)
REQUIRE_SOURCES = os.environ.get("STRAUSS_REQUIRE_SOURCES") == "1"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Tests marked `sources` need the challenge files under data/source/ (never in Git).

    Missing files skip such a test with an explicit reason, unless STRAUSS_REQUIRE_SOURCES=1
    (then the test fails). Existing source-dependent tests are not marked and fail loudly.
    """
    if item.get_closest_marker("sources") is None:
        return
    missing = [p for p in SOURCE_FILES if not (REPO_ROOT / p).is_file()]
    if missing:
        message = f"challenge source files are not available locally: {missing}"
        if REQUIRE_SOURCES:
            pytest.fail(message)
        pytest.skip(message)
