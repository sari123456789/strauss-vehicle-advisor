# Cloud runbook — Neon database and Render/Vercel configuration

Companion to `docs/plans/cloud-integration-plan.md` (S2), updated for the full-system integration
(`feature/full-system-integration`, 2026-10-20). No secret values appear here; they live only in
the git-ignored `src/backend/.env` (locally) and in the Render/Vercel dashboards.

**Every step below that writes to Neon, Render or Vercel requires explicit owner approval.**
None of them was executed during the integration work.

## 1. Neon database

| Step | Status |
|---|---|
| Neon project, database `neondb`, direct endpoint, `sslmode=require` | Done (2026-10-19) |
| Business migrations `0001`–`0003` (`business`, `auth` schemas) | Done (`0003_auth_credentials`) |
| Reference import (release 1: 20 vehicles, 14 occupations, 77,988 tax records) | Done |
| Demo seed (e001–e006, rep01, ops01; six profiles) | Done |
| Demo passwords for `e001` and `rep01` | Done (kept privately) |
| Business migration `0004_representative_answers` (answers, case facts, outbox, audit) | **Done** (2026-10-20, approved; verified: version 0004 and the four tables exist) |
| AI migrations `0001`–`0004` (`agent` schema) | **Done** (`0004_conversation_hidden` applied 2026-10-22, approved; verified: `agent.conversations.hidden_at` exists) |
| LangGraph checkpointer tables | **Done** (verified 2026-10-20) |
| Passwords for further demo logins (e.g. `e005` for the waiver demo) | Optional |

Neon's sample table `public.playing_with_neon` is unrelated and may be deleted.

### Commands (from `src/backend`, with the project virtual environment)
Set the URLs for the current shell only, from the local `.env` value `STRAUSS_NEON_URL` (direct
endpoint, prefix `postgresql+psycopg://`, `?sslmode=require`). Always confirm that the target
host ends in `.neon.tech` and is not the pooled endpoint (`-pooler`) before any write.

```powershell
$env:STRAUSS_DATABASE_URL = "<STRAUSS_NEON_URL>"
$env:STRAUSS_MIGRATION_DATABASE_URL = "<STRAUSS_NEON_URL>"
$env:STRAUSS_AI_DATABASE_URL = "<STRAUSS_NEON_URL>"
$env:STRAUSS_AI_MIGRATION_DATABASE_URL = "<STRAUSS_NEON_URL>"
.venv\Scripts\python.exe -m alembic upgrade head                    # Business: adds 0004
.venv\Scripts\python.exe -m alembic -c alembic_ai.ini upgrade head  # agent schema
.venv\Scripts\python.exe -m app.cli.setup_checkpointer              # LangGraph tables (idempotent)
$env:STRAUSS_NEW_PASSWORD = "<new password, at least 12 characters>"
.venv\Scripts\python.exe -m app.cli.set_password --login e005       # optional
```

All commands are idempotent or safe to re-run. Never run test suites against Neon: tests use
only databases whose name ends in `_test`. Behind HTTPS interception (local machine), uv and the
Gemini client need `SSL_CERT_FILE` pointing to a bundle with the interception root.

## 2. Render Web Service

**Build command:** `pip install uv && cd src/backend && uv sync --frozen --no-dev`. uvicorn is a
declared dependency now, so the separate `uv pip install uvicorn` step is no longer needed.

**Start command:** `cd src/backend && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
Linux uses the selector event loop by default; no `--loop` option is needed.

**Health check path:** `/api/v1/health/ready`. It returns 200 only when all of these hold:
- the Business and agent databases are reachable;
- a reference release exists;
- the in-process worker is alive, when it is enabled.

**Environment variables:**

Service URL: `https://strauss-vehicle-advisor-api.onrender.com`. As of 2026-10-20 it still runs the
old `develop` build (only `/api/v1/health`). It needs a build from the integration code.

| Variable | Value |
|---|---|
| `STRAUSS_ENVIRONMENT` | `production` (requires the token secret) |
| `STRAUSS_DATABASE_URL`, `STRAUSS_MIGRATION_DATABASE_URL` | Neon direct URL |
| `STRAUSS_AI_DATABASE_URL`, `STRAUSS_AI_MIGRATION_DATABASE_URL` | Neon direct URL |
| `STRAUSS_AUTH_TOKEN_SECRET` | random, at least 32 bytes |
| `STRAUSS_AUTH_ALLOWED_ORIGINS` | `https://strauss-vehicle-advisor.vercel.app` (CSRF origin check for refresh/logout) |
| `STRAUSS_AUTH_COOKIE_SECURE` | `true` |
| `STRAUSS_AUTH_COOKIE_SAMESITE` | `strict` with the Vercel rewrite below (same origin); `none` only without it |
| `STRAUSS_WORKER_IN_PROCESS` | `true` on **exactly one** instance (worker + outbox relay) |
| `STRAUSS_LOG_LEVEL` | `INFO` |
| `STRAUSS_AI_LLM_*` | Gemini free-tier profile (see `.env.example`); `FREE_TIER_ACK=true` only for a billing-free project |

**Startup fails clearly** with a schema-version message when a migration or the checkpointer step
is missing. The API never migrates by itself.

## 3. Vercel (Angular)

The frontend calls the same-origin path `/api/v1`. Vercel forwards `/api/*` to Render, so the
refresh cookie is first-party. This avoids third-party-cookie blocking in Safari and Chrome. The
rewrite is committed in `drive-angular/vercel.json` and points to
`https://strauss-vehicle-advisor-api.onrender.com`. It takes effect when Vercel builds a branch
that contains it, and the Vercel project's root directory must be `drive-angular`.

- Keep `STRAUSS_AUTH_ALLOWED_ORIGINS` set to the Vercel origin, because the browser's `Origin`
  header is forwarded.
- No Vercel environment variable is needed for the API URL.

**Fallback (not recommended):** a cross-site setup without the rewrite needs
`STRAUSS_AUTH_COOKIE_SAMESITE=none` and a different `API_BASE_URL` in the Angular build. Browsers
that block third-party cookies will lose the session on reload.

## 4. Post-deploy smoke check

`scripts/smoke.py --base-url https://strauss-vehicle-advisor-api.onrender.com` with `SMOKE_E001_PASSWORD`,
`SMOKE_E005_PASSWORD` and `SMOKE_REP01_PASSWORD` set. It checks these steps in order:
1. readiness;
2. login;
3. conversation start;
4. E-001 ATTO 3 `"11693"`;
   - 4b. E-005 waiver `"17806"`;
5. a chat reply;
6. the representative answer, followed by the conversation continuation.

## 5. Operations: representative events (outbox → AI)

- **Undelivered events:**
  `SELECT id, sequence, created_at FROM business.integration_outbox WHERE acknowledged_at IS NULL ORDER BY sequence;`
- **Relay counters:** `GET /api/v1/health/ready` → `info.relay` (`delivered`, `backingOff`,
  `quarantined`, `transientFailures`).
  - A quarantined event stays in the outbox and is retried hourly. It is logged at ERROR
    (`relay_event_not_accepted`) with its event ID only.
  - A restart resets the backoff and retries it immediately.
- **Conversation turns:** `agent.inputs.status` shows each turn's state (`retry`, `dead`,
  `needs_reconciliation`). Turns have their own retries, independent of event delivery.
