"""Static import-boundary checks for the fixed architecture (architecture Section 2, CLAUDE.md).

Package layout: `app/{api, business, ai, contracts}` plus shared entry points
(`bootstrap`, `main`, `settings`, `runtime`, `cli`).

- Business Domain: standard library only.
- Business BL: Domain and standard library only (no ORM, HTTP, LLM, Pydantic).
- Business Application: Domain, BL, contracts and its own ports; never Infrastructure or frameworks.
- Business Infrastructure: Domain, Application ports and contracts; never services, BL, API or AI.
- AI module: never imports `app.business`; tools see only `app.contracts`.
- Contracts: pydantic and standard library only.
- API: outside both modules; delegates to Business/AI Application only.
- No import cycles between `app` modules.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

from tests.paths import BACKEND_APP

STDLIB = set(sys.stdlib_module_names) | {"__future__"}
LLM_LIBS = {"langgraph", "langchain", "langchain_core", "openai", "anthropic"}
DB_LIBS = {"sqlalchemy", "psycopg", "alembic", "asyncpg"}
WEB_LIBS = {"fastapi", "starlette", "httpx"}

DOMAIN = "app.business.domain"
BL = "app.business.bl"
BUSINESS_APP = "app.business.application"
PORTS = "app.business.application.ports"
BUSINESS_INFRA = "app.business.infrastructure"
AI = "app.ai"
CONTRACTS = "app.contracts"
API = "app.api"
SHARED_ENTRY_POINTS = ("app.bootstrap", "app.main", "app.settings", "app.runtime", "app.cli")


def _module_name(path: Path) -> str:
    rel = path.relative_to(BACKEND_APP.parent).with_suffix("")
    return ".".join(p for p in rel.parts if p != "__init__")


def _sources() -> dict[str, ast.Module]:
    return {
        _module_name(path): ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in BACKEND_APP.rglob("*.py")
        if "__pycache__" not in path.parts
    }


SOURCES = _sources()
MODULES = set(SOURCES)


def _imports(tree: ast.Module) -> list[str]:
    """Imported module names; `from pkg import sub` counts as `pkg.sub` when it is a module."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)
            found.extend(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if f"{node.module}.{alias.name}" in MODULES
            )
    return found


IMPORTS = [(module, imported) for module, tree in SOURCES.items() for imported in _imports(tree)]


def _top(name: str) -> str:
    return name.split(".")[0]


def _within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _violations(
    layer: str, allowed_app: tuple[str, ...], allowed_third_party: set[str] | None
) -> list[str]:
    """Imports from `layer` outside `allowed_app`; `allowed_third_party=None` allows any library."""
    bad = []
    for module, imported in IMPORTS:
        if not _within(module, layer):
            continue
        if _top(imported) == "app":
            if not any(_within(imported, a) for a in allowed_app):
                bad.append(f"{module} -> {imported}")
        elif (
            allowed_third_party is not None
            and _top(imported) not in STDLIB
            and _top(imported) not in allowed_third_party
        ):
            bad.append(f"{module} -> {imported}")
    return bad


def _forbidden(layer: str, forbidden_app: tuple[str, ...], forbidden_libs: set[str]) -> list[str]:
    return [
        f"{m} -> {i}"
        for m, i in IMPORTS
        if _within(m, layer)
        and (any(_within(i, f) for f in forbidden_app) or _top(i) in forbidden_libs)
    ]


def _subpackages(path: Path) -> set[str]:
    return {p.name for p in path.iterdir() if p.is_dir() and p.name != "__pycache__"}


def _top_level_files(path: Path) -> set[str]:
    return {p.stem for p in path.glob("*.py")}


# --- Structure ---------------------------------------------------------------------------


def test_scan_found_modules() -> None:
    assert any(_within(m, CONTRACTS) for m in MODULES)
    assert any(_within(m, DOMAIN) for m in MODULES)


def test_app_has_exactly_the_approved_top_level_layout() -> None:
    assert _subpackages(BACKEND_APP) == {"api", "business", "ai", "contracts", "cli"}
    assert _top_level_files(BACKEND_APP) == {"__init__", "bootstrap", "main", "settings", "runtime"}


def test_business_module_has_exactly_four_layers() -> None:
    business = BACKEND_APP / "business"
    assert _subpackages(business) == {"application", "bl", "domain", "infrastructure"}
    assert _top_level_files(business) == {"__init__"}


def test_ai_module_has_exactly_the_approved_packages() -> None:
    ai = BACKEND_APP / "ai"
    assert _subpackages(ai) == {"application", "workflow", "tools", "infrastructure"}
    assert _top_level_files(ai) == {"__init__"}


# --- Business Module layers --------------------------------------------------------------


def test_domain_is_independent() -> None:
    assert _violations(DOMAIN, (DOMAIN,), set()) == []


def test_business_logic_depends_only_on_domain() -> None:
    assert _violations(BL, (BL, DOMAIN), set()) == []


def test_application_uses_bl_domain_contracts_and_own_ports_only() -> None:
    allowed = (BUSINESS_APP, BL, DOMAIN, CONTRACTS)
    assert _violations(BUSINESS_APP, allowed, {"pydantic"}) == []


def test_infrastructure_depends_on_domain_ports_and_contracts_only() -> None:
    assert _violations(BUSINESS_INFRA, (BUSINESS_INFRA, DOMAIN, PORTS, CONTRACTS), None) == []


def test_ports_do_not_depend_on_application_services() -> None:
    assert _violations(PORTS, (PORTS, DOMAIN, CONTRACTS), set()) == []


@pytest.mark.parametrize("layer", [DOMAIN, BL, CONTRACTS, BUSINESS_APP])
def test_no_llm_db_or_web_frameworks_in_core_layers(layer: str) -> None:
    assert _forbidden(layer, (), LLM_LIBS | DB_LIBS | WEB_LIBS) == []


@pytest.mark.parametrize("package", ["business/domain", "business/bl"])
def test_no_binary_float_arithmetic_in_money_layers(package: str) -> None:
    """Money uses Decimal only: no float literals and no float() conversions."""
    root = BACKEND_APP / package
    assert root.is_dir(), f"money layer {package} not found"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                offenders.append(f"{path.name}:{node.lineno} float literal")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"float", "round"}:
                    offenders.append(f"{path.name}:{node.lineno} {node.func.id}()")
    assert offenders == []


def test_orm_and_database_drivers_only_in_infrastructure_persistence() -> None:
    """SQLAlchemy/psycopg/Alembic stay inside the modules' database infrastructure."""
    allowed = (f"{BUSINESS_INFRA}.database", f"{AI}.infrastructure")
    bad = [
        f"{m} -> {i}"
        for m, i in IMPORTS
        if _top(i) in DB_LIBS and not any(_within(m, a) for a in allowed)
    ]
    assert bad == []


# --- AI Module ---------------------------------------------------------------------------


def test_ai_module_never_imports_the_business_module() -> None:
    assert _forbidden(AI, ("app.business", API, *SHARED_ENTRY_POINTS), set()) == []


def test_ai_tools_see_only_contracts() -> None:
    assert _violations(f"{AI}.tools", (f"{AI}.tools", CONTRACTS), {"pydantic"} | LLM_LIBS) == []


def test_ai_has_no_database_access_outside_its_infrastructure() -> None:
    for layer in (f"{AI}.application", f"{AI}.workflow", f"{AI}.tools"):
        assert _forbidden(layer, (f"{AI}.infrastructure",), DB_LIBS) == [], layer


def _docstring_nodes(tree: ast.Module) -> set[int]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, owners)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


BUSINESS_SCHEMA_REF = re.compile(r"\b(business|auth)\.[a-z_]", re.IGNORECASE)


def test_ai_code_contains_no_business_schema_sql() -> None:
    """AI code must not reference business-owned schemas (`business.x`, `auth.x`) in strings."""
    offenders = []
    for module, tree in SOURCES.items():
        if not _within(module, AI):
            continue
        docstrings = _docstring_nodes(tree)
        offenders.extend(
            f"{module}: {node.value[:60]!r}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and BUSINESS_SCHEMA_REF.search(node.value)
        )
    assert offenders == []


# --- Shared packages ---------------------------------------------------------------------


def test_contracts_are_framework_free() -> None:
    assert _violations(CONTRACTS, (CONTRACTS,), {"pydantic"}) == []


def test_api_delegates_only_to_module_applications() -> None:
    allowed = (API, CONTRACTS, BUSINESS_APP, f"{AI}.application", "app.settings", "app.bootstrap")
    assert _violations(API, allowed, {"fastapi", "starlette", "pydantic"}) == []


def test_api_is_outside_both_modules() -> None:
    """Only the FastAPI entry point (`app.main`) imports the API; no module depends on it."""
    bad = [
        f"{m} -> {i}"
        for m, i in IMPORTS
        if _within(i, API) and not _within(m, API) and m != "app.main"
    ]
    assert bad == []


def test_shared_entry_points_contain_no_business_rules_or_ai_workflow() -> None:
    forbidden = (BL, DOMAIN, f"{AI}.workflow", f"{AI}.tools")
    for entry in SHARED_ENTRY_POINTS:
        assert _forbidden(entry, forbidden, set()) == [], entry


# --- Cycles ------------------------------------------------------------------------------


def test_no_import_cycles_between_app_modules() -> None:
    graph: dict[str, set[str]] = {m: set() for m in MODULES}
    for module, imported in IMPORTS:
        if imported in MODULES and imported != module:
            graph[module].add(imported)

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []
    counter = 0

    def visit(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in graph[node]:
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))

    for module in sorted(graph):
        if module not in index:
            visit(module)
    assert cycles == []


def test_backend_app_path_exists() -> None:
    assert isinstance(BACKEND_APP, Path) and (BACKEND_APP / "__init__.py").is_file()
