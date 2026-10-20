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
| M3 | Outbox → AI relay (`BusinessEventRelay`) + E1 | **Done** — see commit below |
| M3B | B4 end-to-end verification + E2 | Not started |
| M4 | `ai-api.json` contract + drift test, `scripts/smoke.py`, manual Gemini check | Not started |
| M5 | Angular on the real API (auth, profile, chat polling, history, catalogue, comparison, representative) + test migration | Not started |
| M6 | `.env.example`, README, cloud runbook, business-decisions §20, final push and report | Not started |

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
- `ruff check`, `ruff format`, `mypy app`: clean.
- **Manual check against the local DB (M1):** `ready` 200, e001 login, `/me/profile` 200, and
  `POST /conversations` creates a conversation together with its case.

## Local environment notes
- The local dev DB `strauss` holds migrations 0001–0004 (Business) and 0001–0003 (AI), the
  checkpointer tables, the import and the seed.
- Demo passwords for e001, e003, e005 and rep01 are in the session scratchpad only (never committed).

## Next step (exact)
M3B:
1. Add `kind: RunKind = RunKind.REGULAR` to `ReadinessRequest` (`app/contracts/readiness.py`).
2. Pass it to the planner in `readiness_service.py`.
3. Send the goal kind from `app/ai/workflow/nodes/act.py` (`compute`).
4. Re-export the JSON schemas (`app/cli/export_contract_schemas.py`) and the Business OpenAPI.
5. Add HTTP and conversation tests for E-005 waiver (net "17806"), E-003 MG4 ("13109"),
   mandatory-car ineligibility (T21) and the unknown occupation (T23).
