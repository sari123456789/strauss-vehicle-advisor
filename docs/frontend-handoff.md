# Claude Code — Angular plan reconciliation / instructions

**Status:** Updated replacement for the incompatible contract-design Step 0 of `Pasted markdown(2).md`. Do not treat this document as permission to start all implementation automatically.

## Keep from the proposed plan
- Remove hard-coded vehicles, monetary figures, local-only fake persistence and frontend payroll calculations.
- Use Angular HTML/CSS rather than a clickable image for the homepage; retain brown reference and accessibility/visual verification.
- Use server-derived cases, chat, catalogue, representative, comparison and history screens with empty/loading/error states.
- Use a separate development-only HTTP mock, with automated checks blocking mock data from production.
- Implement contract tests and E2E; support SSE replay/reconnect only according to the agreed server envelope.

## Replace the proposed Amendment A
- Use the approved expanded EmployeeProfile: six actual salary fields plus reported occupation, group, qualifying children, and current arrangement. Do NOT save alternatives or hypotheticals to the shared profile.
- On new conversation, load latest salary profile. On old conversation, preserve snapshots/history; before NEW regular calculation synchronize actual salary with current profile after checking revisions. Do NOT simply leave old actual salary as an indefinite case copy.
- Use `actual_update` vs `hypothetical` mode of canonical server writes, not per-field `scope` until specifically approved; ask when ambiguous.
- Use `missing`, `needs_clarification`, `usable` canonical statuses; do not invent `known/estimated/unknown` as an incompatible second schema.
- Do NOT implement `confirm`, `reset`, `acceptAssumption`, profile CRUD/delete, automatic `last-confirmed-wins`, or audit history table as approved initial requirements.
- Use 409 optimistic conflict control with `expectedProfileVersion` for actual salary updates.
- Do NOT delete conversations/calculations automatically after 30 days in the demo.
- Do NOT implement document upload/extraction in initial UI.
- `deltaDisplay` rounds exact difference; E-003=-1939, E-006=+722.

## Revised Step 0 — mandatory gate
1. Read `agent-spec.md`, `architecture.md`, and `integration-contracts.md`.
2. Ask for approval for **unsettled** DTO field names and SSE payloads.
3. Define Python Pydantic HTTP DTOs and OpenAPI jointly with services developer; generate TypeScript types (do not make TypeScript authoritative).
4. Stub a dev-only mock server conforming to the generated contract.
5. Add contract compatibility checks, then proceed with Angular implementation and visual polish.

## Working arrangement

**Canonical module boundary and shared API (approved architecture):** The Python backend has exactly **two functional modules**: **Business System** (the four layers **Application**, **Business Logic (BL)**, **Domain / Core**, and **Infrastructure**) and **AI Engine** (LangGraph conversation/workflow, agent tools, LLM adapter, persisted conversation state and representative-answer continuation). **One shared FastAPI boundary sits outside both modules**, authenticates and validates incoming Angular requests, and dispatches them by route: business, catalogue, calculation and representative endpoints to Business Application; chat, conversations and streaming endpoints to the AI workflow entry point. Internal agent-to-business calls go **only through the Business Application services**, never directly to BL, Core functions, repositories, ORM or PostgreSQL. The API is a transport boundary, not a third business module. API/worker/bootstrap are runtime entry points and wiring, not extra functional modules. The existing **Application–Infrastructure dependency-inversion/ports-and-adapters design remains unchanged**; this clarification does not create mutual direct imports or alter other dependency rules.

Backend: one Python modular application with shared Application services. The agent developer uses fake in-process tool implementations. Frontend uses separate HTTP mock. The mock must conform to the same schema as Python. No private divergent API invented by Claude Code.

## Paste into Claude Code (English)

Before coding, replace your Step 0/Amendment A with the agreed `integration-contracts.md` and `architecture.md`. Use the approved expanded employee profile and contextual verification only; do not use last-write-wins, do not add a 30-day purge, and do not implement undocumented update operations. Generate TypeScript contracts from jointly approved Python/OpenAPI schemas. First produce a concise contract diff and list unresolved DTO/SSE details for approval. After that, implement Angular against typed contract-compliant mocks, with no fake numeric values in production. Preserve the planned accessible brown homepage and front-end testing approach.


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
