# Angular ↔ API contract reconciliation

**Status:** PROPOSED. This needs joint approval from the services developer and the agent developer, as required by INTEGRATION_CONTRACTS §5 and §7 and the frontend handoff Step 0. It is not a requirement and it does not override the four authoritative documents.
**Date:** 2026-10-15
**Basis:**
- `agent-spec.md` (Agent Spec v1.4)
- `architecture.md` (Architecture v2.2)
- `integration-contracts.md` (Integration Contracts v1.0, "IC")
- `frontend-handoff.md` ("Handoff")

## 1. Contract diff: what was withdrawn from the earlier frontend Step 0

The earlier frontend output had three parts: Amendment A, the hand-written `docs/api-contracts.md`, and `drive-angular/src/app/core/api/contracts/*.ts`. All three are withdrawn. The TypeScript types will be generated from the jointly approved Pydantic/OpenAPI schema (IC §5, §7; Handoff Step 0.3).

| Withdrawn frontend item | Authoritative replacement |
| --- | --- |
| Per-field `scope: actual \| scenario` | Per-operation `mode: actual_update \| hypothetical`. When one message mixes both, use separate operations or clarify (IC §5; Arch §8) |
| Statuses `known` / `estimated` / `unknown` and `assumptionAccepted` | `missing`, `needs_clarification`, `usable` (Agent §5; Arch §6; IC §3) |
| Provenance values `user`, `saved_profile`, … | `source`: `employee`, `catalogue`, `rules`, `tax_dataset`, `representative` (Arch §6) |
| PATCH ops `reset`, `confirm`, `acceptAssumption` | `set` and `clear` only (IC §5; Arch App. C) |
| Last-confirmed-wins profile writes with no `expectedProfileVersion` | `expectedCaseVersion` + `expectedProfileVersion`; a stale version returns 409 and the client reconciles (IC §5; Arch §8) |
| Indefinite case snapshot of the profile, plus `profileUpdatesAvailable`/`reset` | The case is initialized from the latest profile. Before a **new regular calculation**, the server synchronizes actual fields to the latest profile. Historical results stay as snapshots (Arch §7.1; IC §5) |
| Profile delete endpoints and the "my details" delete UI | Not approved; listed as an open question in IC §8 |
| Fields `payroll.baseSalary`, `commissions`, `bonus`, `employeePensionRate`, `employeeStudyFundRate`, `otherNetDeductions` | `baseGross`, `commissionsGross`, `bonusGross`, `pensionEmployeeRate`, `studyFundEmployeeRate`, `creditPoints`. There are no extra deductions in the demo (Arch §6, §7) |
| `currentArrangement.kind = company_car \| maintenance` | `company_car`, `allowance`, `no_car_no_allowance` (Agent §5; Arch §7) |
| `deltaDisplay` = difference of the displayed nets | `deltaDisplay = displayRound(deltaRaw)`, so E-003 is −1939 and E-006 is +722, with the workbook values documented as exceptions (Arch §12; IC §6) |
| Conversation status `waiting_for_representative` | A representative request never pauses the conversation; request status is separate from conversation status (2026-10-15 update) |
| Profile audit/history table; AC35–AC42; 30-day retention | No mandatory history/audit ledger and no automatic purge (IC §3). Acceptance follows the mandatory checks 1–7 and T01–T79 |
| Showing `traceId` and HTTP status in error UI | Show natural Hebrew messages only. Codes and trace IDs are kept for diagnostics and are never shown as raw statuses (2026-10-15 update) |

## 2. Open items — one proposal each, approval required

The items marked **(decision)** carry business meaning and need a product/business decision, not just a choice of shape.

| # | Topic | Conflict or gap (sources) | Proposal |
| --- | --- | --- | --- |
| O1 | Children and current-arrangement field names | Agent §5 uses `eligibleChildrenCount`, `currentVehicle`, `currentParticipation`, `currentFamilyDeduction` and `currentAllowance`. The 2026-10-15 update and Arch §7 use `qualifyingChildrenCount` and a nested `currentArrangement`. | `employee.qualifyingChildrenCount` and `currentArrangement.{kind, vehicle{catalogueId \| taxKey}, participation, familyDeduction, familyDeductionIncludedInParticipation, allowance}` |
| O2 | Per-field metadata on the profile | Arch §7.1 lists `value, status, source, sourceMessageId, updatedAt, revision`. The 2026-10-15 update lists `source, status, updatedAt, lastConfirmedAt`. | Use the union: `value, status, source, sourceMessageId, updatedAt, lastConfirmedAt, revision` |
| O3 | Context-specific reconfirmation with no `confirm` op (decision) | IC §5 requires confirmation to be represented in server DTOs without a `confirm` op. Arch §8 allows a confirmation-only change to increment the version. | An `actual_update` `set` with an unchanged value refreshes `lastConfirmedAt` (needs approval) |
| O4 | Estimates in the actual profile (decision) | Arch §6: an estimate stays `needs_clarification` until it is agreed for a **hypothetical scenario**, and is then `usable` only there. The product owner's 2026-10-15 instruction: an estimate such as average commissions may describe **actual** payroll. IC §8 lists estimate semantics as open. | No proposal; a decision is needed. Until then, the UI treats estimates as Arch §6 describes |
| O5 | Actual vs hypothetical in direct UI edits | IC §5 marks this PROPOSED | Every save from the side panel requires an explicit choice ("My real figure" / "Just to check"), with nothing preselected. Each save sends one PATCH with one `mode` |
| O6 | Profile read endpoint | IC §5 proposes `GET /api/v1/me/salary-profile`, but the profile is no longer salary-only | `GET /api/v1/me/employee-profile` → `{version, fields{…O2}}`, read-only |
| O7 | Case DTO layout | Not settled (IC §5) | `{caseId, conversationId, caseVersion, calculationMonth, referenceDate, salaryProfileVersionUsed, latestProfileVersion, profileMismatch, actual{fields}, hypothetical{overrides}, alternatives[]}`, with actual values and overrides kept separate for the side panel |
| O8 | Readiness DTO | Agent §6 output fields differ from Arch §9's "goal/scope" response | Request: Arch §14 goal + `selection` + `expectedCaseVersion`. Response: Agent §6 fields (`readyScenarioIds, missingEmployeeFields, ambiguousFields, pendingRepresentativeFields, blockedScenarios, baselineReady, scopeComplete, stateRevision`) |
| O9 | Comparison DTO and breakdown names | The formula symbols differ: Agent §8 uses V, K, R; Arch §11 uses U, H, C | Arch §10 fields plus `kind: regular \| hypothetical` and `salaryProfileVersionUsed`. Breakdown lines are named in words, each `{raw, display}`: `earnedGross, allowance, electricGrossUp, vehicleBenefit, taxBase, incomeTax, nationalInsurance, healthInsurance, pension, studyFund, participation, familyGapDeduction`, plus `displayRoundingReconciliation` |
| O10 | SSE envelope and payloads | Arch §19 lists 9 event kinds without payload shapes | Envelope `{eventId, sequence (null for delta), conversationId, type, occurredAt, data}`. `representative.waiting` carries `{requestId, alternativeId, fieldCode}` and does not change the conversation's usability |
| O11 | Conversation list for the history panel | Arch A8 requires resuming a conversation, but there is no list route in Arch §19 | `GET /api/v1/conversations?cursor=&limit=` → the owner's summaries `{conversationId, caseId, title, updatedAt, latestCalculationId}` |
| O12 | Auth routes and SSE authentication | Arch §22 defines the mechanism, but the paths and SSE auth are open (IC §8) | `POST /api/v1/auth/login \| refresh \| logout`, `GET /api/v1/me`. Fetch-based SSE with a Bearer token. CSRF token on refresh |
| O13 | Representative answer fields | Agent §9.2 has `answerId, questionId, value, unit, answeredBy, answeredAt, validityNote/effectiveFrom`. Arch §17 has `outcome, expected request version, effectiveFrom/To, note` | Use the Arch §17 shape plus `unit`. ID naming is `requestId`; `questionId` is an alias in the tool layer only |
| O14 | Stable error/issue code list (needed for the Hebrew message map) | Agent §7 issue codes and Arch §10/§12 codes are separate lists | One enum that unions both lists, published in OpenAPI |
| O15 | Documentation file names | CLAUDE.md expects `docs/architecture.md`, `agent-spec.md`, `integration-contracts.md` and `frontend-handoff.md`. The files are named `*-modules-final.md`, and the docs cross-reference other names (`strauss-agent-spec.md`, `INTEGRATION_CONTRACTS.md`, `strauss-architecture-en-unified.md`) | **Resolved 2026-10-15** (with the product owner's approval): the files were renamed to the CLAUDE.md names, and only the filename references inside them were updated |
| O16 | Audit table | IC §3 says there is no mandatory audit ledger, but Arch §17 and §20 keep `business.audit_entries` | Backend decision; it does not affect the frontend |
| O17 | Who produces the OpenAPI schema | There is no Python repository in this workspace, so the frontend cannot generate types | The services developer publishes `openapi.json`. The frontend generates its types from it and adds a CI drift check (IC §7.5) |

## 3. What the frontend can build without these approvals

These parts don't depend on DTO or SSE shapes:

- The homepage, built in HTML/CSS faithful to `brown-reference.png`, with its visual tests.
- The responsive shell, header and navigation drawer, and accessible sheets.
- Design tokens and the brown palette.
- Shared loading, error and empty states.
- A decimal-string money formatter.
- Removal of the demo code.

Everything that calls the API waits for O1–O14 and the generated types.
