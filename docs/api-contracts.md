# Shared Business Contracts — PROVISIONAL

**Contract version:** `0.1.0` · **Status:** `provisional` — proposed by the Business developer in Step 0
(2026-10-15). **Not jointly approved.** The AI developer has not started yet; every item below requires a
joint integration review before either side treats it as final (Integration Contracts §7–§8).

Authority order is unchanged: challenge sources → `agent-spec.md` → `architecture.md` →
`integration-contracts.md`. This file documents the proposed implementation interface only and does not
override any of them. Do not change a shared contract unilaterally.

## 1. Where the contracts live

| Item | Path | Owner |
|---|---|---|
| Pydantic contracts (tools + future HTTP DTOs) | `src/backend/app/contracts/` | Shared |
| Tool Protocol | `app/contracts/business_port.py` → `BusinessToolsPort` | Shared |
| Case-creation Protocol (Business owns its transaction, C1) | `app/contracts/case_lifecycle.py` → `CaseLifecyclePort` | Shared |
| Business event feed (provisional, C4) | `app/contracts/business_events.py` → `BusinessEventFeed`, `BusinessEvent` | Shared |
| Business Unit of Work port | `app/business/application/ports/unit_of_work.py` | Business |
| Durable input queue (provisional, C3) | `app/ai/application/ports/input_queue.py` → `DurableInputQueue` | AI |
| Fixture-backed fake | `tests/fakes/fake_business_port.py` + `tests/fakes/fixtures/*.json` | Shared |
| In-memory AI-side queue fakes | `tests/fakes/ai/fake_unit_of_work.py`, `fake_input_queue.py` | AI |
| Exported JSON Schemas (for Angular, until full OpenAPI) | `contracts/schemas/*.schema.json` | Generated |

Regenerate schemas with `uv run python -m app.cli.export_contract_schemas`; the test suite fails on drift.
`app/contracts` depends only on `pydantic` and the standard library (enforced by
`tests/architecture/test_import_boundaries.py`).

## 2. Conventions

- JSON field names are camelCase (Python attributes are snake_case via alias generation).
- Unknown fields are rejected (`extra="forbid"`); models are immutable.
- Money is a **decimal string** (`"18500.00"`); JSON numbers are rejected, so binary floats never enter.
  Raw results keep full `Decimal` precision (D10); `display` is a whole-shekel string rounded half away
  from zero.
- Rates are decimal strings in 0–1 (`"0.06"` = 6%). Credit points are non-negative decimal strings.
- Official vehicle codes are strings that keep leading zeros (`"01014"`, `"00003"`).
- Unknown is `value: null, status: "missing"` — never zero.
- Option keys are deterministic: `vehicle:CAR-10` or `waiver`.
- `ToolContext` (actor, conversationId, caseId, traceId) is injected by server code; it is never a
  model-selected argument.

## 3. Response envelope

Every tool returns `Envelope[T]`:

| Field | Meaning |
|---|---|
| `status` | `ok` · `partial` (some items blocked/pending) · `needs_information` · `error` |
| `data` | Result payload; must be `null` when `status=error` |
| `issues[]` | `{code, field?, optionKey?, requestId?, reason?}` |
| `error` | `{code, traceId, safeDetails}`; only with `status=error` |
| `caseVersion`, `profileVersion` | Versions after the operation |
| `sourceVersions` | `rulesSnapshotId`, `catalogueVersionId`, `taxDatasetVersionId` |
| `traceId` | Correlation ID |

Expected business outcomes (missing data, ineligibility, version conflict, not found) are **returned**,
not raised. Technical failure is never represented as zero money.

**Issue codes** (Agent Spec §7): `MISSING_INPUT`, `AMBIGUOUS_INPUT`, `INELIGIBLE`, `VEHICLE_NOT_FOUND`,
`TAX_KEY_AMBIGUOUS`, `SOURCE_CONFLICT`, `STALE_REVISION`, `PENDING_REPRESENTATIVE`, `TOOL_UNAVAILABLE`,
`UNSUPPORTED_INPUT`.

**Error codes** (proposed): `MALFORMED_REQUEST` (400), `UNAUTHENTICATED` (401), `FORBIDDEN` (403),
`NOT_FOUND` (404; also used for foreign resources — no existence disclosure), `VERSION_CONFLICT` (409),
`OPERATION_PAYLOAD_MISMATCH` (409), `INVALID_STATE_TRANSITION` (409), `INVALID_BUSINESS_INPUT` (422),
`INPUT_OUT_OF_SUPPORTED_RANGE` (422), `GROSS_UP_NOT_CONVERGED`, `RATE_LIMITED` (429),
`INFRA_UNAVAILABLE` (503). HTTP mapping applies only at the API boundary; employees always see natural
Hebrew messages, never codes.

## 4. Canonical tools (`BusinessToolsPort`)

| Tool | Request | Result | Key rules |
|---|---|---|---|
| `get_conversation_context` | — | `CaseContext` | Latest actual profile, `salaryProfileVersionUsed`, `latestProfileVersion`, `profileMismatch`, hypothetical overrides, alternatives, open requests, recent runs with freshness, `fieldsRequiringConfirmation` (G4: informational, case-relative; ask only when the current goal needs the fact; no time-based expiry). Business part only; messages are AI-owned. |
| `update_conversation_inputs` | `UpdateInputsRequest` | `UpdateInputsResult` | `set`/`clear` only on allowlisted `FieldPath`s; `actual_update` requires `expectedCaseVersion` **and** `expectedProfileVersion` and updates profile + case atomically; `hypothetical` touches the case only; `set null` invalid; one field at most once; whole list rejected on any invalid value. |
| `list_eligible_options` | `ListOptionsRequest` | `ListOptionsResult` | Categories default `regular`+`family`; waiver only when requested; persisted `resultSetId`; ordered items with eligibility + reasons; `uncheckedCategories`, `scopeComplete`. |
| `resolve_vehicle` | `ResolveVehicleRequest` | `ResolveVehicleResult` | Exactly one of `catalogueId`/`taxKey`/`query`; `resolved`/`ambiguous`/`not_found`; never auto-selects. |
| `get_vehicle_tax_value` | `TaxValueRequest` | `TaxValueResult` | Complete five-part key or mapped catalogue ID only. |
| `check_eligibility` | `EligibilityRequest` | `EligibilityResult` | `eligible`/`ineligible`/`needs_information` with reasons. |
| `check_calculation_readiness` | `ReadinessRequest` | `ReadinessResult` | Goal-specific: `list_regular`, `list_family`, `waiver_eligibility`, `calculate_net`, `compare_to_current`. Output names follow Agent Spec §6. |
| `compare_scenarios` | `CompareRequest` | `ComparisonResult` | Selection `explicit`(optionKeys)/`result_set`/`all_eligible`; `kind=regular` requires `expectedProfileVersion` (synchronizes to latest profile); per-scenario status; partial results; `delta` null without baseline. |
| `create_representative_question` | `CreateQuestionRequest` | `CreateQuestionResult` | Allowlisted `fieldCode`; idempotent by key; blocks only the dependent option. |
| `get_comparison_details` | `ComparisonDetailsRequest` | `ComparisonDetails` | Immutable snapshot; freshness evaluated on read; never recalculates. |
| `get_representative_request` | `RepresentativeRequestLookup` | `RequestStatusView` | G1: one request of the server-bound case only; foreign/invented IDs are `NOT_FOUND`; read-only; `answer` empty until the answer stage. |
| `resolve_occupation` | `ResolveOccupationRequest` | `ResolveOccupationResult` | G2: `resolved` only for an exact official ID or exact normalized official name (either written form of "X/ת"); otherwise `ambiguous` candidates or `not_found`; no synonyms, no invented IDs; never writes the profile. |
| `confirm_profile_fields` | `ConfirmProfileFieldsRequest` | `ConfirmProfileFieldsResult` | G3: confirms saved usable actual values (sets `lastConfirmedAt`, revision + 1; values unchanged); missing/unclear fields in `notConfirmable`; `expectedCaseVersion` + `expectedProfileVersion`; idempotent by `operationId`; profile + case atomically. |

`get_conversation_messages` (optional) is owned by the AI Engine and is not part of this port.

### Updatable fields (`FieldPath`)

`payroll.baseGross`, `payroll.commissionsGross`, `payroll.bonusGross`, `payroll.pensionEmployeeRate`,
`payroll.studyFundEmployeeRate`, `payroll.creditPoints`, `employee.occupationId`,
`employee.entitlementGroup`, `employee.qualifyingChildrenCount`, `currentArrangement.kind`,
`currentArrangement.vehicle`, `currentArrangement.propulsion`, `currentArrangement.participation`,
`currentArrangement.familyDeduction`, `currentArrangement.familyDeductionIncludedInParticipation`,
`currentArrangement.allowance`.

Not approved v1 change operations in `update_conversation_inputs` (rejected by the contract): `confirm`,
`reset`, `acceptAssumption`, per-field `scope`. Confirmation is the separate `confirm_profile_fields` tool (G3).

`PendingRepresentativeField.fieldCode` is the `RepresentativeFieldCode` enum (G5).

Step 4A result semantics (resolution, listing, dedupe): see `docs/business-decisions.md` §15.

## 5. Name mapping (D5)

| Older / alternative name | Canonical name |
|---|---|
| `eligibleChildrenCount` (Agent Spec §5) | `qualifyingChildrenCount` |
| `expectedRevision`, `stateRevision` | `expectedCaseVersion`, `caseVersion` |
| `questionId` | `requestId` |
| `calculationId`, table `calculations` | `runId`, table `business.comparison_runs` |
| `update_case` / `calculate_comparisons` / `check_readiness` / `get_case_context` | aliases of `update_conversation_inputs` / `compare_scenarios` / `check_calculation_readiness` / `get_conversation_context` (business part) |
| `alternativeIds` with `waiver` | `optionKeys` (`vehicle:CAR-xx`, `waiver`) resolved to persisted alternative IDs |
| `strauss-architecture-en*.md`, `strauss-agent-spec*.md`, `INTEGRATION_CONTRACTS.md`, `CLAUDE_FRONTEND_HANDOFF*.md` (references inside documents) | `docs/architecture.md`, `docs/agent-spec.md`, `docs/integration-contracts.md`, `docs/frontend-handoff.md` |

## 6. Transaction boundaries (decision D1, contracts C1-C4)

No database transaction or session is shared between modules. Each module owns its own Unit of
Work and database identity (separate least-privilege logins; provisioning is deferred, D8).
Cross-module consistency is eventual and recoverable; nothing is described as cross-module atomic.

**Conversation + case creation (AI-coordinated, recoverable):**
1. AI records its conversation as `initializing` (AI transaction).
2. AI calls `CaseLifecyclePort.create_case_for_conversation(actor, CreateCaseCommand)`. Business
   authorizes the revalidated actor, creates the case (copy of the latest actual profile and
   `salaryProfileVersionUsed`) and **commits its own transaction**; it returns `Envelope[CreateCaseResult]`.
   Idempotent by (actor, conversationId, operationId): a retry returns the same case (`created=false`);
   a changed payload is `OPERATION_PAYLOAD_MISMATCH`; another owner's conversation is `NOT_FOUND`.
3. AI activates its conversation with the case ID (AI transaction).
Failure windows: after 1 (no case) or after 2 (case, conversation still `initializing`) the AI
worker repeats step 2 with the same operation ID and continues. A case without a conversation
remains only if the AI never retries; it has no access path. Recovery is to be proven by tests
when the AI module is built.

**Representative answer (implemented: B3a + M3):** one Business transaction stores the
authorization and state checks, the answer, the case mutation, version increments, the audit entry
and a `business.integration_outbox` row. The AI `BusinessEventRelay`
(`app/ai/application/event_relay.py`) pulls events through `BusinessEventFeed`, which only it
receives. It enqueues each event into the AI's own `DurableInputQueue`, deduplicated by `eventId`,
and acknowledges an event only after its enqueue committed. Delivery is at-least-once and
consumption idempotent.

- **E1 (approved 2026-10-20):** `pending(limit, *, after_sequence=None)`.
  - The relay pages past events it cannot accept yet, so they never block later events.
  - The cursor is valid only within one scan. Each scan restarts from `None`, because sequences
    may become visible out of order.
- **Failure handling:**
  - Permanent per-event failures (no bound conversation, or a payload mismatch) are never
    acknowledged. They back off per event (30 s, 2 min, 10 min, then 1 h) and are quarantined
    (hourly retries, ERROR log) after 10 attempts. They stay recoverable in the outbox.
  - Transient failures back off globally (1 s, 5 s, 30 s, 2 min, then 5 min).
- **Two stages:** delivery retries (relay, outbox) are separate from turn-processing retries
  (AI `inputs`: retry, dead, needs_reconciliation).
- **Runtime:** the relay runs inside the API process when `STRAUSS_WORKER_IN_PROCESS=true`, on
  one instance only. Its counters appear under `/api/v1/health/ready` `info.relay`.
- **Diagnosis:** to list undelivered events, run
  `SELECT id, sequence, created_at FROM business.integration_outbox WHERE acknowledged_at IS NULL ORDER BY sequence`.

`DurableInputQueue` semantics (AI-owned, provisional): same `eventId` + same payload → `duplicate`;
same `eventId` + different payload → `InputPayloadMismatchError`.

## 7. Using the fake (AI developer)

```python
from tests.fakes.fake_business_port import FakeBusinessPort
port = FakeBusinessPort("blocked")   # success | missing | blocked | stale | unauthorized
envelope = await port.compare_scenarios(ctx, request)
port.calls  # recorded (tool, ctx, request) tuples
```

The fake performs no calculation or eligibility logic. Fixture amounts are based on workbook employee
E-001 (fictional) and the independently reproduced reference values — they are not engine output. All
fixtures are validated against the same Pydantic models at construction and by `tests/contract/`.

## 8. Open items for joint review

- Envelope status set and error-code list.
- `OptionKey` format and `Selection` shape; persisted `resultSetId` semantics.
- `CaseContext` composition with AI-owned conversation history.
- Representative `fieldCode` allowlist and `expectedValueType`.
- `ComparisonDetails.inputSnapshot` typed shape (finalized with persistence).
- HTTP DTO naming for Angular and the SSE envelope (Integration Contracts §8).

## 9. Representative HTTP routes (B3b, full-system integration)

Implemented in `app/api/business/routers/representatives.py` and exported to
`contracts/openapi/business-api.json`. Paths follow architecture §9. The bodies and views are the
existing shared contract models; there are no new DTOs. Every route needs a bearer token of an
active `representative`. Other roles get `403 FORBIDDEN` and anonymous callers get
`401 UNAUTHENTICATED`, both as Problem Details.

| Method and path | Body / query | Response |
|---|---|---|
| `GET /api/v1/representative-requests` | `status` (repeatable, default `open`), `limit` 1–200 | `Envelope[list[RepresentativeRequestView]]`, oldest first |
| `GET /api/v1/representative-requests/{requestId}` | — | `Envelope[RepresentativeRequestView]` |
| `POST /api/v1/representative-requests/{requestId}/answers` | `AnswerSubmit` | `Envelope[AnswerView]` |

**Answer behavior:**
- Every response is `200`. A replay with the same `operationId` and payload returns the stored answer.
- A different payload under the same `operationId` is `409 OPERATION_PAYLOAD_MISMATCH`.
- A second answer to an already answered request is `409 INVALID_STATE_TRANSITION`.
- A stale `expectedRequestVersion` is `409 VERSION_CONFLICT`.
- An invalid value is `422 INVALID_BUSINESS_INPUT`.
- A committed answer writes its `representative.answered` outbox event in the same transaction.

**Privacy:** the views never contain the employee's identity, salary, profile, case ID or conversation ID.
