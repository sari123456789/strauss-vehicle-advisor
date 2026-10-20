# Proposal and integration handoff: AI-assisted payslip PDF import

**Status:** Implemented and wired on `feature/payslip-ai-import` (merged with
`feature/full-system-integration` M1–M4). Verified end to end locally (Angular → FastAPI →
Business → local PostgreSQL). HTTP DTOs remain PROVISIONAL until the shared-ownership review.
**Audience:** the full-system integration developer, the Business developer and the frontend developer.

## 1. Decisions that need sign-off

| # | Topic | Current authoritative text | Proposal |
|---|---|---|---|
| D0 | Profile updates from the UI | Integration note: "profile is read-only in the UI (actual updates go through the chat)". | **APPROVED (feature owner):** payslip import is a narrowly scoped exception. Only after explicit review and confirmation, the selected fields go through the existing `actual_update` (server re-validation, `expectedProfileVersion`, `operationId`). No salary amounts in conversation messages or logs. Entry points: profile page (main) and chat when salary data is missing, both hosting the same component. |
| D1 | Document upload scope | `docs/architecture.md` §1 (line 25) and `docs/agent-spec.md` T66 / exclusions table: payslip upload is **not** implemented in the initial demo; typed input only. | Add payslip PDF upload as a feature with review and explicit approval. The four authoritative docs are **not** edited on this branch; their owners update them during integration (§8). |
| D2 | Pension and study-fund rates | `EmployeeProfile` stores **rates** (`payroll.pensionEmployeeRate`, `payroll.studyFundEmployeeRate`), not deduction amounts. | Use a rate only when a percentage is **printed** on the employee line (unit conversion `6.00%` → `"0.06"`). Never derive a rate from amount ÷ salary. Otherwise the field is `rejected/rate_not_printed` or `missing`, and the employee types it. |
| D3 | External LLM and real payslips | No policy exists for sending payroll documents to an external provider. | `PayslipImportConfig.external_llm_enabled` is `False` by default (rules-only extraction; every field needs review). Enable it **only for synthetic payslips** until an approved data-handling policy explicitly authorizes otherwise. Regex redaction is best-effort and is no justification for sending real data. |
| D4 | Draft persistence | n/a | MVP uses `InMemoryDraftStore`: a **single-process demo store with no durable persistence** (§5). A durable `agent`-schema table is proposed in §6 but not implemented. |
| D5 | Provenance | `SourceType` has no `document_confirmed` value ("future-only"). | Approved values are written as ordinary employee-reported actual updates (`source=employee`), because the employee explicitly confirms them. Adding a document source type is a Business contract change for later. |

## 2. What was built (independently, on this branch)

| Area | Files |
|---|---|
| Application (AI Engine) | `src/backend/app/ai/application/payslip/` — `models.py`, `errors.py` (codes + Hebrew messages), `text.py` (normalization, Decimal parsing, best-effort redaction), `candidates.py` (Hebrew label rules), `extraction.py` (structured LLM task), `drafting.py` (rules + AI fusion, contract validation), `ports.py`, `service.py` |
| Infrastructure (AI Engine) | `src/backend/app/ai/infrastructure/payslip/` — `pdf_reader.py` (pypdf, in memory), `memory_store.py` (demo store) |
| Composition factory | `src/backend/app/ai/infrastructure/payslip/composition.py` — `build_payslip_service(conversations=, business=, llm=, external_llm_enabled=)` reuses the existing runtime services (no second auth, Business service or store) |
| Angular feature | `drive-angular/src/app/features/payslip/` — models, typed client (`PAYSLIP_API_BASE_URL` = `/api/v1`), `PayslipImportStore` (one instance per host), `<dr-payslip-import>` component, Hebrew error map, specs |
| Standalone HTTP router | `src/backend/app/api/routers/payslip_imports.py` (not included by `app.main`) |
| Tests | `tests/unit/ai/payslip/`, `tests/api/test_payslip_imports_api.py`, `tests/integration/ai_business/test_payslip_import_real_business.py` and `test_payslip_http.py` (marker `db`: real login/bearer, ownership, actual_update, case sync); Angular `*.spec.ts` in the feature folder |
| Synthetic fixtures | `tests/fakes/payslip/fixtures/*.pdf` and generator `tests/fakes/payslip/generate_fixtures.py` |

No existing file was modified: no shared contract, migration, workflow node, bootstrap, `main.py`, router or Angular file changed. No new runtime dependency: `pypdf` was already declared. The package layout respects `tests/architecture` (the payslip code lives inside the existing `application` and `infrastructure` packages of the AI Engine and imports only `app.contracts` and AI ports).

## 3. Flow

```
PDF bytes (raw body, ≤2 MB) ─► PypdfTextReader (in memory: magic, encrypted, ≤5 pages, text-layer check)
  ─► candidates: lines with numbers + label hints (field, employee/employer, monthly/cumulative,
     excluded net/total/tax/benefit lines, suspicious instruction-like text), redacted evidence
  ─► optional LLM (LlmTaskKind.INTERPRET, strict Pydantic schema, no tools): returns only
     {field, candidateId, value index, party, period, confidence} - never a number
  ─► drafting: rules always win; values come from the document's own digits as Decimal;
     validated with the authoritative `SetChange` contract adapters
  ─► PayslipDraft (status per field) ─► employee review / corrections (re-validated)
  ─► approve: ONE `update_conversation_inputs` (mode=actual_update, expectedProfileVersion,
     operationId) through `BusinessToolsPort` with a server-built ToolContext
```

### Field mapping (existing `FieldPath` values only; no new fields)

| Profile field | Typical payslip labels | Rule |
|---|---|---|
| `payroll.baseGross` | שכר יסוד, משכורת בסיס, Base Salary | Monthly line only; not total gross, net, or cumulative (מצטבר). |
| `payroll.commissionsGross` | עמלות, Commissions | Monthly. |
| `payroll.bonusGross` | בונוס, פרמיה, מענק, Bonus | Several bonus lines → `conflict` (never summed). |
| `payroll.pensionEmployeeRate` | פנסיה/תגמולי עובד + `%` | Employee line with a printed percent only; employer line never used. |
| `payroll.studyFundEmployeeRate` | קרן השתלמות עובד + `%` | Same. |
| `payroll.creditPoints` | נקודות זיכוי, Credit points | Printed number. |

Recognized but **never mapped**: net pay, total gross, income tax, national insurance, employer
contributions, cumulative/YTD values, reimbursements, and the taxable car benefit (שווי רכב, which
Business computes from the official tax dataset). Current-arrangement amounts are out of scope for the MVP.

### Field statuses

`extracted` (rules and AI agree on one value from an unambiguous line), `needs_review` (one proposed
value the employee must check: rules-only, AI-only, or AI-resolved), `conflict` (several values →
`alternatives`, no value), `rejected` (only employer/cumulative/negative/invalid evidence), `missing`,
`corrected` (typed by the employee). Approvable: `extracted`, `needs_review`, `corrected`.
Each field has evidence `{page, lineIndex, text}` (one redacted, ≤120-char line).

## 4. Security and privacy

- **No profile update before explicit approval.** Upload/review never call a Business write.
- **Approval re-validates** every selected field with the contract model, then Business re-parses
  the values, checks support limits, ownership, `expectedCaseVersion`/`expectedProfileVersion`
  and the `operationId` idempotency key. All fields are applied in one Business transaction,
  so nothing is partially applied.
- **Trusted context:** `ConversationService.turn_binding(actor, draft.conversationId)` builds the
  `ToolContext`; the client never supplies it. The actor comes from the bearer token.
- **Ownership:** another employee's draft or conversation is `404` (indistinguishable from missing);
  non-employee roles get `FORBIDDEN`.
- **No storage of the PDF** or of the full text; parsing is in memory (no temporary files).
  After approval, values and evidence are purged from the draft (statuses and the Business
  result with field names and versions remain until expiry).
- **Logs** carry draft id, sizes, page/candidate counts, statuses and codes only (tested).
- **Prompt injection:** document text is wrapped in `<document>` and declared untrusted; the model
  has no tools and cannot output values; instruction-like lines are excluded from candidates and,
  if present, the LLM is **not called at all** for that document; a model flagging instructions
  discards its own answer.
- **Rejected input:** non-PDF, malformed, encrypted, >2 MB, >5 pages, too much text, and
  image-only (scanned) PDFs (no OCR in the MVP), each with a natural Hebrew message.
- **Redaction is best-effort** (identity/bank/contact lines dropped, long digit runs and e-mails
  masked). It is not a guarantee; see D3.
- Synthetic fixtures only; no real payslip is committed or sent to any provider.

## 5. In-memory draft store — defined behavior (single-process demo)

| Situation | Behavior |
|---|---|
| Process restart | All drafts are lost → `DRAFT_NOT_FOUND` ("הטיוטה אינה זמינה עוד, אפשר להעלות את התלוש מחדש"). Nothing is recovered and the profile is not touched. |
| Multiple workers/instances | Not supported: drafts are not shared. Run one API process with this store. |
| Expiry (24 h, injected clock) | Checked on every access → `DRAFT_EXPIRED` and removal; purged lazily on upload. Approval of an expired draft is blocked. |
| Limits | ≤3 non-applied drafts per employee, ≤500 overall → `TOO_MANY_DRAFTS`. |
| Duplicate approval | Same `operationId` and payload → stored result replayed; same id, different payload → `OPERATION_PAYLOAD_MISMATCH`; different id after success → `DRAFT_ALREADY_APPLIED`. Across a restart, Business idempotency on `operationId` still prevents a second effect. |
| Concurrent requests | A per-draft `asyncio.Lock` serializes corrections, approval and cancellation; `expectedDraftVersion` rejects approvals of a draft that changed (`DRAFT_VERSION_CONFLICT`). |
| Approval failure | Draft returns to `review` with `lastError` (e.g. `PROFILE_VERSION_CONFLICT`); the employee reloads and retries. |

## 6. Proposed durable persistence (not implemented; needs coordination)

AI-owned table in the `agent` schema, no cross-schema FK, no PDF:
`payslip_drafts(id uuid pk, owner_subject_id uuid, conversation_id uuid, version int, status text,
mode text, page_count int, fields jsonb, document_issues jsonb, last_error text,
applied_operation_id uuid, applied_request_hash text, applied_result jsonb,
created_at timestamptz, expires_at timestamptz)` with a periodic purge of expired rows and value
purging on approval. `SELECT … FOR UPDATE` replaces the in-process lock.

## 7. Proposed HTTP API (PROVISIONAL DTOs, in `payslip_imports.py`)

Base `/api/v1`, bearer auth, Problem Details errors with `details.payslipCode` (+ `details.field`).

| Method | Path | Body | Result |
|---|---|---|---|
| POST | `/conversations/{conversationId}/payslip-imports` | raw PDF, `Content-Type: application/pdf` | `201 DraftView` |
| GET | `/payslip-imports/{draftId}` | — | `DraftView` |
| PATCH | `/payslip-imports/{draftId}` | `{expectedDraftVersion, corrections:[{field, value}]}` | `DraftView` (version+1) |
| POST | `/payslip-imports/{draftId}/approve` | `{expectedDraftVersion, expectedProfileVersion, operationId, fields:[...]}` | `UpdateInputsResult` |
| DELETE | `/payslip-imports/{draftId}` | — | `204` |

Values are decimal strings in profile units; **rates are fractions** (`"0.06"` = 6%). Upload is
synchronous (typically well under a second without the LLM; ≤10 s extraction timeout).

## 8. Shared-file wiring (done on this branch)

| File | Change |
|---|---|
| `app/main.py` | The lifespan resolves ONE LLM port (shared with the AI runtime) and sets `app.state.ai_payslip_imports = build_payslip_imports(...)`, released on shutdown. The router is included under `/api/v1`. CORS also allows `DELETE`. |
| `app/bootstrap.py` | `build_payslip_imports(services, runtime, llm=, settings=)` reuses `runtime.conversations` (ownership + trusted `ToolContext`) and `services.tools` (`actual_update`). |
| `app/settings.py` | `payslip_external_llm` (`STRAUSS_PAYSLIP_EXTERNAL_LLM`), default `false`. |
| `contracts/openapi/payslip-api.json` | A new, separate document (`tests/contract/payslip_openapi.py`) with drift, bearer and Angular-model checks. `ai-api.json` is unchanged. |

Still open for the document owners: `architecture.md` §1/§19, `agent-spec.md` T66 and the
exclusions table, `integration-contracts.md` and `frontend-handoff.md`. A system message in the
conversation after approval is optional; it is not implemented, so no workflow node changed. The
agent already sees the new values through `get_conversation_context`, because the case
synchronizes to the new profile version (tested).

### Running locally (as verified)

1. Docker PostgreSQL `localhost:55432/strauss`, never Neon.
2. With `STRAUSS_DATABASE_URL` and `STRAUSS_MIGRATION_DATABASE_URL` set to the local URL:
   - `alembic -c alembic.ini upgrade head`
   - `alembic -c alembic_ai.ini upgrade head`
   - `python -m app.cli.setup_checkpointer`
   - `python -m app.cli.import_data`
   - `python -m app.cli.seed_demo`
   - `STRAUSS_NEW_PASSWORD=… python -m app.cli.set_password --login e001` (repeat for `e002`)
3. API: uvicorn `app.main:app` on `127.0.0.1:8000` with the asyncio **Selector** loop (Windows),
   `STRAUSS_AUTH_COOKIE_SECURE=false` for plain-HTTP local development.
4. Angular: `npm start` (`proxy.conf.json` forwards `/api/v1` to port 8000). Sign in as `e001`,
   then open **הפרופיל**.
5. Browser E2E:
   `E2E_PAYSLIP=1 E2E_LOGIN=e001 E2E_OTHER_LOGIN=e002 E2E_PASSWORD=… npx playwright test e2e/payslip --project=chromium`

## 9. Frontend (Angular)

**Implemented and placed on the profile page.**

Minimal real-API infrastructure, the smallest piece the full Angular integration (M5) can adopt
or replace, in `drive-angular/src/app/core/api/`:
- `ApiSession`: login and refresh through the HttpOnly cookie, with the CSRF header. The access
  token is kept **in memory only**.
- `apiAuthInterceptor`: adds the bearer token to `/api/v1/*` and renews once on a 401. Demo `/api`
  calls are untouched.
- `EmployeeApiClient`: `GET /me/profile`, and the employee's working conversation through the
  idempotent `POST /conversations`, with a `clientRequestId` remembered per employee and month.

Shared Angular files touched (minimal):
- `main.ts`: registers the interceptor.
- `sign-in.page.*`: real login for employees; the representative view is still the local demo.
- `profile.page.*`: inserts `<dr-profile-payslip-section>`, the real salary facts plus the import.
- `angular.json` and `proxy.conf.json`: the dev proxy.

The chat entry point is **not** wired: the advisor page still runs on demo data (M5). The same
component is meant to be hosted there:

```html
<dr-payslip-import
  [conversationId]="conversationId"      <!-- active conversation of the employee -->
  [profileVersion]="profile.profileVersion"
  [compact]="false"                     <!-- true inside the chat panel -->
  (applied)="reloadProfile()"           <!-- emits UpdateInputsResult (new profileVersion) -->
  (profileStale)="reloadProfile()"      <!-- PROFILE_VERSION_CONFLICT: pass the new version -->
  (closed)="hidePayslipImport()" />
```

The legacy demo upload field ("העלאת תלוש או חוזה", never sent to the server) is still in the
demo profile form. Remove it when M5 replaces the demo form.

### Design notes (as implemented)

- **Entry point:** a secondary button "העלאת תלוש שכר (PDF)" in the profile side panel, next to
  the salary facts; also allowed from the chat composer's attach action. Accept `application/pdf`,
  check size (≤2 MB) client-side.
- **States:** `uploading` (progress bar) → `extracting` (spinner, "קורא את התלוש…") → `review` →
  `confirming` → `done` | `error`.
- **Review table (RTL):** field label (Hebrew), proposed value (₪ / % for rates, shown as percent but
  sent as a fraction), source ("עמוד 1: <evidence text>"), status chip:
  extracted = green "זוהה", needs_review = amber "לבדיקה", conflict = amber with a picker of
  `alternatives` plus free input, missing = grey "לא נמצא — אפשר להזין", rejected = red with the
  reason (employer share / cumulative / rate not printed), corrected = blue "עודכן ידנית".
- Editing a value calls PATCH with `expectedDraftVersion`; use the returned version.
- **Confirmation:** a checkbox per approvable field (pre-checked only for `extracted`), a summary
  sentence, and "אשר ועדכן את הפרופיל" disabled while any checked field is not approvable.
  Send a client-generated `operationId` and keep it for retries of the same click.
- **After success:** refresh the profile side panel (new `profileVersion`) and show
  "הפרופיל עודכן. אפשר להמשיך בשיחה ולבקש השוואת רכבים." Hypothetical "what if" values are
  never set through this flow.
- **Errors:** map `details.payslipCode` to the Hebrew texts in `errors.py` (`MESSAGES_HE`); never
  show HTTP codes. `PROFILE_VERSION_CONFLICT` → reload profile and draft, keep choices;
  `DRAFT_NOT_FOUND`/`DRAFT_EXPIRED` → offer re-upload; `SCANNED_UNSUPPORTED`/`ENCRYPTED_PDF` →
  suggest a digital PDF or manual entry.

## 10. Verification status

| Level | Status |
|---|---|
| Independent feature implementation | Done on `feature/payslip-ai-import`: backend unit, router, real-Business and real-HTTP (login, ownership, actual_update) tests pass locally; Angular feature specs pass (vitest). |
| API/frontend integration | **Done** for the profile page (§8, §9). The chat entry point waits for the real chat UI (M5). |
| Browser end-to-end (Playwright, real stack) | **Passed locally** (`e2e/payslip/payslip-journey.spec.ts`, two consecutive runs): login, profile, upload of the synthetic PDF, extraction, correction, explicit approval, exactly one profile update, refreshed profile, identical replay without effect, and 404 for another employee. |
| Deployment | **Not done** (out of scope; no Neon, no production). |
| Real Gemini call | **Not performed.** Only `ScriptedLlm` was used. |

## 11. Known issues found during this work (outside the payslip feature)

- **Intermittent `tests/integration/ai/database/test_conversation_service.py::test_concurrent_duplicate_starts_yield_one_conversation_and_case`**
  (fails roughly 1 in 2 local runs; code identical to `origin/develop`, untouched by this branch).
  Observed error: `ConversationConflictError("client request ID reused with different content")`.
  Likely cause (code analysis; not proven by a fix): `create_conversation` uses `INSERT … ON CONFLICT (id) DO NOTHING`, but
  `thread_id` and `case_operation_id` are also unique. When five identical starts race, a
  waiting insert can fail on one of those other unique indexes instead of the `id` arbiter. The
  resulting `IntegrityError` is reported as `ConversationConflictError`, although the request
  is a legitimate duplicate.
  Suggested fix (AI persistence owner): use `ON CONFLICT DO NOTHING` without a conflict target, or
  on `IntegrityError` re-read by `id` and accept identical content. Not changed here
  (`app/ai/infrastructure/persistence/store.py` is shared AI code).
- Local test runs from `src/backend` with explicit test paths must pass `-c pyproject.toml`;
  otherwise pytest roots at the repository, `asyncio_mode=auto` is not loaded and async tests fail.
- `drive-angular/e2e/visual/home.visual.spec.ts` (homepage vs reference image, ±4px and ≤3% pixel
  checks) fails locally both with and without the payslip changes (verified by stashing them);
  pre-existing, unrelated to this feature.
