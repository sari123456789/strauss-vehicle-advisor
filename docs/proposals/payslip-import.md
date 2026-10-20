# Proposal and integration handoff: AI-assisted payslip PDF import

**Status:** PROPOSAL. Implemented independently on `feature/payslip-ai-import`; not wired into the
application. Nothing here is an approved contract until the shared-ownership owners sign off.
**Audience:** the full-system integration developer, the Business developer and the frontend developer.

## 1. Decisions that need sign-off

| # | Topic | Current authoritative text | Proposal |
|---|---|---|---|
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
| Standalone HTTP router | `src/backend/app/api/routers/payslip_imports.py` (not included by `app.main`) |
| Tests | `tests/unit/ai/payslip/`, `tests/api/test_payslip_imports_api.py`, `tests/integration/ai_business/test_payslip_import_real_business.py` (marker `db`) |
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

## 8. Integration handoff (shared files — not changed on this branch)

1. **`app/bootstrap.py` / lifespan** — build and store the service:
   ```python
   app.state.ai_payslip_imports = PayslipImportService(
       reader=PypdfTextReader(),
       store=InMemoryDraftStore(),          # single process only (§5)
       conversations=conversation_service,  # the existing ConversationService
       business=business_services.tools,    # BusinessToolsPort
       clock=<existing UTC clock>,
       llm=<existing QuotaGuardedLlm or None>,
       config=PayslipImportConfig(external_llm_enabled=<setting, default False>),
   )
   ```
   The setting must be read in `app.settings` / `app.ai.infrastructure.llm.settings` (only these
   may read the environment).
2. **`app/main.py`** — `app.include_router(payslip_imports.router, prefix="/api/v1")`.
3. **OpenAPI / TypeScript** — regenerate models from the DTOs after joint approval.
4. **Docs** — update `architecture.md` §1/§19, `agent-spec.md` T66 and exclusions,
   `integration-contracts.md` (endpoints, codes), `frontend-handoff.md` (§9 below).
5. **Agent (optional, later)** — after approval the agent sees the new values through the normal
   `get_conversation_context`; no workflow change is required. A follow-up could post a system
   message "הפרופיל עודכן מתלוש השכר" to the conversation history.

## 9. Frontend handoff (Angular; standalone component, no redesign)

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
| Independent feature implementation | Done on `feature/payslip-ai-import`: unit, router and real-Business integration tests pass locally. |
| API/frontend integration | **Not done** (needs §8 and the Angular component). |
| End-to-end deployment | **Not done / not verified.** |
| Real Gemini call | **Not performed.** Only `ScriptedLlm` was used. |
