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
| M3B | B4 end-to-end verification + E2 | **Done** — commit after M3 (see git log) |
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
- After M3B: E2 readiness tests 2 passed; B4 end-to-end tests 5 passed (E-005 waiver
  "17806" with allowance "2000"; E-003 MG4 "13109" with allowance "0"; mandatory-car waiver
  ineligible; conversation shows only Business amounts; occupation outside the table is not
  resolved and is refused with 422). Full suite: **1084 passed, 5 skipped, 0 failed**.
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

## Next step (exact)
M4:
1. Add `tests/contract/ai_openapi.py` and `tests/contract/test_ai_openapi.py`, which export the
   conversation routes to `contracts/openapi/ai-api.json` with a drift test.
2. Add `scripts/smoke.py`, using httpx with `--base-url`. Passwords come from the env variables
   `SMOKE_<LOGIN>_PASSWORD`. Steps:
   1. ready
   2. e001 login
   3. conversation
   4. CAR-10 comparison returns "11693"
   - 4b. e005 waiver comparison returns "17806"
   5. message and poll
   6. representative question, rep01 answer, then a follow-up message
3. Add an automated ASGI run of the smoke script with ScriptedLlm.
4. Run a manual Gemini check against the local uvicorn server:
   - run `--loop asyncio:SelectorEventLoop` with `STRAUSS_WORKER_IN_PROCESS=true`;
   - use two conversations: e001 with CAR-10 and e005 with the waiver.
