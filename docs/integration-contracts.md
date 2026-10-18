# Strauss Vehicle Advisor — Shared Integration Contracts

**Version:** 1.0 — 2026-10-15  
**Status:** Implementation coordination baseline. Concrete DTOs not explicitly settled by existing specifications are marked **PROPOSED — approval required**.

## 1. Authority and precedence

1. Challenge JSON/workbook/CSV/PDF govern source business facts, with documented source discrepancies.
2. `agent-spec.md` v1.4 governs approved conversation/business behaviors and canonical agent tools.
3. `architecture.md` (corrected architecture) governs Python backend modules, business HTTP API, persistence and integration.
4. This document resolves the **implementation interface between parallel teams** without overriding 1–3.
5. The Angular plan is subordinate to the above. Do not incorporate its incompatible "Amendment A" as-is.

When source ambiguity remains, document it and ask; do not silently implement a new rule. The reference value of the same business field must agree across server, agent and UI.

## 1.1. Fixed module ownership and route dispatch

**Canonical module boundary and shared API (approved architecture):** The Python backend has exactly **two functional modules**: **Business System** (the four layers **Application**, **Business Logic (BL)**, **Domain / Core**, and **Infrastructure**) and **AI Engine** (LangGraph conversation/workflow, agent tools, LLM adapter, persisted conversation state and representative-answer continuation). **One shared FastAPI boundary sits outside both modules**, authenticates and validates incoming Angular requests, and dispatches them by route: business, catalogue, calculation and representative endpoints to Business Application; chat, conversations and streaming endpoints to the AI workflow entry point. Internal agent-to-business calls go **only through the Business Application services**, never directly to BL, Core functions, repositories, ORM or PostgreSQL. The API is a transport boundary, not a third business module. API/worker/bootstrap are runtime entry points and wiring, not extra functional modules. The existing **Application–Infrastructure dependency-inversion/ports-and-adapters design remains unchanged**; this clarification does not create mutual direct imports or alter other dependency rules.

## 2. Parallel work split

| Ownership | Agent developer | Services/Tools developer | Joint ownership |
| --- | --- | --- | --- |
| Code | `app/agent/**`, prompts, Hebrew evaluations, mock-backed tool integration | `app/application/**`, `app/business/**`, `app/domain/**`, `app/infrastructure/**`, DB migrations, FastAPI business routes | Canonical DTO contracts, agent-tool adapter, composition/root, end-to-end integration |
| Test | Agent workflow against fake service port; confirmation and escalation | Payroll/eligibility/repository/reference tests; API contract tests | Cross-boundary contract tests, concurrency, representative/resume and E2E |
| Rules | Never compute or authorize in the LLM | Sole calculation/readiness/eligibility authority | Changes require both developers' review |

The agent developer can start with type-compatible fake responses. The tools developer can start without an LLM. Work in feature branches; no unreviewed changes to shared schema files.

## 3. Canonical domain model

- `employeeId` / `ownerSubjectId` come **only** from authenticated server context, not from model proposals.
- A conversation is owned by one employee. Its business `caseId` is bound to its conversation; one case stores conversation-local non-salary facts, request, alternatives and hypothetical overrides.
- `business.employee_profiles`: six actual salary fields, plus `occupationId`, `entitlementGroup`, `qualifyingChildrenCount`, and actual last-reported `currentArrangement` (including vehicle, participation, family deduction or allowance as applicable): `baseGross`, `commissionsGross`, `bonusGross`, `pensionEmployeeRate`, `studyFundEmployeeRate`, `creditPoints`. Values have status, source, source message and revision metadata. One latest version per employee.
- Occupation, entitlement group, qualifying children and reported current arrangement are **shared actual employee profile fields**. Only hypothetical overrides, alternatives, questions, messages, and calculations remain conversation-specific.
- New conversations initialize relevant working actual employee facts from the latest employee profile. Older conversations retain old messages/calculation snapshots; before a **new regular calculation** actual salary inputs must synchronize to the latest profile with optimistic version checks. Continuing a hypothetical preserves its saved baseline and overrides unless the employee explicitly changes it.
- No separate mandatory salary-change-history table, audit-ledger table, or 30-day conversation purge in the approved initial demo. Persist all visible conversation messages and immutable calculation input/output snapshots. A future pilot requires a separately decided real-data retention policy.
- Document uploads/extraction are future-only, not included in initial frontend/backend acceptance.

### Field state and intent are different things

Canonical collected-field state from agent specification: `missing`, `needs_clarification`, `usable`. Source: `employee`, `catalogue`, `rules`, `tax_dataset`, `representative` (future `document_confirmed`). Actual vs hypothetical is the *write mode/scenario classification*, not a substitute for field status. Do not silently replace field states with `known/estimated/unknown`, or add `assumptionAccepted` to the production contracts unless jointly approved in a subsequent version.

Clear employee-reported monthly values can be `usable` without employer verification. An explicitly tentative statement may require clarification under existing readiness rules; whether to support a separate `estimated` status is **PROPOSED — approval required**, not part of v1.0.

## 4. Canonical Python agent-tool API

Use **these** externally exposed names from Agent Spec §7. Short architecture names (e.g., `update_case`, `calculate_comparisons`, `check_readiness`) are service method aliases only, not an additional model-facing tool namespace.

| Canonical tool | Essential inputs | Essential outputs |
| --- | --- | --- |
| `get_conversation_context` | Server-bound conversation identity | State, current/profile revisions, mismatch, active request, history/result freshness |
| `update_conversation_inputs` | Allowlisted fields, source message, expected case/profile versions, `mode=actual_update\|hypothetical` | Accepted/ambiguous fields, new revisions, stale results |
| `list_eligible_options` | Conversation state, scope/filters | `resultSetId`, eligible IDs and reasons, unchecked tracks, `scopeComplete` |
| `resolve_vehicle` | Vehicle identifiers, purpose | resolved / ambiguous / not_found; candidates |
| `get_vehicle_tax_value` | Verified complete tax key or explicit mapped catalogue ID | Amount, complete key, source/dataset version |
| `check_eligibility` | Revision, resolved alternative | eligible / ineligible / needs_information, reasons |
| `check_calculation_readiness` | Active request, state revision | Ready, missing/ambiguous, representative pending, blocked alternatives, baseline readiness |
| `compare_scenarios` | Result set or selected IDs, baseline mode, expected revision | Optional baseline and per-alternative result/status, versions and issues |
| `create_representative_question` | Field/alternative/request/question/idempotency key | Question ID and saved status |
| `get_comparison_details` | Authorized calculation ID | Immutable snapshot, kind, freshness, alternatives |
| `get_conversation_messages` (optional) | Authorized bounded range/anchor | Visible messages |

Every tool call verifies authorization and revisions in Application. Agent gets typed contracts and injected actor/conversation context; never direct SQL/ORM access. A pending alternative must not block unrelated ready alternatives. Never return zero for unknown or failed money.

## 5. HTTP boundary: Python to Angular

`/api/v1/cases` and comparison/vehicle/representative endpoints in corrected Architecture §9 are the HTTP basis. Python service and agent tools are **not** called through internal HTTP. Angular uses HTTP/SSE only, never calculation formulas. Server is authoritative for eligibility, vehicle benefit, net, delta and statuses.

### Mutating business case — approved minimal request shape

```json
{
  "expectedCaseVersion": 3,
  "expectedProfileVersion": 5,
  "mode": "actual_update",
  "operationId": "11111111-1111-4111-8111-111111111111",
  "changes": [
    {"op": "set", "field": "payroll.baseGross", "value": "18500.00"},
    {"op": "set", "field": "payroll.commissionsGross", "value": "0.00"}
  ]
}
```

- `mode`: one of `actual_update`, `hypothetical`. An ambiguous user statement requires clarification; don't default to actual. Mode is **per operation** in the agreed architecture; for a message that mixes actual updates and hypotheticals, use distinct version-checked operations (or clarify). Do not implement a per-field `scope` as an unapproved replacement.
- Supported initial `changes`: restricted `set`/`clear` only. `set.value` must not be null. `clear` establishes missing, **never zero**. Only approved actual employee-profile fields (six salary fields plus occupation, group, qualifying children and reported current arrangement) may update the cross-conversation profile. Hypothetical values always remain case-local.
- Actual salary changes update case and salary profile atomically with expected case **and profile** versions; stale revisions => 409; reload/reconcile instead of last-write-wins.
- Hypotheticals only alter scenario state. Historical calculations retain immutable snapshots.
- `confirm`, `reset`, `acceptAssumption`, per-field status/scope, and profile deletion routes from the earlier Angular plan are **not approved v1 operations**. The expanded actual employee profile IS approved; context-specific confirmation must be represented in agreed server DTOs without requiring a separate `confirm` PATCH operation. Propose them separately with full Python/TypeScript contract changes if needed.
- Currency values use decimal **strings** (e.g. `"18500.00"`); UUID IDs as strings; preserve zero-padded official vehicle codes.

### Read, result and readiness semantics

- New case: latest actual salary copied. Read of reopened case reports latest profile version and mismatch, plus preserved historical results.
- New regular calculation: synchronize to latest profile first; return 409 or explicit freshness error if concurrent update wins. Historical result retrieval never recalc.
- Readiness is goal/scope-specific: catalogue browsing needs group but not salary, alternative net does not need current baseline, delta does. Return missing, ambiguous, representative pending and blocked items distinctly.
- Each comparison returns `netRaw`, `netDisplay`, `deltaRaw`, `deltaDisplay`, breakdown, calculation/rules/dataset versions and per-alternative status. `deltaRaw` is difference of exact nets and `deltaDisplay` rounds **that difference**; never subtract rounded display nets.
- Keep API errors machine readable (`status`, `code`, `traceId`, safe details). Version mismatch = HTTP 409; unauthorized resource access must not reveal foreign data.
- Requests are idempotent by operation ID + payload, including committed/in-progress outcomes.

**PROPOSED — approval required before Angular implementation:** exact JSON naming/layout of complete case DTO, readiness DTO, comparison DTO, SSE envelope, `GET /api/v1/me/salary-profile` view, and an explicit way to set actual vs hypothetical in direct manual UI edits. Produce a single OpenAPI/Pydantic contract and generate TypeScript types from it instead of hand-authoring divergent schemas.

## 6. Critical business invariants shared by both teams

- Group may select same or lower group only. Family track requires >= 4 children who have **not yet turned 24** (age 23 included); gap participation is `0.5 * max(0, cost - group budget)` in addition to ordinary participation.
- Electric reimbursement is **250 ILS net** after gross-up and is excluded from pension/study contribution base. Actual vs hypothetical and baseline use the same policy.
- Only the complete five-part official tax key (or validated explicit catalogue mapping) can determine benefit. No commercial-name-only automatic selection.
- Demo salary uses configured 2025 tables and 2026 vehicle dataset; never silently refresh to current payroll law.
- E-003 delta display `-1939` and E-006 `+722` per precise-difference rounding; document that workbook displays `-1938` and `+721` respectively. Test six employee reference cases including this exception.

## 7. Contract-first work gate

Before either branch connects to real endpoints, commit and jointly review:

1. This agreement plus corrected architecture and agent specification as read-only business baselines.
2. `app/contracts/` Pydantic input/output models for the ten canonical model-facing tool contracts, plus HTTP DTOs; use stable enumerations/errors.
3. Generated OpenAPI and TypeScript clients/types in Angular; contract fixtures with at least one success, missing, blocked, stale and unauthorized case.
4. Fake in-process Python services for agent tests and a dev-only HTTP mock for Angular; both must be generated from/matched against the **same** schema.
5. Automated contract tests, reference calculation tests and a minimal full-flow smoke test. CI must reject API/schema drift.

Do not let either developer autonomously edit a shared contract. Changes require both developers' approval and a versioned migration/compatibility decision.

## 8. Remaining approvals — do not silently decide

- Exact HTTP JSON DTOs and SSE event payload fields (names and shapes).
- Whether tentative salary estimates deserve distinct status/acceptance semantics beyond `needs_clarification`.
- Whether employee can explicitly delete saved salary profile fields via UI; if so define authorization, concurrency, and impact on existing cases.
- Whether any additional real-world payroll deductions are in initial demo scope (the current agreed initial scope says no).
- Actual/SSE/session authentication mechanism and production deployment specifics, where not already fixed by Architecture.

These are contract-design questions, not permission to override earlier approved business behavior.


## Approved implementation update — 2026-10-15: contextual employee profile and non-blocking workflows

**Decision authority:** This section supersedes conflicting older language in this document (in particular references to a salary-*only* shared profile, compulsory blanket verification, or globally paused conversations). It does not supersede original challenge monetary/eligibility rules or previous calculation rounding decisions. Apply it consistently to Agent Spec, Architecture, Integration Contracts and Frontend Handoff.

### Shared EmployeeProfile, not a compulsory intake questionnaire

- Maintain **one employee-owned, versioned actual EmployeeProfile** containing `baseGross`, `commissionsGross`, `bonusGross`, `pensionEmployeeRate`, `studyFundEmployeeRate`, `creditPoints`, `occupationId`, `entitlementGroup`, `qualifyingChildrenCount`, and `currentArrangement` with applicable real vehicle/key, reported participation, family deduction/overlap flag, or allowance. These are the **last employee-reported** facts with per-field `source`, `status`, `updatedAt`, and `lastConfirmedAt` as appropriate; they are not silently promoted to verified company data. Keep identifier resolution and eligibility checks in deterministic server code.
- Each new conversation loads/display these actual facts in a side panel and starts a separate case. **Do not demand global profile approval at conversation start.** Agent asks or reconfirms *only* facts necessary for the user's current goal when missing, ambiguous, stale in a materially relevant way, contradictory, or otherwise unsafe to assume. A non-relevant outdated field must not block an unrelated request. Avoid asking again for an unambiguously confirmed fact in the current context.
- For a request to browse regular vehicles, group may be relevant; payroll/current vehicle/children usually are not. For calculating a new alternative's net, current arrangement is not required. For a delta versus today, confirm the reported current arrangement and applicable participation if they might be outdated. Qualifying child count must be checked when family-track eligibility is actually relevant and its dated meaning uncertain. A saved count does not guarantee age eligibility at a later calculation date.
- Clear **real-life corrections** update only specified actual profile fields and current case atomically with `expectedCaseVersion` and `expectedProfileVersion`; conflict returns a controlled reconciliation outcome, **never last-write-wins**. Explicit hypothetical / “what if” changes affect only the case/scenario. Ambiguous real-versus-hypothetical intent requires a clarification before writing. Do not update a profile just because a user selected a vehicle to compare.
- Historical visible messages, conversation state, representatives' requests/answers and immutable calculation snapshots remain separate by conversation; previous computations are not rewritten. On resuming a conversation, fetch latest profile version and reconcile applicable actual facts before a *new regular calculation*; preserve hypothetical scenario snapshots. The side panel presents actual profile values, distinctly from scenario overrides. No automatic 30-day demo purge.

### Independent representative requests; conversation always usable

- Representative request status (e.g. `open`, `answered`, `cannot_determine`, `cancelled`) is **not** the conversation status. Creating a request does not globally pause chat intake or graph processing. Only the affected fact/alternative/delta becomes pending. Complete other ready calculations, show partial results when possible, and explain naturally what remains unanswered. If nothing calculable is ready, acknowledge the pending representative request and invite the employee to continue discussing other choices.
- Persist each request with `requestId`, original conversation/case/request/alternative references, requested fact and versions, so that replies can be applied only if still relevant. On representative answer: validate ownership, applicability and versions; persist it; compute the newly ready part; surface an asynchronous chat/UI update. **Do not switch the active user goal back** if the conversation has advanced. History and all later messages must remain intact. Any graph interrupt/checkpoint is local to the pending subtask, not a lock on the conversation.

### Human-friendly errors and safe server codes

- The server keeps structured HTTP status and stable business `code`, with safe details/trace ID for diagnostics. **Never show raw HTTP codes (`404`, `409`, `500`), stack traces, or exception payloads in the employee-facing chat/UI.** Render a contextual, natural Hebrew message based on the business code plus request context; predictable failures can be mapped by Angular without an LLM call. E.g. unavailable vehicle: “לא מצאתי את הרכב המבוקש, אפשר לבדוק את הדגם או השנה”; stale revision: “הנתונים עודכנו בינתיים. אטען אותם מחדש לפני השמירה”; temporary failure: “יש תקלה זמנית, אפשר לנסות שוב”. Do not silently retry unsafe writes; do not expose existence of another employee's resource.

### Mandatory acceptance checks

1. New conversation displays actual profile but listing vehicles **does not** ask to revalidate salary, current car, or children if not relevant.
2. New alternative-only estimate proceeds without verifying current car; request for delta checks current baseline as needed.
3. Explicit actual change persists across new conversations; “what if” does not; ambiguous phrasing asks before updating. Profile write race produces version conflict, never overwrite.
4. Stored current car is described as **last reported**, and stale/unconfirmed current details cannot silently ground a precise delta.
5. Pending representative response does not prevent new messages/new tasks; ready alternatives appear immediately, and later answer updates the correct calculation without hijacking the current topic.
6. All expected failures become natural Hebrew messages; no raw HTTP status is visible in the UI.
7. Historical messages, snapshots and previous results remain unchanged after real profile updates and delayed answers.


## Approved implementation update — 2026-10-18: backend package layout

**Decision authority:** project owner, 2026-10-18. Package paths only; ownership, tool names, contracts and business behavior are unchanged.

The ownership paths in Section 2 map to the repository layout as follows: agent developer `app/agent/**` → `app/ai/**`; services/tools developer `app/application/**`, `app/business/**`, `app/domain/**`, `app/infrastructure/**` → `app/business/**` (with layers `application/`, `bl/`, `domain/`, `infrastructure/`); joint ownership of canonical DTO contracts → `app/contracts/**`, plus `app/api/**`, `app/bootstrap.py`, `app/main.py`, `app/settings.py` and `app/runtime.py`. The AI module does not import `app.business`; agent tools receive the business tool port (`app.contracts.business_port.BusinessToolsPort`) through dependency injection. See `architecture.md`, "Approved implementation update — 2026-10-18: backend package layout".


## Approved implementation update — 2026-10-18: D1 module isolation (contracts C1–C4)

**Decision authority:** project owner, 2026-10-18. Replaces the shared-transaction assumptions of this document and of `architecture.md` §3, §18, §19 and §21 (see `architecture.md`, "Approved implementation update — 2026-10-18: D1 module isolation and consistency model").

- Business and AI have separate least-privilege database identities and their own Units of Work; no transaction handle, session or credential is shared or exposed to model-controlled code.
- `CaseLifecyclePort.create_case_for_conversation(actor, command) -> Envelope[CreateCaseResult]` (C1): Business owns and commits its transaction; idempotent by actor, conversation and operation ID; recoverable coordination instead of atomic creation.
- Business `UnitOfWork` carries no AI input queue (C2); `DurableInputQueue` is AI-internal and provisional (C3).
- `BusinessEventFeed` / `BusinessEvent` (C4) are provisional shared contracts for at-least-once delivery from the Business Outbox; not implemented yet.
- Still provisional until the AI developer starts: C3/C4 shapes, AI conversation statuses, HTTP DTOs and SSE.
