"""AI Engine LLM boundaries: provider SDKs and credentials stay in AI Infrastructure.

- Only `app.ai.infrastructure.llm` imports a provider SDK (google.genai, httpx).
- AI Application, Workflow and Tools never import AI Infrastructure (they see `LlmPort`).
- Only the LLM settings module reads environment variables inside `app.ai`.
"""

import ast
import re

from tests.paths import BACKEND_APP

AI_ROOT = BACKEND_APP / "ai"
PROVIDER_SDKS = {"google", "httpx", "httpx2", "anthropic", "openai", "groq"}
LLM_INFRA = "app.ai.infrastructure.llm"
SETTINGS_MODULE = "app.ai.infrastructure.llm.settings"


def _modules() -> dict[str, ast.Module]:
    result = {}
    for path in AI_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(BACKEND_APP.parent).with_suffix("")
        name = ".".join(p for p in rel.parts if p != "__init__")
        result[name] = ast.parse(path.read_text(encoding="utf-8"))
    return result


MODULES = _modules()


def _imports(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def test_scan_found_ai_modules() -> None:
    assert LLM_INFRA + ".gemini" in MODULES and "app.ai.workflow.graph" in MODULES


def test_provider_sdks_only_in_llm_infrastructure() -> None:
    offenders = [
        f"{module} -> {name}"
        for module, tree in MODULES.items()
        for name in _imports(tree)
        if name.split(".")[0] in PROVIDER_SDKS and not module.startswith(LLM_INFRA)
    ]
    assert offenders == []


def test_workflow_application_and_tools_do_not_import_ai_infrastructure() -> None:
    offenders = [
        f"{module} -> {name}"
        for module, tree in MODULES.items()
        if module.startswith(("app.ai.application", "app.ai.workflow", "app.ai.tools"))
        for name in _imports(tree)
        if name.startswith("app.ai.infrastructure")
    ]
    assert offenders == []


def test_only_the_settings_module_reads_the_environment() -> None:
    offenders = []
    for module, tree in MODULES.items():
        if module == SETTINGS_MODULE:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
                offenders.append(module)
            if isinstance(node, ast.Name) and node.id in {"environ", "getenv", "dotenv_values"}:
                offenders.append(module)
    assert offenders == []


def test_no_api_key_literal_in_ai_code() -> None:
    offenders = [
        module
        for module, tree in MODULES.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("AIza")
    ]
    assert offenders == []


# --- AI database boundary (P2c: response staging, checkpointer) ----------------------------

DB_DRIVERS = {"sqlalchemy", "psycopg", "psycopg_pool", "psycopg2", "asyncpg", "alembic"}
CHECKPOINTER_MODULES = ("langgraph.checkpoint.postgres",)
AI_INFRA = "app.ai.infrastructure"
MIGRATIONS_AI = BACKEND_APP.parent / "migrations_ai"
SQL_STATEMENT = re.compile(
    r"\b(SELECT\s.+\sFROM|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM|CREATE\s+(TABLE|INDEX|"
    r"FUNCTION|TRIGGER|SCHEMA)|ALTER\s+TABLE|DROP\s+(TABLE|SCHEMA|FUNCTION))\b",
    re.DOTALL,
)
QUALIFIED = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE|ON|FUNCTION|REFERENCES|EXISTS)\s+\"?([A-Za-z_]\w*)\"?\.\w"
)


def _strings(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
        elif isinstance(node, ast.JoinedStr):  # f-strings: keep the literal parts and {SCHEMA}
            parts = [
                v.value if isinstance(v, ast.Constant) else "agent"
                for v in node.values
                if isinstance(v, ast.Constant | ast.FormattedValue)
            ]
            found.append("".join(str(p) for p in parts))
    return found


AGENT_ALIAS = re.compile(r"\bagent\.\w+\s+(?:AS\s+)?([a-z]\w*)\b")
SQL_WORDS = {"on", "where", "set", "join", "values", "using", "for", "returning", "as"}
READ_ONLY_CATALOGS = {"information_schema", "pg_catalog"}
TRIGGER_RECORDS = {"new", "old"}


def _allowed_qualifiers(sql: str) -> set[str]:
    """`agent`, read-only system catalogs, trigger records and aliases of `agent` tables."""
    aliases = {a.lower() for a in AGENT_ALIAS.findall(sql)} - SQL_WORDS
    return {"agent"} | READ_ONLY_CATALOGS | TRIGGER_RECORDS | aliases


def test_database_drivers_and_checkpointer_only_in_ai_infrastructure() -> None:
    offenders = [
        f"{module} -> {name}"
        for module, tree in MODULES.items()
        if not module.startswith(AI_INFRA)
        for name in _imports(tree)
        if name.split(".")[0] in DB_DRIVERS or name.startswith(CHECKPOINTER_MODULES)
    ]
    assert offenders == []


def test_no_sql_outside_ai_infrastructure() -> None:
    offenders = [
        module
        for module, tree in MODULES.items()
        if not module.startswith(AI_INFRA)
        for value in _strings(tree)
        if SQL_STATEMENT.search(value)
    ]
    assert offenders == []


def test_ai_sql_references_only_the_agent_schema() -> None:
    trees = dict(MODULES)
    for path in MIGRATIONS_AI.rglob("*.py"):
        trees[f"migrations_ai.{path.stem}"] = ast.parse(path.read_text(encoding="utf-8"))
    offenders = sorted(
        {
            f"{module}: {schema}"
            for module, tree in trees.items()
            for value in _strings(tree)
            if SQL_STATEMENT.search(value)
            for schema in QUALIFIED.findall(value)
            if schema.lower() not in _allowed_qualifiers(value)
        }
    )
    assert offenders == []


def test_boundary_scanners_detect_violations() -> None:
    """Guard against vacuous checks: the scanners flag known-bad samples."""
    bad = ast.parse(
        "import psycopg_pool\n"
        "from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver\n"
        "Q = 'SELECT net FROM business.comparison_runs'\n"
    )
    assert {"psycopg_pool", "langgraph.checkpoint.postgres.aio"} <= set(_imports(bad))
    [query] = [v for v in _strings(bad) if SQL_STATEMENT.search(v)]
    assert QUALIFIED.findall(query) == ["business"]
    assert "business" not in _allowed_qualifiers(query)
    aliased = "SELECT 1 FROM agent.inputs i JOIN auth.users u ON u.id = i.id"
    assert "auth" in set(QUALIFIED.findall(aliased)) - _allowed_qualifiers(aliased)
    assert any(SQL_STATEMENT.search(v) for v in _strings(MODULES[f"{AI_INFRA}.persistence.store"]))
