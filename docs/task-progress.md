# Full-system integration — task progress

Working file for resuming the integration if a session stops. The approved plan is mirrored
here; the authoritative specs remain the four documents in `docs/`.

## Goal
One working application: sign-in → Gemini conversation → Business tools → authoritative
comparison (including waiver/allowance, B4) → representative question → answer → outbox →
AI conversation resumes → history. Same architecture and payroll rules; no new business rules.

## Ground rules (approved)
- Branch `feature/full-system-integration` (created from `origin/develop` 20e9361, no upstream
  to develop). Push only to `origin/feature/full-system-integration`. No push/merge/PR to
  `develop`/`main`, no force push, no reset/clean/rebase.
- No Neon, no deploy, no production migrations. Local Docker PostgreSQL only
  (`localhost:55432`, database `strauss`; tests use temporary `_test` databases).
- Gemini only for a few manual end-to-end checks; automated tests use `ScriptedLlm`.
  The LLM profile `DEV` is configured in the git-ignored `src/backend/.env` (never print it).
- Approved shared-contract changes: **E1** `BusinessEventFeed.pending(limit, *, after_sequence)`
  and **E2** `ReadinessRequest.kind: RunKind = "regular"`. Anything else needs approval.
- **E3** (representative answer for waiver/allowance of an occupation missing from the table)
  stays blocked: no authoritative rule. Report as a partial B4 sub-case.
- History is built from existing conversation/case data (no new route); profile is read-only in
  the UI (actual updates go through the chat).

## Milestones
| # | Scope | Status |
|---|---|---|
| M1 | FastAPI lifespan composition, settings (auth/CORS/worker), readiness, checkpointer CLI, deps | **Done** — commit `4e95a49`, pushed |
| M2 | Representative HTTP routes (B3b) | **Done** — commit `01359c7`, pushed |
| M3 | Outbox → AI relay (`BusinessEventRelay`) + E1 | **Done** — commit `b243575`, pushed |
| M3B | B4 end-to-end verification + E2 | **Done** — commit `edb4f6f`, pushed |
| M4 | `ai-api.json` contract + drift test, `scripts/smoke.py`, manual Gemini check | **Done** — commit `2c6c05d`; Gemini verified 2026-10-20 after the key was replaced (see below) |
| M5 | Angular on the real API (auth, profile, chat polling, history, catalogue, comparison, representative) + test migration | **Done** — commit `3d6a0ef` |
| M6 | `.env.example`, README, cloud runbook, business-decisions §20, final push and report | **Done** — final commit (see git log) |

## Key decisions taken during implementation
- Token secret: required outside `development`/`test`; a random per-process secret otherwise.
- The startup checks both migration heads and the checkpointer tables (read-only). If they are
  not current, startup fails with a clear message and the API never migrates.
- `/api/v1/health/ready`: booleans only. It returns 503 as Problem Details when not ready. The
  relay counters appear under `info.relay`.
- Worker and relay run in-process only with `STRAUSS_WORKER_IN_PROCESS=true` (one instance).
- **Windows local server:** start `uvicorn` with `--loop asyncio:SelectorEventLoop`, because async
  psycopg cannot use the Proactor loop.
- **Dependency install behind TLS interception:** export the Windows roots to a PEM and set
  `SSL_CERT_FILE` before `uv sync`. The lock is unchanged except that it adds uvicorn and
  psycopg-pool.
- **Representative routes:**
  - They return `Envelope[...]` with status 200, the same pattern as the profile route.
  - A second answer to a request is 409 `INVALID_STATE_TRANSITION`.
  - A stale request version is 409 `VERSION_CONFLICT`.
- **Relay:**
  - Acknowledges an event only after the enqueue commits.
  - Permanent per-event failures are never acknowledged. They back off (30 s, 2 min, 10 min,
    then 1 h) and are quarantined after 10 attempts. The scan pages past them with E1.
  - Transient failures use a global backoff.
- Doc hash baseline in `data/source-manifest.json` is updated whenever an authoritative doc in
  `docs/` changes (revision note appended).

## Test results (actual runs)
- **After M1:** full backend suite, 1057 passed, 5 skipped. The skips are 1 opt-in live-LLM
  test and 4 tests for original-edition files that are not in the repo.
- **After M2:** representative HTTP, composition, contract and architecture tests: 180 passed.
- **After M3:**
  - Relay unit tests: 13 passed.
  - Representative flow integration tests: 3 passed. They cover the full answer → relay → AI
    resume → recalculation path with `participationSource=representative`, the feed cursor,
    and crash recovery.
  - Full suite: 1076 passed and 5 skipped, with one failure: the doc-hash baseline for
    `integration-contracts.md`. The baseline was then updated, and the integrity test now
    passes (15 passed, 4 skipped).
- After M3B: E2 readiness tests 2 passed; B4 end-to-end tests 5 passed (E-005 waiver
  "17806" with allowance "2000"; E-003 MG4 "13109" with allowance "0"; mandatory-car waiver
  ineligible; conversation shows only Business amounts; occupation outside the table is not
  resolved and is refused with 422). Full suite: **1084 passed, 5 skipped, 0 failed**.
- **After M4:**
  - `tests/contract/test_ai_openapi.py` passes (drift and bearer checks).
  - The smoke script passes all 7 steps against the real app (ASGI, ScriptedLlm).
  - Unit, API, contract, architecture, smoke and composition tests: 726 passed, 4 skipped.
- **Manual smoke against local uvicorn** (local DB, worker on):
  - Steps 1–4b PASS: ready, login, conversation, CAR-10 "11693", waiver "17806".
  - Steps 5–6 cannot pass with the current Gemini key, which Google rejects with
    `403 PERMISSION_DENIED` ("Your project has been denied access"). The key in `.env` also
    does not have the usual AI Studio format.
  - The system degrades correctly: the reply is `assistant_unavailable`, there is no crash and
    no invented answer.
- **Bug found and fixed in M4:** `GeminiProviderClient` did not keep the `genai.Client` alive.
  - After garbage collection the SDK closed its HTTP client, and every later call failed with
    "client has been closed" (`unexpected_RuntimeError`).
  - A regression test was added.
- **Diagnostics:** `app.*` loggers are now routed to stderr when the server has no logging
  configured. The level is set by `STRAUSS_LOG_LEVEL`, default INFO. `llm_call ... code=...`
  lines are visible.
- **Local TLS interception:** the Gemini SDK needs `SSL_CERT_FILE` pointing to the exported
  Windows roots, the same as uv.
- **After M5 (Angular):**
  - `npm run build`: OK, bundle check passed.
  - `npm test`: 15 passed (decimal-format 9, plus workflow 6 against `HttpTestingController`).
  - Playwright chromium: 29 passed. That includes the real-backend journey `e2e/integration/journey.spec.ts`.
  - Playwright webkit-mobile: 8 passed.
  - **1 failure that already exists on `develop`:** `home.visual` "logo: top" is 11px off against
    a 4px tolerance. Verified on the `develop` code with a stash; it comes from the logo change
    in `b0411be`.
- **M5 fixes found by the real journey:**
  - A race in the conversation store: a message could be sent to the previous conversation
    while a new one was being created. A generation counter and a `switching` state now
    prevent it.
  - The representative panel was missing `role=status`.
- **Local development:** refresh and logout require the browser origin in
  `STRAUSS_AUTH_ALLOWED_ORIGINS` (CSRF check), for example
  `http://localhost:4200,http://127.0.0.1:4200`.
- **Final run (M6):**
  - Full backend suite: **1088 passed, 5 skipped, 0 failed**. The 5 skips are the opt-in live
    Gemini test and 4 tests for original-edition source files that are not in the repo.
  - `ruff check`, `ruff format --check`, `mypy app`, `export_contract_schemas --check`: clean.
  - Angular: build OK, `npm test` 15 passed.
- `ruff check`, `ruff format`, `mypy app`: clean.
- **Manual check against the local DB (M1):** `ready` 200, e001 login, `/me/profile` 200, and
  `POST /conversations` creates a conversation together with its case.

## Local environment notes
- The local dev DB `strauss` holds migrations 0001–0004 (Business) and 0001–0003 (AI), the
  checkpointer tables, the import and the seed.
- Demo passwords for e001, e003, e005 and rep01 are in the session scratchpad only (never committed).

## B4 status
- Complete for all 14 occupations in the authoritative table: eligibility comes from the table
  and the allowance comes from the occupation, never from the group. Verified through HTTP and
  through a conversation.
- Partial sub-case (E3, blocked):
  - When an occupation is outside the table, the system clarifies (`resolve_occupation`) or
    escalates.
  - A representative's waiver or allowance answer for it is still rejected
    (`field_code_not_answerable_yet`), because no authoritative rule exists.

## Manual Gemini check (2026-10-20, new key)
- **Before the fix:** the interpretation schema was rejected with `400 INVALID_ARGUMENT`, because
  the approved free-tier model does not accept `minItems`/`maxItems`. They are now dropped from
  the provider schema, and Pydantic still enforces array lengths. A regression test was added.
- **Latency:** the free tier answered in about 13–21 s, with occasional `504`/`503` responses.
  The local timeout `STRAUSS_AI_LLM_PROFILE_DEV_TIMEOUT_SECONDS` was raised to 45; the maximum
  allowed is 60.
- **Real conversation (e001) against the local uvicorn server:**
  1. "אילו רכבים מתאימים לי?" returned the real catalogue from Business.
  2. "כמה אקבל נטו עם ATTO 3?" returned 11,693 ₪, which equals the Business display "11693".
     Gemini composed the explanation and used only that amount.
  - One turn hit a provider `503` and was answered with the deterministic "temporary problem,
    try again" message. The retry succeeded.
- **Smoke run:** steps 1–5 PASS. Step 6 depends on the model first running a calculation for the
  option (a name reference resolves only against options already shown in the conversation). It
  is covered deterministically by `test_smoke.py` and the M3 flow tests.

## Next step (exact)
The integration plan is complete on `feature/full-system-integration`. Remaining items need the
owner:
1. Approve the Neon steps: migration 0004, the AI migrations and the checkpointer tables
   (`docs/plans/cloud-runbook.md` §1).
2. Approve the Render/Vercel configuration (runbook §2–§3) once the Render URL is known.
3. Open a PR or merge into `develop`. This is a separate decision.

## Full system with payslip import (branch `feature/full-system-with-payslip`)
Merge of `feature/payslip-ai-import` (ee9f14d) into `feature/full-system-integration` (d10d6a9).
Common base: M4 (2c6c05d).
- Backend: no overlapping files. The payslip router, settings and bootstrap wiring merged as is.
  Business calculations, contracts and the AI workflow are unchanged.
- Angular: one session architecture, M5's `SessionService`, `authInterceptor` and `API_BASE_URL`.
  The payslip branch's parallel stack was removed: `ApiSession`, `apiAuthInterceptor`,
  `EmployeeApiClient` and the duplicate profile section.
  - The payslip client now goes through the shared interceptor.
  - The profile page shows M5's real profile table plus the payslip import.
  - The advisor sidebar offers the same component in compact form.
  - Both entry points use the active advisor conversation (`ConversationStore.ensureActive`).
- Verified locally (isolated database, deterministic model, no Gemini or Neon):
  - pytest: 1166 passed, 9 skipped.
  - ruff, format and mypy: clean.
  - Angular: build passes; unit tests 34 passed.
  - Real E2E: the M5 journey and the payslip journey both pass. The profile updated once
    (v1 to v2), a new AI case sees the new values, and another employee's draft access returns 404.
- Known issues, also present on `feature/full-system-integration`:
  - The M5 journey fails at "+ new conversation" for an account with no conversations yet. A race
    between `ensureActive()` on page load and the click; it reproduces on the pristine branch.
  - Visual home-page tests (1586×992) fail.
  - The webkit drawer focus test fails at 768px.

