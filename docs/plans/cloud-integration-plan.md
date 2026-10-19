# Cloud Integration Plan — work split for the Business and AI developers

**Status:** proposal by the Business developer (2026-10-19), to be agreed by both developers.
It is subordinate to `CLAUDE.md` and the authoritative documents in `docs/`. Items marked
**DECISION** must be confirmed by both developers before the affected work starts.

**Audience:** each developer's Claude Code session. Read the whole file, then work only on your
own track (Section 4 or 5) and on the shared items assigned to you (Section 3).

**Goal:** the complete demo running in the cloud as early as possible. That means an employee
signs in, chats in Hebrew, gets real Business calculations, and a representative answers a
pending question. Every milestone is verified against the real deployed system, never against
fakes.

---

## 1. Where we are (develop @ 8edc1b9)

| Area | State |
|---|---|
| Business BL / Application / persistence (4A-4C) | Done; tested on PostgreSQL (incl. six reference employees) |
| `BusinessToolsFacade` (13 tools) + `BusinessServices` in `bootstrap.py` | Done |
| Business HTTP API (`app/api/business/`, auth, Problem Details, OpenAPI `contracts/openapi/business-api.json`) | Done as an isolated component; **not wired into `app.main`** |
| AI workflow (LangGraph), gateway, persistence (`agent` schema, `migrations_ai`), input claims, checkpoints | Done; tested |
| AI <-> Business in-process (Stage A, Stage B) | Done; tested |
| `app.main` | Only `GET /api/v1/health` |
| AI conversation HTTP routes, worker loop, representative answers, frontend <-> server | **Missing** |
| API hosting | **Render Web Service already deployed** from `develop` (later `main`). Build: `pip install uv && cd src/backend && uv sync --no-dev && uv pip install --python .venv/bin/python uvicorn`. Start: `cd src/backend && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Serves `/api/v1/health` only until S1 lands. |
| Frontend hosting | **Vercel already deployed** from `develop` (later `main`): `https://strauss-vehicle-advisor.vercel.app/#/` (hash routing). Not yet connected to the API. |
| Database | **Neon database `neondb` exists and is reachable from the Business machine** (PostgreSQL 18, direct endpoint, `sslmode=require`; verified 2026-10-19). Not built yet: no migrations, no data; only Neon's sample table `public.playing_with_neon`. Its URL is stored locally as `STRAUSS_NEON_URL` in the git-ignored `src/backend/.env`. |

## 2. Target cloud topology (MVP)

| Component | Where | Notes |
|---|---|---|
| PostgreSQL | **Neon** (account exists), one database | Use the **direct** (non-pooled) connection string, `sslmode=require`, prefixed `postgresql+psycopg://`. Schemas `business`, `auth` and `agent` in one DB. One DB user for the demo (D8 role separation stays deferred). |
| API + worker | **Render Web Service** (existing; build and start commands in Section 1) | The worker loop runs **inside the same process** (see DECISION W). No new hosting setup is needed; S1 code plus env vars make it serve the full API. |
| Frontend | **Vercel** (existing): `https://strauss-vehicle-advisor.vercel.app` | `API_BASE_URL` points at the Render URL. This origin goes into `STRAUSS_AUTH_ALLOWED_ORIGINS`. |
| LLM | Gemini free tier | AI LLM profile env vars (see `.env.example`); `FREE_TIER_ACK=true` only for a billing-free project. |

**Deploy branch:** Render and Vercel auto-deploy from `develop` during integration; both switch
to `main` at the end, after a normal reviewed merge.

**Cross-origin setup:** the API (`onrender.com`) and the site (`vercel.app`) are on different
sites. Therefore:
- CORS allows the Vercel origin, with credentials.
- The refresh cookie must be `SameSite=None; Secure`. A `Strict` cookie is not sent
  cross-site, so S1 makes `SameSite` configurable: `strict` locally, `none` in the cloud. CSRF
  stays protected by the custom header plus the `Origin` check.

### Shared decisions to confirm first
- **DECISION W (worker):** run the input worker as an asyncio background task started in the
  FastAPI lifespan, controlled by `STRAUSS_WORKER_IN_PROCESS=true`. It uses the same code as a
  separate worker entrypoint, which can be split out later. Reason: Render's free tier has no
  background workers. Architecture §26 describes a separate worker process, so this is an
  approved MVP deviation only if both developers agree.
- **DECISION P (live updates):** first version uses **polling**:
  `GET /api/v1/conversations/{id}/messages?afterSequence=N` every 2 s while a turn is pending.
  SSE (architecture §19) comes later, once its auth is agreed (an EventSource cannot send
  `Authorization`; open item D4D-7).
- **DECISION R (routes):** adopt the Business OpenAPI document as the HTTP contract for business
  routes (`/api/v1/auth/*`, `/api/v1/me/profile`, `/api/v1/cases/{caseId}/*`), together with
  its Problem Details error shape. The frontend's proposed `HttpDriveClient` endpoints are
  replaced by these and by the AI conversation routes.

## 3. Shared integration — owner: **Business**; reviewer: AI

These are shared files (`app/main.py`, `app/bootstrap.py`, `app/settings.py`, `pyproject.toml`).
One owner avoids conflicts; the other developer reviews before the merge to `develop`.

### S1. Composition and auth settings (first PR, Business)
1. **`settings.py`:**
   - `auth_token_secret: SecretStr | None`. Required (at least 32 bytes) when
     `environment != "development"`; fail at startup otherwise.
   - `auth_allowed_origins: list[str]` (the Angular origin(s), e.g. the Vercel URL).
   - `auth_cookie_secure: bool = True`.
   - `auth_cookie_samesite: Literal["strict", "lax", "none"] = "strict"` (`none` in the cloud,
     because Vercel and Render are different sites; `HttpConfig` and the auth router read it).
2. **`bootstrap.py`:** `build_business_api(settings, services) -> BusinessApi`, which builds
   `AuthService(uow, ScryptPasswordHasher(), Hs256TokenCodec(secret, issuer="strauss",
   audience="strauss-api"), SystemClock())`, `HttpConfig(...)`, and `BusinessApi(auth,
   services.tools, services.cases, services.profiles, http)`. It reuses the existing
   `build_business_services` and `build_business_persistence`.
3. **`main.py`:**
   - a FastAPI **lifespan** that builds `BusinessServices` once, calls
     `install_business_api(app, api)`, exposes `services` on `app.state` for the AI wiring, and
     disposes engines on shutdown;
   - `CORSMiddleware` for `auth_allowed_origins`, with credentials, exposing `X-Request-ID` and
     allowing the `Authorization`, `Content-Type`, `X-CSRF-Protection` and `X-Request-ID`
     headers;
   - keep `/api/v1/health`, and add `/api/v1/health/ready` (DB `SELECT 1` plus "a reference
     release exists").
4. **AI mount point** (agreed signature, implemented by AI in A1/A2):
   `app.api.routers.conversations.router` included under `/api/v1`, and
   `app.bootstrap.build_ai_runtime(settings, services) -> AiRuntime`, which holds the
   conversation service, the turn runner and the worker loop. `main.py` calls it in the lifespan.
   Until A1 lands, `main.py` includes the router only if the module exists — no stub routes.
5. **Architecture tests:** `test_api_is_outside_both_modules` allows `app.main` to import
   `app.api.business`. No other rule changes.

### S2. Build the Neon database and configure the existing services (Business, can start now)
Render and Vercel already exist, so no new hosting setup is needed.
1. **Build the database** (from the Business machine; exact commands go in the runbook
   `docs/plans/cloud-runbook.md`):
   - for the session only, set `STRAUSS_DATABASE_URL`, `STRAUSS_MIGRATION_DATABASE_URL`,
     `STRAUSS_AI_DATABASE_URL` and `STRAUSS_AI_MIGRATION_DATABASE_URL` from `STRAUSS_NEON_URL`
     (the direct `neondb` URL in the git-ignored `src/backend/.env`; never in a committed file).
     Double-check the target before running any write, so the local Docker database is never
     confused with Neon;
   - `alembic upgrade head` (Business);
   - `alembic -c alembic_ai.ini upgrade head` (AI). Run it on the AI machine if the AI packages
     are missing on the Business machine;
   - `python -m app.cli.import_data` (it verifies the SHA-256 hashes against the manifest; the
     canonical sources exist only on the Business machine and are never committed);
   - `python -m app.cli.seed_demo`;
   - `python -m app.cli.set_password --login e001` and `--login rep01`.

   The Business machine reaches Neon directly, so the HTTPS block on PyPI does not affect the
   database.
2. **Render environment variables** (dashboard, secrets only there):
   - the four URLs above, `STRAUSS_ENVIRONMENT=production`;
   - `STRAUSS_AUTH_TOKEN_SECRET` (random, at least 32 bytes);
   - `STRAUSS_AUTH_ALLOWED_ORIGINS=https://strauss-vehicle-advisor.vercel.app`;
   - `STRAUSS_AUTH_COOKIE_SAMESITE=none`, `STRAUSS_WORKER_IN_PROCESS=true`;
   - the Gemini profile variables.

   Set the Render health check path to `/api/v1/health/ready` after S1.
3. **Vercel environment:** the API base URL is the Render URL.
4. **Build command:** the current command works, and `uvicorn` is installed at build time. A
   later clean-up, not blocking, could add `uvicorn[standard]` to `pyproject.toml` (on a machine
   with PyPI access) and switch to `uv sync --frozen --no-dev`.

## 4. Business track

| # | Work | Depends on | Output |
|---|---|---|---|
| B1 | S1 composition + auth settings | — | Business API live in `app.main` |
| B2 | S2: build Neon, load data, Render/Vercel env vars, runbook | — (can start now, in parallel to B1) | Cloud database with real data |
| B3 | **Representative answers** (the stage that was deferred until now) | — (parallel to B1) | Representative screen works end to end |
| B4 | Q1 `ReadinessRequest.kind` + `occupation.waiverAllowance` | Joint contract approval | Small contract change |

### B3 detail — representative answers (required for the representative screen)
1. **Migration `0004`** (additive):
   - `business.representative_answers` (one per request: outcome, value, unit, effective dates,
     representative subject, note, time);
   - `business.integration_outbox` (event_id, sequence, type, payload JSON, created_at,
     acknowledged_at);
   - the `field_code` CHECK extended only if the waiver-allowance code is approved.
2. **Representative application service** (role `representative`; never an employee profile or
   salary):
   - list open requests, minimal context (`RepresentativeRequestView`);
   - get one request;
   - submit `AnswerSubmit`. The request is version-checked and idempotent by `operationId`, and
     a second answer is `409`.
3. **Application of an answer** (one Business transaction):
   - store the answer, apply it where valid, increment the case version, write the outbox event
     `representative.answered` (`RepresentativeAnsweredEvent`), and commit;
   - `alternative.participation` (money, ILS, the effective dates cover the case reference date)
     becomes the alternative's participation override: the planner uses it instead of the
     catalogue default, with `participationSource="representative"`;
   - `employee.entitlementGroup`, `currentArrangement.propulsion` and
     `currentArrangement.vehicle` become case facts with `source=representative`;
   - `cannot_determine` blocks only that option;
   - outside the effective period, the answer is saved and `application=not_applicable`.
4. **`BusinessEventFeed` implementation:** `pending(limit)` returns unacknowledged rows in
   sequence order; `acknowledge(ids)` is idempotent. It is given only to the AI worker.
5. **`get_representative_request`** returns the real `AnswerView`.
6. **HTTP routes** under `/api/v1/representative-requests` (role `representative`): list, get,
   and `POST /{id}/answers`. Add them to the Business OpenAPI and run the drift test.
7. **Tests:** PostgreSQL integration only (no fakes):
   - an answer applies and a recalculated comparison uses the representative participation;
   - outside the period, not applied;
   - a second answer is `409`;
   - employee and foreign access is forbidden;
   - the outbox row is written atomically;
   - the feed redelivers until acknowledged.

## 5. AI track

| # | Work | Depends on | Output |
|---|---|---|---|
| A0 | Dependency changes needed by either side (PyPI is reachable only on the AI machine) | When requested | `uv.lock` updated |
| A1 | **Conversation HTTP routes** (architecture §19, polling per DECISION P) | S1 mount point | Chat over HTTP |
| A2 | **Worker loop** in-process (DECISION W), plus consumption of `BusinessEventFeed` | A1; B3 for answers | Turns are processed in the cloud |
| A3 | **Frontend integration** (Angular) | DECISION R; A1; S1 | Employee screen live |
| A4 | Representative screen in Angular | B3 routes | Representative flow live |

### A1 detail — conversation routes
All routes use the bearer token. The actor comes from `AuthService.authenticate` (the same
dependency as the Business API, `app.api.business.dependencies.current_actor`), never from the
body.
- `POST /api/v1/conversations` with `{clientRequestId, calculationMonth}`: calls
  `ConversationService.start_conversation`; idempotent; returns the conversation and case IDs.
- `GET /api/v1/conversations`: lists the actor's conversations.
- `GET /api/v1/conversations/{id}`: history and display state (`history`); a foreign
  conversation is `404`.
- `POST /api/v1/conversations/{id}/messages` with `{messageId, text}`: `submit_user_message`;
  returns `202` with the input ID. A repeat with the same ID and content creates nothing new.
- `GET /api/v1/conversations/{id}/messages?afterSequence=N`: the polling endpoint.
- `GET /api/v1/conversations/{id}/inputs/{inputId}`: the input state.

Errors use the same Problem Details shape and `BusinessRoute`-style handling, and never show
raw codes in UI text. Export the AI OpenAPI to `contracts/openapi/ai-api.json` with a drift
test.

### A2 detail — worker
- Loop: `claim_next_input`, then run the turn with `LangGraphTurnRunner(services.tools, llm,
  checkpointer=FencedPostgresSaver)`, then record the assistant messages, then `complete_input`.
- A temporary failure is a **retry with backoff, never `completed`** (architecture §18:
  5 s / 30 s / 2 min ... up to 1 h; dead after 10 attempts).
- The actor comes from the persisted conversation owner through
  `IdentityService.resolve(owner, Origin.WORKER)`.
- The event feed loop: `pending`, then enqueue the `representative_answered` input with dedup by
  `eventId`, and only then `acknowledge`.
- Graceful stop on shutdown; one instance (session advisory lock as designed).

### A3 detail — frontend
- Generate TypeScript types from `contracts/openapi/business-api.json` and `ai-api.json`;
  replace the proposed `HttpDriveClient`.
- Sign-in (access token in memory; refresh through the HttpOnly cookie with
  `X-CSRF-Protection: 1`).
- Side panel from `/me/profile`.
- Chat via conversation routes + polling.
- The comparison card renders `ComparisonResult` display values. Never compute amounts in the
  browser.
- Errors are mapped from `code` to Hebrew text.
- Configure `API_BASE_URL` per environment (Vercel: the Render URL). Calls use
  `withCredentials: true` for the auth routes, so the cross-site refresh cookie is sent.

## 6. Verification (real system only)
After every deploy, run the same smoke sequence against the cloud URL with the seeded demo
users. Keep it as a committed script `scripts/smoke_cloud.sh` (curl), with no fakes:
1. `GET /api/v1/health/ready` returns 200.
2. Login as `e001` returns 200 with an `accessToken`.
3. `POST /api/v1/conversations` returns a conversation and a case.
4. `POST /api/v1/cases/{caseId}/comparisons` for `vehicle:CAR-10` (regular,
   `includeCurrent=false`) returns `alternatives[0].net.display == "11693"` (E-001 reference
   value, agent-spec §8).
5. **After A1/A2:** send a message, poll, and receive an assistant reply.
6. **After B3/A4:** create a representative question, answer it as `rep01`, and see the
   employee's alternative recalculated.

The existing suites (unit, contract, architecture, PostgreSQL integration, Stage A/B) must pass
on the full-environment machine before each merge to `develop`.

## 7. Working rules for both Claude Code sessions
- Branches: Business works on `feature/business-backend`, AI on `integration/ai-business` (or
  `feature/ai-engine`). Merge to `develop` in small PRs, at least daily. Never commit to `main`.
- File ownership:
  - Business owns `app/business/**`, `app/api/business/**`, `migrations/**`,
    `tests/integration/business/**` and the S1/S2 shared items;
  - AI owns `app/ai/**`, `app/api/routers/conversations.py`, `migrations_ai/**`, `drive-angular/**`
    and `tests/integration/ai/**`;
  - `tests/integration/ai_business/` is shared, with new files only (no edits to the other
    side's files).
- `app/contracts/**`, `contracts/schemas/**` and the OpenAPI files change only with both
  developers' approval (CLAUDE.md).
- `pyproject.toml` and `uv.lock` change only on a machine with PyPI access; announce each
  dependency in the PR.
- Never commit `.env`, secrets, or `data/source/**`. Secrets live only in Render/Neon settings.
- No raw HTTP codes or stack traces in employee-facing text; no money computed outside Business.

## 8. Schedule (full-time work with Claude Code; days are working days)

| Milestone | Content | Earliest |
|---|---|---|
| **M1 — Business API in the cloud** | S1 + S2 (Neon built and loaded, Render env vars; hosting already exists); Postman: login, profile, comparison (smoke 1-4 once A1's `POST /conversations` exists; until then, steps 1-2) | Day 1 |
| **M2 — Chat in the cloud** | A1 + A2 (polling, in-process worker, Gemini free tier) | Day 2-4 |
| **M3 — Employee demo in the cloud** | A3 frontend connected and deployed | Day 4-6 |
| **M4 — Full demo incl. representative** | B3 + A4 | Day 5-8 |
| M5 — Hardening | SSE (if agreed), Q1, waiver allowance, D8 roles, merge to `main` | After M4 |

**Assumptions:**
- Both developers work in parallel.
- DECISIONS W, P and R are agreed on day 1.
- PyPI is reachable on the AI machine.
- Neon is reachable from the Business machine (verified 2026-10-19), so the data load runs there.

**Main risks:** the in-process worker on Render's free plan (the instance sleeps when idle, so
pending turns resume on wake-up), Gemini free-tier limits, and the frontend contract changes in
A3.
