"""Static import-boundary checks for the fixed architecture (architecture Section 2, CLAUDE.md).

- Domain: standard library only.
- BL: Domain and standard library only (no ORM, HTTP, LLM, Pydantic).
- Contracts: pydantic and standard library only.
- Application: Domain, BL, contracts and its own ports; never Infrastructure, API, AI or frameworks.
- Infrastructure: implements Application ports; never Application services, BL, API or AI.
- AI Engine (`app.agent`): business access only via Application; no BL, ORM or business repos.
- API: transport only; no BL, Infrastructure or ORM.
"""

import ast
import sys
from pathlib import Path

import pytest

from tests.paths import BACKEND_APP

STDLIB = set(sys.stdlib_module_names) | {"__future__"}
LLM_LIBS = {"langgraph", "langchain", "langchain_core", "openai", "anthropic"}
DB_LIBS = {"sqlalchemy", "psycopg", "alembic", "asyncpg"}
WEB_LIBS = {"fastapi", "starlette", "httpx"}


def _modules_and_imports() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for path in BACKEND_APP.rglob("*.py"):
        rel = path.relative_to(BACKEND_APP.parent).with_suffix("")
        module = ".".join(p for p in rel.parts if p != "__init__")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pairs.extend((module, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                pairs.append((module, node.module))
    return pairs


IMPORTS = _modules_and_imports()


def _top(name: str) -> str:
    return name.split(".")[0]


def _within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _violations(
    layer: str, allowed_app: tuple[str, ...], allowed_third_party: set[str]
) -> list[str]:
    bad = []
    for module, imported in IMPORTS:
        if not _within(module, layer):
            continue
        if _top(imported) == "app":
            if not any(_within(imported, a) for a in allowed_app):
                bad.append(f"{module} -> {imported}")
        elif _top(imported) not in STDLIB and _top(imported) not in allowed_third_party:
            bad.append(f"{module} -> {imported}")
    return bad


def test_scan_found_modules() -> None:
    assert any(_within(m, "app.contracts") for m, _ in IMPORTS)


def test_domain_is_independent() -> None:
    assert _violations("app.domain", ("app.domain",), set()) == []


def test_business_logic_depends_only_on_domain() -> None:
    assert _violations("app.business", ("app.business", "app.domain"), set()) == []


def test_contracts_are_framework_free() -> None:
    assert _violations("app.contracts", ("app.contracts",), {"pydantic"}) == []


def test_application_uses_ports_not_infrastructure() -> None:
    allowed = ("app.application", "app.domain", "app.business", "app.contracts")
    assert _violations("app.application", allowed, {"pydantic"}) == []


def _forbidden(layer: str, forbidden_app: tuple[str, ...], forbidden_libs: set[str]) -> list[str]:
    return [
        f"{m} -> {i}"
        for m, i in IMPORTS
        if _within(m, layer)
        and (any(_within(i, f) for f in forbidden_app) or _top(i) in forbidden_libs)
    ]


def test_infrastructure_depends_on_ports_only() -> None:
    bad = [
        f"{m} -> {i}"
        for m, i in IMPORTS
        if _within(m, "app.infrastructure")
        and (
            (_within(i, "app.application") and not _within(i, "app.application.ports"))
            or any(_within(i, f) for f in ("app.business", "app.api", "app.agent"))
        )
    ]
    assert bad == []


def test_ai_engine_reaches_business_only_through_application() -> None:
    forbidden = ("app.business", "app.infrastructure", "app.api")
    assert _forbidden("app.agent", forbidden, DB_LIBS) == []


def test_api_is_transport_only() -> None:
    assert _forbidden("app.api", ("app.business", "app.infrastructure"), DB_LIBS | LLM_LIBS) == []


@pytest.mark.parametrize(
    "layer", ["app.domain", "app.business", "app.contracts", "app.application"]
)
def test_no_llm_db_or_web_frameworks_in_core_layers(layer: str) -> None:
    assert _forbidden(layer, (), LLM_LIBS | DB_LIBS | WEB_LIBS) == []


@pytest.mark.parametrize("package", ["domain", "business"])
def test_no_binary_float_arithmetic_in_money_layers(package: str) -> None:
    """Money uses Decimal only: no float literals and no float() conversions."""
    offenders = []
    for path in (BACKEND_APP / package).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                offenders.append(f"{path.name}:{node.lineno} float literal")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"float", "round"}:
                    offenders.append(f"{path.name}:{node.lineno} {node.func.id}()")
    assert offenders == []


def test_orm_and_database_drivers_only_in_infrastructure_database() -> None:
    """SQLAlchemy/psycopg/Alembic stay inside `app.infrastructure.database` (ORM never leaks)."""
    bad = [
        f"{m} -> {i}"
        for m, i in IMPORTS
        if _top(i) in DB_LIBS and not _within(m, "app.infrastructure.database")
    ]
    assert bad == []


def test_reference_data_consistency_is_pure_business_logic() -> None:
    allowed = ("app.business", "app.domain")
    assert _violations("app.business.reference_data", allowed, set()) == []


def test_backend_app_path_exists() -> None:
    assert isinstance(BACKEND_APP, Path) and (BACKEND_APP / "__init__.py").is_file()
