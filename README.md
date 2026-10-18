# Strauss Vehicle Advisor

Hebrew conversational vehicle-benefit advisor for the Strauss hackathon — a demonstration based on the
supplied challenge materials, **not** an approved payroll system. See `CLAUDE.md` and `docs/`.

## Status

Business Module only (branch `feature/business-backend`); no AI functionality yet. The Angular
frontend is developed on the `feature/frontend` branch.

- Step 0: setup, provisional shared contracts, fakes, tests.
- Step 1: Domain + deterministic Business Logic (payroll, gross-up, benefits, comparisons).
- Step 2: source importers (CSV cp1255, workbook, PDF integrity) and immutable reference-data
  persistence (Alembic, `business` schema, append-only releases). See `docs/business-decisions.md` §9.
- Package-layout refactor: two modules `app/business` and `app/ai` (skeleton), shared `app/api`
  and `app/contracts`; no behavior change. See `docs/business-decisions.md` §12.
- Step 3A: persistence foundation — migration `0002_business_cases` (`auth.users`, profiles,
  cases, alternatives, operations, result sets, comparison runs, representative requests),
  repositories, `SqlUnitOfWork`, optimistic versions, idempotency, demo seed. See §13 and
  `docs/plans/business-module-roadmap.md`.
- Step 3B: Business Application services — revalidated identity, authorization policy, employee
  profile and case services (actual vs hypothetical updates, idempotency, optimistic versions),
  contract changes C1–C4 (decision D1). See `docs/business-decisions.md` §14.

## Layout

```
docs/                     authoritative specs (architecture, agent-spec, integration-contracts, frontend-handoff)
                          + api-contracts.md (PROVISIONAL shared contracts) + business-decisions.md
data/source/              original challenge files (byte-for-byte; never edit)
data/source-manifest.json SHA-256 of every source and normalized document
contracts/schemas/        generated JSON Schemas of the shared contracts
data/rules/               derived demo policy (explicit decisions + mappings, versioned)
src/backend/              Python backend (package `app`); Alembic migrations in src/backend/migrations
tests/                    architecture, contract, unit and API tests
compose.yaml              local PostgreSQL
```

Backend package (`src/backend/app/`):

```
api/                      shared FastAPI boundary (outside both modules)
business/                 Business Module
  application/              use cases, authorization, Application-owned ports
  bl/                       deterministic Business Logic (payroll, gross-up, benefits, comparisons)
  domain/                   Domain / Core
  infrastructure/           PostgreSQL persistence and source imports
ai/                       AI Engine Module (skeleton; owned by the AI developer)
  application/  workflow/  tools/  infrastructure/
contracts/                shared integration contracts (Pydantic DTOs, Protocols)
cli/                      entry points (import_data, export_contract_schemas)
bootstrap.py main.py settings.py runtime.py   composition root and startup only
```

Tests mirror it: `tests/unit/business/{application,bl,domain,infrastructure}`,
`tests/integration/business/database`, `tests/architecture` (import boundaries, layout, cycles,
port implementations, authorization-context signatures).

## Local setup (Windows / PowerShell)

Prerequisites: Docker Desktop, `uv` (installed here via `py -m pip install --user uv`).

```powershell
py -m uv python install 3.12
cd src/backend
py -m uv sync
cd ../..
docker compose up -d postgres         # PostgreSQL 16.4 on localhost:55432, UTF-8
```

If HTTPS interception (e.g. antivirus web shield) causes `invalid peer certificate: UnknownIssuer` in uv,
point `SSL_CERT_FILE` at a CA bundle that includes the interception root for that session only. See
`docs/business-decisions.md` §3.

## Database and reference-data import

Setup order (architecture §26): migrate, then import. From `src/backend`:

```powershell
.venv\Scripts\python.exe -m alembic upgrade head            # business schema (migration identity)
.venv\Scripts\python.exe -m app.cli.import_data --dry-run    # validate all sources; writes nothing
.venv\Scripts\python.exe -m app.cli.import_data              # validate and publish one release
```

Every source must match `data/source-manifest.json`; a new source edition requires an explicit
manifest update. Re-importing identical sources is a no-op (`"status": "unchanged"`). Published
reference data is immutable (UPDATE/DELETE/TRUNCATE are rejected by the database); the active set
is the latest row of `business.reference_releases`. Any change to `data/rules/derived-demo-policy.json`
must bump `derivedPolicyVersion` and add a `revisions` entry, otherwise publication is rejected.

## Demo accounts and profiles (Step 3A)

After the import, seed the fictional reference employees (E-001…E-006) and two staff accounts:

```powershell
.venv\Scripts\python.exe -m app.cli.seed_demo --dry-run    # validate; writes nothing
.venv\Scripts\python.exe -m app.cli.seed_demo              # create missing accounts/profiles
```

The seed reads names and payroll values from the workbook and the explicit mapping in
`data/demo/demo-accounts.json`. Re-running creates nothing and never overwrites a profile.
Seeded facts are usable for the demo but not employee-confirmed (`lastConfirmedAt` is null); the
demo origin is recorded internally. Accounts have no passwords (authentication is Step 3B).

## Challenge source files on another machine

`data/source/` is never committed (redistribution not approved). On a new machine, obtain the four
files through the approved channel and place them under `data/source/` with the names in
`CLAUDE.md`; `python -m app.cli.import_data --dry-run` verifies them against
`data/source-manifest.json` (SHA-256 and size). Tests marked `sources` skip with an explicit
reason when the files are missing; set `STRAUSS_REQUIRE_SOURCES=1` to make them mandatory.

## Checks

```powershell
cd src/backend
py -m uv run pytest                                  # DB tests are skipped if PostgreSQL is down
$env:STRAUSS_REQUIRE_DB = "1"; py -m uv run pytest   # mandatory: DB tests must run and pass
$env:STRAUSS_REQUIRE_SOURCES = "1"                    # also require the data/source/ files
py -m uv run ruff check app ../../tests migrations --config pyproject.toml
py -m uv run ruff format --check app ../../tests migrations --config pyproject.toml
py -m uv run mypy app migrations
py -m uv run python -m app.cli.export_contract_schemas --check
```

DB integration tests use the separate database `strauss_test` (created automatically;
`STRAUSS_TEST_DATABASE_URL` overrides it). They refuse to run against any database whose name does not
end in `_test`, and downgrade migrations only there — never against the development database.

If `py -m uv run` fails with `uv trampoline failed to canonicalize script path`, call the virtual
environment directly, e.g. `.venv\Scripts\python.exe -m pytest`.

In VS Code, select the interpreter `src/backend/.venv` so imports resolve.
