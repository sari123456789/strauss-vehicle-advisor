# Cloud runbook — Neon database and Render/Vercel configuration

Companion to `docs/plans/cloud-integration-plan.md` (S2). No secret values appear here; they
live only in the git-ignored `src/backend/.env` (locally) and in the Render/Vercel dashboards.

## 1. Neon database (status 2026-10-19)

| Step | Who / where | Status |
|---|---|---|
| Neon project, database `neondb`, direct endpoint, `sslmode=require` | Business | Done |
| Business migrations `0001`–`0003` (`business`, `auth` schemas) | Business machine | **Done** (`0003_auth_credentials`) |
| Reference import (SHA-256 verified against `data/source-manifest.json`) | Business machine | **Done**: release 1, 20 vehicles (18 + 2 family), 14 occupations, 77,988 tax records |
| Demo seed (e001–e006, rep01, ops01; six profiles) | Business machine | **Done** |
| Demo passwords for `e001` and `rep01` (scrypt hashes only) | Business machine | **Done**; the passwords are kept privately by the Business developer |
| AI migrations (`agent` schema, `alembic_ai.ini`) | AI machine (needs AI packages) | **Pending** |

Neon's sample table `public.playing_with_neon` is unrelated and may be deleted.

### Commands (from `src/backend`, with the project virtual environment)
Set the URLs for the current shell only, from the local `.env` value `STRAUSS_NEON_URL` (direct
endpoint, prefix `postgresql+psycopg://`, `?sslmode=require`). Always confirm that the target
host ends in `.neon.tech` and is not the pooled endpoint (`-pooler`) before any write.

```powershell
$env:STRAUSS_DATABASE_URL = "<STRAUSS_NEON_URL>"
$env:STRAUSS_MIGRATION_DATABASE_URL = "<STRAUSS_NEON_URL>"
.venv\Scripts\python.exe -m alembic upgrade head          # Business schemas
.venv\Scripts\python.exe -m app.cli.import_data            # verifies hashes, publishes a release
.venv\Scripts\python.exe -m app.cli.seed_demo              # idempotent
$env:STRAUSS_NEW_PASSWORD = "<new password, at least 12 characters>"
.venv\Scripts\python.exe -m app.cli.set_password --login e001
```

AI machine (AI developer):

```powershell
$env:STRAUSS_AI_DATABASE_URL = "<STRAUSS_NEON_URL>"
$env:STRAUSS_AI_MIGRATION_DATABASE_URL = "<STRAUSS_NEON_URL>"
.venv\Scripts\python.exe -m alembic -c alembic_ai.ini upgrade head   # agent schema
```

The import, seed and password commands are idempotent or safe to re-run. Never run test suites
against Neon: tests use only databases whose name ends in `_test`.

## 2. Render Web Service environment variables (dashboard → Environment)

| Variable | Value | Needed from |
|---|---|---|
| `STRAUSS_ENVIRONMENT` | keep `development` until S1; then `production` **together with** `STRAUSS_AUTH_TOKEN_SECRET` (after S1, production refuses to start without the secret; today no code reads this variable) | S1 |
| `STRAUSS_DATABASE_URL` | Neon direct URL | now |
| `STRAUSS_MIGRATION_DATABASE_URL` | Neon direct URL | now |
| `STRAUSS_AI_DATABASE_URL` | Neon direct URL | A1/A2 |
| `STRAUSS_AI_MIGRATION_DATABASE_URL` | Neon direct URL | A1/A2 |
| `STRAUSS_AUTH_TOKEN_SECRET` | random, at least 32 bytes | S1 |
| `STRAUSS_AUTH_ALLOWED_ORIGINS` | `https://strauss-vehicle-advisor.vercel.app` | S1 |
| `STRAUSS_AUTH_COOKIE_SAMESITE` | `none` | S1 |
| `STRAUSS_WORKER_IN_PROCESS` | `true` | A2 (DECISION W) |
| `STRAUSS_AI_LLM_*` | Gemini free-tier profile (see `.env.example`) | A2 |

The variables marked S1/A1/A2 have no effect until that code is merged; setting them earlier
is harmless. After S1, set the Render health check path to `/api/v1/health/ready`.

## 3. Vercel
API base URL = the Render service URL (configured with A3).
