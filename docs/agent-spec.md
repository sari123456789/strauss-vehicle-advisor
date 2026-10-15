# Strauss Vehicle Advisor — Agent Specification and Acceptance Scenarios

Version 1.5 | 2026-10-15 | Implementation specification; no application has been built under this task.

## 1. Scope, status and sources

This specification consolidates the supplied challenge files and the decisions approved in the conversation. The backend is Python only. LangGraph is the approved AI workflow framework; no LLM provider is mandated here. The system estimates monthly take-home pay under the challenge's demo rules; it is not an approved Strauss payroll system or a complete payroll engine.

Labels: **Source requirement** means explicitly stated in the supplied files. **Approved decision** means agreed with the user. **Implementation proposal** means a recommended technical approach, not a challenge requirement. **Unresolved** means an authoritative decision is still required before the affected behavior is implemented.

The document is in English for implementation. Employee and representative interactions remain in Hebrew. Preserve original Hebrew source filenames, source column names and group labels. Translating the specification does not translate or change source data.

| Source file | Reviewed content | Purpose |
|---|---|---|
| הנחיות האתגר - שטראוס (PDF)(3).pdf | All four pages | Conversation, tools, representative workflow, privacy and output requirements |
| נתוני האתגר - כללים, קטלוג רכבים, עיסוקים, 6 עובדים לדוגמה וחישובים (Excel) (1)(1).xlsx | All six sheets | Vehicle catalogue, occupations, demo employees and reference calculations |
| קובץ הכללים לדוגמה - תקציבים, מסלולים, שיטת הנטו (JSON) (1)(1).json | Entire file, version `mock-2026-10-12` | Configurable rules and demo calculation parameters |
| מאגר שווי שימוש ברכב 2026 - הקובץ המורחב של רשות המסים (CSV, 77,988 רשומות)(2).csv | All records, headers, key uniqueness and catalogue matches | Official-key vehicle benefit lookup |

The source rules were not replaced with current tax tables from the internet. The PDF narrative's example numbers are illustrative, not binding numerical fixtures. The demo uses 2025 payroll tables and 2026 vehicle benefit data; disclose both source years explicitly.

### Instructions for the coding agent

Treat approved decisions as implementation requirements. Keep the acceptance scenarios in this specification and implement appropriate automated checks for calculation, validation, persistence and authorization. Conversation scenarios also require agent evaluations across different Hebrew phrasings; do not reduce them to exact-text assertions. Scenarios are planned tests, not evidence of passing tests. Do not generate implementation-mirroring tests merely to increase the test count. Preserve scenario IDs for traceability and record any actual failure or source discrepancy.

## 2. Agreed behavior

1. Collect employee information through conversation. Persist actual employee facts in the employee-owned latest EmployeeProfile (salary, occupation, group, qualifying child count, and reported current arrangement) and reuse them in new conversations. Stored facts are last-reported values, not automatically verified employer records. Load and display shared actual facts in the side panel at conversation start. Do not demand blanket confirmation; ask about stored facts only when necessary for the current user goal and missing, ambiguous, contradictory, or materially stale.
2. Keep each conversation's messages, state, alternatives, representative questions and calculations independently. An EmployeeProfile update must not rewrite historical messages or calculation snapshots. Hypothetical inputs must not update the actual EmployeeProfile.
3. The language model interprets intent, collects inputs and calls tools. Code determines eligibility, readiness, lookup, calculations and ranking.
4. Support listing vehicles, calculating one or several alternatives, and calculating all eligible alternatives, with or without comparison to the current arrangement.
5. Current vehicle details are not required to calculate alternative net pay alone. A complete current baseline is required for differences against today.
6. For a new vehicle in the demo, use the catalogue's default employee participation and disclose the source. For the current vehicle, use the employee's actual reported participation.
7. A field named "default" does not by itself require a representative question. Escalate missing company information, unresolved conflicts or relevant exceptional arrangements.
8. Variable salary components need an explicit monthly amount to use consistently across the comparison. Asking for a calendar month is not inherently required.
9. An effective date is not mandatory in representative answers. Respect any validity restriction actually provided.
10. Missing is not zero. Never invent amounts, eligibility, identifiers or vehicle specifications.
11. Persist explicit real-world corrections to the versioned shared EmployeeProfile and current case atomically with both expected profile and case versions; concurrent conflicts must be reconciled, not silently overwritten. Hypothetical inputs affect only the scenario.
12. A representative request leaves the conversation active. Only dependent calculations wait; continue other requests, return partial results when possible, and asynchronously resume affected computations when answers arrive.
13. Present human-friendly Hebrew errors to employees, never raw HTTP status codes, exception payloads or stack traces.

## 3. Source review findings

| ID | Verified finding | Required handling |
|---|---|---|
| SRC-01 | CSV contains 77,988 records and 19 columns, tax year 2026; no duplicate complete keys | Revalidate each imported source version |
| SRC-02 | Catalogue has 18 regular and two family-track vehicles, not 20 plus two | Use actual records for counts |
| SRC-03 | Catalogue omits vehicle type code. ATTO 3 has two matches without this field: type 1 benefit 3,130; type 2 benefit 6,950 | A documented type-1 mapping is supported for the demo's 20 vehicles, all of which match the CSV benefit and drive type. Never generalize this assumption to future vehicles |
| SRC-04 | CSV drive-column label conflicts with JSON value mapping | Mapping `0=petrol, 1=hybrid, 2=plug-in hybrid, 3=electric` was verified for all 20 catalogue vehicles. Do not infer drive type from marketing names; SPORTAGE HEV is plug-in hybrid in the supplied data |
| SRC-05 | Excel mentions a 250-gross alternative supposedly in JSON, but JSON only defines `gross_up` | Use 250 net via gross-up; do not pretend a gross-mode switch exists |
| SRC-06 | Payroll rules use 2025 tables alongside 2026 vehicle data | Label the demo estimate and both source years; do not silently update |
| SRC-07 | Two Excel differences appear to subtract rounded net values rather than round the exact difference | Document the exceptions; do not edit source files or conceal the discrepancy |

Import CSV as Windows-1255. Trim surrounding whitespace in headers and values while preserving meaningful content and leading zeros in codes. The official lookup key is `(שנת מס, שנת רישום, קוד סוג רכב, קוד תוצר, קוד דגם)`. Commercial name alone is insufficient.

## 4. Responsibilities

**Canonical module boundary and shared API (approved architecture):** The Python backend has exactly **two functional modules**: **Business System** (the four layers **Application**, **Business Logic (BL)**, **Domain / Core**, and **Infrastructure**) and **AI Engine** (LangGraph conversation/workflow, agent tools, LLM adapter, persisted conversation state and representative-answer continuation). **One shared FastAPI boundary sits outside both modules**, authenticates and validates incoming Angular requests, and dispatches them by route: business, catalogue, calculation and representative endpoints to Business Application; chat, conversations and streaming endpoints to the AI workflow entry point. Internal agent-to-business calls go **only through the Business Application services**, never directly to BL, Core functions, repositories, ORM or PostgreSQL. The API is a transport boundary, not a third business module. API/worker/bootstrap are runtime entry points and wiring, not extra functional modules. The existing **Application–Infrastructure dependency-inversion/ports-and-adapters design remains unchanged**; this clarification does not create mutual direct imports or alter other dependency rules.


**Language model:** interpret requests, propose extracted values with source message references, clarify ambiguity, explain in Hebrew and select permitted tool actions. Extraction does not authorize unrestricted database writes.

**Python workflow controller:** load authorized conversation state, validate and persist inputs, maintain active requests, evaluate readiness, invoke services, prevent duplicate actions and check revisions before publishing.

**Deterministic services:** catalogue filtering, eligibility, tax-record resolution, payroll calculation, ranking and structured results. Calculation services independently validate prerequisites even if readiness was checked earlier.

**Database:** latest employee profile with a version number, complete visible messages, conversation state, representative questions and calculations with immutable input/output snapshots. A separate employee-profile change-history table is not required for the approved demo. Messages do not replace structured calculation inputs.

**Presentation:** render tables and amounts directly from calculation outputs. The model must not rewrite numerical values. Explanations use returned reasons and change breakdowns. Vector retrieval must not determine eligibility or select vehicle benefit records.

## 5. Data dictionary and validation

Collected fields carry `value`, `status`, `source`, `sourceMessageId`, `updatedAt`, `revision` and, when applicable, `lastConfirmedAt`. Actual shared facts are stored in the versioned employee-owned EmployeeProfile; hypothetical overrides and alternatives are conversation-specific. Stored values are last reported and are not silently treated as employer-verified. A qualifying-child count may become stale as ages change. Statuses: `missing`, `needs_clarification`, `usable`. Sources: `employee`, `catalogue`, `rules`, `tax_dataset`, `representative`; `document_confirmed` is future-only. The initial implementation uses typed inputs and has no document upload or extraction. An employee-reported usable value is clear enough for calculation; it is not employer verification.

| Field | Meaning/unit | Required for |
|---|---|---|
| entitlementGroup | Hebrew group label א/ב/ג/ד | Regular vehicle selection and family-track budget |
| occupationId | Resolved occupation-table identifier | Waiver; resolving a conflict when occupation and group are both supplied |
| baseGross | Monthly gross base salary, ILS | Net calculation |
| commissionsGross | Monthly gross commission amount, including explicit zero | Net calculation |
| bonusGross | Monthly gross bonus amount, including explicit zero | Net calculation |
| pensionEmployeeRate | Employee pension contribution rate | Net calculation |
| studyFundEmployeeRate | Employee study-fund contribution rate, including zero | Net calculation |
| creditPoints | Employee-reported tax credit points | Net calculation; never infer from name, sex or children |
| eligibleChildrenCount | Children meeting the approved age condition | Family-track eligibility only |
| currentArrangement | `company_car`, `allowance`, `no_car_no_allowance` | Current baseline |
| currentVehicle | Catalogue ID or verified complete tax key | Baseline with a vehicle |
| currentParticipation | Actual monthly participation, ILS | Baseline with a vehicle |
| currentFamilyDeduction | Actual family-track deduction and whether already included in reported participation | Family baseline; prevent double deduction |
| currentAllowance | Current vehicle allowance, separate from base salary | Allowance baseline |
| alternativeIds | Vehicle IDs or `waiver` | Selected alternatives |
| preferences | Explicit drive/filter/sort preferences | When requested |

Use decimal monetary values; reject NaN and infinity. Negative ordinary input salary components are unsupported in the demo and require clarification. Normalize 6% to 0.06 exactly once. An ambiguous unit or rate requires clarification. Rates must be in 0–1; child counts are nonnegative integers; credit points are nonnegative numbers. Do not invent legal salary limits. Explicit zero is valid where appropriate.

When the employee reports total gross salary, do not add commissions and bonuses again. Clarify whether they are included and map to the component structure. Do not silently discard unsupported salary deductions/components. Explain the model's limitation and clarify before presenting the estimate as complete.

## 6. Request handling and readiness

Active requests contain `requestId`, `intent`, `scope`, `filters`, `baselineRequired` and `stateRevision`. Intents: `list_options`, `calculate_options`, `compare_to_current`, `explain_result`, `hypothetical`, `correct_inputs`, `show_history`, `cancel`.

| Action | Prerequisites | Not inherently required |
|---|---|---|
| List regular vehicles | Valid group and available catalogue | Salary, current vehicle, unrelated occupation details |
| List family vehicles | Group and qualifying child count | Salary |
| Alternative net pay | Complete salary inputs, resolved alternatives, eligibility and full benefit/participation inputs | Current baseline |
| Difference against today | Calculation prerequisites plus a complete current baseline | Unrelated preference information |
| Waiver | Salary and resolved eligible occupation with authoritative allowance amount | Vehicle resolution for alternative net alone |

Readiness output: `readyScenarioIds`, `missingEmployeeFields`, `ambiguousFields`, `pendingRepresentativeFields`, `blockedScenarios`, `baselineReady`, `scopeComplete`, `stateRevision`. Readiness is distinct from eligibility: ineligible alternatives are blocked, not merely incomplete.

For each message: load authorized state and profile versions → extract/classify actual versus hypothetical intent → validate and atomically persist authorized changes → evaluate contextual readiness → ask, escalate, calculate or present. An ambiguous actual-versus-hypothetical intent must be clarified before any profile write. Calculate without extra approval when required information is complete. Calculate ready alternatives even if others are pending; display the status and reason for every unready alternative. A missing baseline blocks differences, not necessarily alternative net values.

"All vehicles" includes all eligible regular catalogue vehicles and family vehicles whose eligibility has been checked. If the child count is unknown, present regular vehicles and mark family eligibility as unchecked; do not claim complete coverage. Waiver is a separate option requiring occupation checks. "All alternatives including waiver" requires this check or an explicit pending status.

Approved demo age rule: age 23 is included, up to but not including the 24th birthday. Ask how many children have not yet turned 24 **only when family-track eligibility is relevant and the stored count is missing or uncertain for the reference date**. Birth dates are unnecessary if the employee provides an unambiguous qualifying count.

## 7. Tool contracts

The server binds `conversationId` to the authorized employee. The model does not select identity or ownership. Validate submitted revisions; an employee-provided foreign conversation ID must not grant access.

| Tool | Main input | Main output |
|---|---|---|
| get_conversation_context | Server-bound conversation ID | State, EmployeeProfile version used, latest EmployeeProfile version and mismatch flag, active request, lists, questions and result freshness |
| update_conversation_inputs | Allowlisted fields, source message, expectedRevision, expectedProfileVersion, mode `actual_update`/`hypothetical` | Actual employee-fact update atomically changes EmployeeProfile and case using both expected versions; hypothetical changes only scenario state; accepted/ambiguous fields, conflicts, new revisions and stale results |
| list_eligible_options | Conversation state/revision, scope and filters | resultSetId, IDs, eligibility reasons, unchecked tracks, scopeComplete |
| resolve_vehicle | Make/name/year/identifiers; purpose current/alternative | resolved, ambiguous or not_found; candidates |
| get_vehicle_tax_value | Complete key or explicitly mapped ID | Benefit amount, complete record key and dataset version; no partial-key auto-selection |
| check_eligibility | State revision and resolved alternative | eligible, ineligible or needs_information; reasons |
| check_calculation_readiness | Active request and state revision | Section 6 output; may be internal to the controller |
| compare_scenarios | resultSetId or selected IDs, baselineMode, expectedRevision | Optional baseline, each alternative's results/status, issues, sources and versions |
| create_representative_question | Missing field, alternative, question, requestId, idempotency key | questionId and save status |
| get_comparison_details | Authorized calculation/comparison ID | Input/output snapshot, kind, freshness and alternatives |
| get_conversation_messages (optional) | Authorized sequence range or messageId | Relevant earlier messages; may be internal retrieval |

For all-alternative requests, prefer `resultSetId` over requiring the model to copy every ID. Validate set/state freshness. Internal services may run within the comparison service; a separate model call per vehicle is unnecessary.

Suggested response envelope: `status`, `data`, `issues`, `stateRevision`, `sourceVersions`. Suggested issue codes: `MISSING_INPUT`, `AMBIGUOUS_INPUT`, `INELIGIBLE`, `VEHICLE_NOT_FOUND`, `TAX_KEY_AMBIGUOUS`, `SOURCE_CONFLICT`, `STALE_REVISION`, `PENDING_REPRESENTATIVE`, `TOOL_UNAVAILABLE`, `UNSUPPORTED_INPUT`. Technical failure is distinct from missing data; failure is never zero money.

## 8. Demo calculation and numerical verification

This is the reconstructed challenge calculation, not guidance for real tax law. Read all parameters from JSON.

Define:

- `G = baseGross + commissionsGross + bonusGross`.
- `P = G × pensionEmployeeRate`; `K = G × studyFundEmployeeRate`.
- `A` = taxable vehicle allowance; `V` = taxable vehicle benefit; `E` = grossed-up electricity reimbursement.
- Tax and insurance base: `T = G + A + E + V`. Vehicle benefit is not paid cash.
- `R` = ordinary employee participation; `F` = family-track gap deduction.

Income tax is the marginal bracket sum from JSON, minus `creditPoints × 242` and `0.35 × min(P, 0.07 × 9500)`, floored at zero. Calculate national insurance and health insurance separately using the JSON thresholds, ceilings and rates, then sum them.

For a family alternative: `F = 0.5 × max(0, catalogue monthly cost − employee group budget)`. For a regular alternative, `F=0`. Neither R nor F reduces V.

`net = G + A + E − incomeTax − nationalInsurance − healthInsurance − P − K − R − F`.

For an electric vehicle, solve in code `net(E) − net(0) = 250` for that same vehicle and unchanged inputs. Do not add 250 again after including E. Hybrid and plug-in hybrid vehicles receive no electric reimbursement under the supplied demo rule. Apply the same electric policy to an electric current vehicle so baseline and alternatives are consistent.

Approved contribution rule: E is taxable and included in insurance bases, but excluded from employee pension and study-fund contribution bases. A is also excluded from contributions under the supplied waiver rule. P and K are calculated from G only.

All 12 displayed net values, all tax/insurance values, rounded gross-ups and family deductions in the six reference cases were reproduced. The audit was independently repeated using Decimal; the audit script is not a production engine.

| Case | Exact current net | Exact alternative net | Exact difference | Displayed exact difference rounded | Excel difference |
|---|---:|---:|---:|---:|---:|
| E-001 | 11,031.819 | 11,692.817 | 660.998 | 661 | 661 |
| E-002 | 8,599.733 | 5,077.844 | -3,521.889 | -3,522 | -3,522 |
| E-003 | 15,047.338 | 13,108.657 | -1,938.681 | -1,939 | -1,938 |
| E-004 | 10,692.845 | 11,899.807 | 1,206.962 | 1,207 | 1,207 |
| E-005 | 14,244.019 | 17,806.288 | 3,562.269 | 3,562 | 3,562 |
| E-006 | 17,072.533 | 17,794.233 | 721.700 | 722 | 721 |

Approved rounding policy: subtract full-precision net values, then round only for display, as required by JSON. Document the two conflicting Excel difference cells as source exceptions. Do not assert full agreement for those cells or alter formulas to conceal a one-shekel discrepancy.

Numerical implementation proposals: Decimal precision of at least 28 digits; display whole shekels using ROUND_HALF_UP, including negative amounts; gross-up error tolerance of 0.000001 ILS; upper-bound expansion and bounded iterations during solving. These exact technical thresholds are proposals, not source mandates. Non-convergence produces an error, not an undisclosed approximate result.

## 9. Persistence, history and representative workflow

Approved simplified storage: `employees`, `employee_profiles`, `conversations`, `messages`, `calculations`. Representative questions/answers remain separately stored under their workflow. Business source tables are outside this narrow persistence description. No separate tables are required for salary revisions, change events, selected alternatives or hypothetical scenarios; keep necessary data in conversation state and calculation snapshots.

### 9.1 What is stored where

| Table | Main fields | Purpose |
|---|---|---|
| employees | employeeId, identity/authorization data | Owner of the profile and conversations |
| employee_profiles | employeeId, actual salary and reported occupation/group/qualifying children/current arrangement fields with status/source/lastConfirmedAt, version, updatedAt | Latest reported actual employee facts; not verified employer data |
| conversations | conversationId, employeeId, state, stateRevision, createdAt, updatedAt | Active request, collected inputs, missing fields, actual baseline, active hypothetical if any, displayed lists, alternatives and relevant calculation/question IDs |
| messages | messageId, conversationId, sequence, role, content, createdAt, optional calculationId | All visible messages in order; rich output stored as structured content or linked to a calculation |
| calculations | calculationId, conversationId, kind, inputSnapshot, results, sourceVersions, employeeProfileVersion, createdAt | Regular and hypothetical calculations in the same table with immutable complete inputs/results |

Each conversation belongs to one employee; each message and calculation belongs to one conversation. One comparison calculation may include a baseline and several alternatives. `kind` is regular or hypothetical. Snapshots include salary, arrangement inputs and hypothetical overrides. Results contain exact amounts, display values and per-alternative statuses. Conversation state preserves displayed list IDs and ordering for references such as "the second one".

Shared EmployeeProfile fields: `baseGross`, `commissionsGross`, `bonusGross`, `pensionEmployeeRate`, `studyFundEmployeeRate`, `creditPoints`, `occupationId`, `entitlementGroup`, `qualifyingChildrenCount`, and the last reported `currentArrangement` (including actual vehicle/key, participation, family deduction and inclusion flag, or allowance as applicable). Each field records status, source, update time, and a confirmation timestamp when applicable. Partial updates change only explicitly supplied actual fields. Partial profiles are permitted but calculations require all relevant fields. Alternatives and hypothetical overrides remain conversation-only. Load and show stored actual values in the side panel, without a blanket confirmation questionnaire. Verify only fields relevant to the current action and only when missing, ambiguous, contradictory, or materially stale. Browsing regular vehicles must not require salary or current-car verification; alternative net alone does not require a current baseline. A precise difference against today requires a suitably current reported arrangement and participation. Review the qualifying-child count when family eligibility is requested and the count might no longer reflect the reference date.

An unambiguous real-world update changes only the specified fields of the shared EmployeeProfile and current conversation case atomically under `expectedProfileVersion` and `expectedCaseVersion`; both versions update as required. No separate per-change record is required: messages remain and historical calculations retain original inputs. A hypothetical is saved in conversation state; when calculated, its result is stored in calculations with kind=hypothetical without changing the actual profile/baseline. Do not store the model's private reasoning.

Example: actual salary 18,500 is saved in the profile; checking salary 20,000 creates a hypothetical calculation in that conversation. A new conversation loads 18,500. After an actual update to 19,000, new conversations load 19,000; historical results keep their original inputs.

### Continuing a conversation and model context

Persist all messages for full history display and reopening. The model does not need the entire transcript on every turn. Load current conversation state, latest profile/version, recent messages and relevant calculations/questions. Retrieve earlier messages or calculation details when necessary. An optional summary is an aid, not a replacement for messages or numerical snapshots.

Earlier-message retrieval may use `get_conversation_messages` with an authorized bounded sequence range or message anchor, or internal controller retrieval.

When reopening an old conversation, preserve history and disclose salary-version differences if relevant. Before a new regular calculation, reconcile applicable actual EmployeeProfile facts and their versions with the conversation state without rewriting old messages/results; do not replace saved hypothetical overrides. "Show the previous comparison" retrieves stored results; "calculate again" creates a new calculation. Continuing a hypothetical retains its original inputs unless the employee requests changes; if the profile has changed, explain that the hypothetical uses its saved baseline.

Classify hypothetical intent by meaning and context, not keywords alone. "Suppose I receive" and "if my salary increases" may be hypothetical; "my salary increased" and "I was mistaken" may be actual updates. Clarify ambiguous intent before updating the profile. Converting a hypothetical into an actual update requires explicit intent and only the clearly specified values.

The latest update is the last successful server commit under expectedProfileVersion control. Concurrent updates from the same version must not silently overwrite each other. Regular calculations check conversation/profile versions before publication; historical and hypothetical results identify their fixed input snapshots. A version number does not require a historical revision table.

### 9.2 Questions, retrieval and authorization

Input writes and revision changes are atomic. Publish a result as current only when its state/source versions still match. A representative question is linked to conversation, alternative, field, active request, and relevant versions, with independent statuses (`open`, `answered`, `cannot_determine`, `cancelled`). Creating it **does not pause or block the conversation or LangGraph intake**: only the dependent scenario, fact or delta is pending. Present any ready alternatives immediately with explanations, or invite the employee to continue chatting if none are ready. On reply, verify relevance, authorization, applicability and versions; save the answer, and recalculate newly ready portions when appropriate, issuing a non-disruptive asynchronous update. Do not switch the active topic or revive cancelled requests. A graph interrupt, if used, must be scoped to a pending subtask and never lock the full conversation.

Representative answer fields: answerId, questionId, value, unit, answeredBy, answeredAt and optional validityNote/effectiveFrom. No date is mandatory. If a future or ambiguous validity restriction is supplied, clarify applicability before using it now. Do not build complex date workflows without need. Use a structured answer value with optional free-text notes. A point answer does not authorize changing global rules.

Historical requests use snapshots; new calculations use applicable current inputs. Clarify ambiguous references to previous comparisons. Enforce authorization on every read/write. Do not expose salary documents to representatives by default; share only information needed for a question. Avoid unnecessarily logging full salary data. Documents are future-only in the initial implementation.

Approved demo retention: no automatic time-based deletion of conversations/calculations. Define real-data retention policy before a pilot; demo retention is not pilot policy.

## 10. Conversation and output rules

Clarify ambiguity only when it affects the requested action. Show stored actual EmployeeProfile values in the side panel at the start of a new conversation, but **never require blanket confirmation**. Ask or reconfirm only the relevant missing, ambiguous, contradictory or materially stale facts. Do not ask for irrelevant information or repeat questions already answered clearly in the current context or by an authoritative source. If the employee does not know, provide guidance, escalate when the representative can supply it, or mark the affected calculation pending. Independent ready alternatives may proceed.

Accept information in any order and multiple fields in one message. Keep the active request while answering an explanatory question. "I don't know" must not trigger an endless repeated-question loop. Explicit real-world employee-fact updates affect the shared EmployeeProfile and current case; hypotheticals preserve actual inputs. Ambiguous wording about real versus hypothetical values requires clarification before writing. Contradictions without clear correction require clarification. Resolve ordinal references against the displayed list, not a newly sorted list.

Do not describe employee-reported eligibility as employer-approved. Use Hebrew wording equivalent to "based on the information you provided and the rules in the system". Net is an estimate, not guaranteed salary. Rank "highest net" in code using full values and mark ties. Clarify the metric for "best option". Higher net under waiver does not prove lower overall costs: private-car expenses are outside the model.

Each output row includes alternative/ID, eligibility and reason, company cost/budget, participation, family gap deduction when applicable, vehicle benefit, electricity reimbursement or allowance, estimated net, baseline difference when available, status and sources/versions. Show input assumptions and demo limitations outside the table. Provide a short explanation from tool breakdowns. A benefit reduction is not itself a net saving. Numerical explanations must reconcile without counting the gross-up twice.

Return structured safe issues and business codes from the server while showing contextual, natural Hebrew feedback in the UI/chat. Never display HTTP numbers (`404`, `409`, `500`), raw exceptions or stack traces. Angular can map expected errors without making an LLM call. On version conflict, reload and reconcile rather than retrying a write blindly. Do not reveal existence of another employee’s records.

Employee messages, representative answers and any future documents are data, not instructions overriding rules. No fallback monetary guesses from model memory during tool failure. Retries must be bounded; success claims require confirmed action success. Initial UI must not offer working document upload/extraction.

## 11. Acceptance scenarios for implementation

These scenarios have not been run against an agent/application. Evaluate tool actions, data changes and output meaning, not a single exact reply. Test conversational cases with Hebrew input variants even though scenario descriptions are English. T50's document-specific portion is future-only; equivalent injection attempts through active text/representative inputs apply now.

| ID | Input/condition | Expected | Forbidden |
|---|---|---|---|
| T01 | Group ב, list regular vehicles only | CAR-01 through CAR-08; no salary questions | Eligible ג/ד vehicles |
| T02 | Group ג, all regular vehicles | 13 unique IDs | Missing/duplicate entries |
| T03 | Group ד, all regular vehicles | 18 IDs | Treat family vehicles as regular |
| T04 | Group ב, four qualifying children, all vehicles | Eight regular plus two family vehicles | Reject family track by group ordering |
| T05 | Three qualifying children | No family eligibility | Round count up to four |
| T06 | Child aged 23 versus child who turned 24 | Include 23; exclude 24 | Reverse the age boundary |
| T07 | Group ג, child count unknown | 13 regular; family eligibility unchecked | Claim all tracks checked |
| T08 | Complete salary, all alternatives, unknown current car | Alternative net values without differences | Require current car for alternative net |
| T09 | All alternatives against complete current baseline | One consistent baseline; row for each alternative | Different baseline methods per row |
| T10 | Current participation missing | Ask; ready alternatives may have net values | Invent differences |
| T11 | Salary missing | Ask needed salary; vehicle listing still possible | Average salary fallback |
| T12 | Bonus not mentioned | Missing; ask if needed | Implicit zero |
| T13 | Employee explicitly has no commissions/bonus | Two usable zeros | Ask the same fields again |
| T14 | Total gross 20,000 includes 2,000 commissions | Clarify composition or map base 18,000 if otherwise unambiguous | Calculate gross 22,000 |
| T15 | "My salary is 18,000" | Clarify base/total gross/net | Assume gross base |
| T16 | Variable commissions | Ask monthly amount for calculation | Choose month/average without data |
| T17 | One message supplies group, salary and contribution fields | Extract all clear inputs | Repeat captured questions |
| T18 | Credit-points explanation requested mid-collection | Explain and retain active request | Cancel comparison |
| T19 | Employee does not know credit points | Explain where to check; no net yet | Infer from name |
| T20 | Occupation table says ב, employee reports ג | Clarify conflict | Silent overwrite |
| T21 | Waiver for mandatory field role | Ineligible with reason | Present as eligible waiver |
| T22 | Waiver for software developer | 1,500 allowance from occupation table | Invent allowance |
| T23 | Occupation absent from table | Clarify/escalate waiver eligibility | Unconfirmed similar-role match |
| T24 | Corolla name with multiple candidates | Resolve model/year or offer choice | Auto-select Corolla Cross |
| T25 | ATTO 3 mapping lacks vehicle type | TAX_KEY_AMBIGUOUS | First-record selection |
| T26 | ATTO 3 complete key, type 1 | V=3,130 | V=6,950 |
| T27 | Codes 01014 and 00003 | Preserve leading zeros | Unnormalized numeric codes |
| T28 | Current car outside catalogue, valid tax key | Calculate baseline | Reject baseline solely due to catalogue |
| T29 | New vehicle outside catalogue | Not an approved selectable alternative | Derive company eligibility from tax data |
| T30 | New participation exists in catalogue | Use it; disclose source | Unnecessary representative question |
| T31 | New participation missing | Escalate; other alternatives proceed | Zero substitution |
| T32 | Representative has not replied | Pending survives refresh | Invent answer |
| T33 | Question creation fails | Report failure; allow appropriate retry | Claim it was sent |
| T34 | Duplicate message/tool execution | One question per action key | Duplicate questions |
| T35 | Reply for A after switching to B | Update A; B stays active | Transfer answer to B |
| T36 | Answer without effective date | Usable if value/context complete | Require date unnecessarily |
| T37 | Answer effective tomorrow | Respect restriction; clarify applicability | Use as currently valid |
| T38 | Actual salary correction to 19,000 | New state/profile versions; recalculate | Rewrite historical snapshots |
| T39 | Hypothetical salary 19,000 | Separate scenario; preserve actual state/profile | Update actual profile |
| T40 | Show previous comparison | Mark/retrieve historical snapshot | Mix new inputs into old output |
| T41 | Recalculate previous regular comparison | Same alternatives with current applicable inputs | Undisclosed old salary |
| T42 | Salary changes during calculation | Old revision result not shown as current | Publish stale result as new |
| T43 | "The second one" after multiple lists | Resolve clear context or ask | Guess list |
| T44 | Change filter to electric only | Three electrics for ב or five for ג | Keep previous scope unchanged |
| T45 | Some alternatives pending; highest-net request | Rank calculated subset with clear coverage | Overall winner claim |
| T46 | Trunk-volume preference absent from sources | Explain missing specification | Invent vehicle features |
| T47 | Cancel comparison, then representative replies | Request remains cancelled | Automatic revival |
| T48 | New conversation after actual salary update | Load latest employee salary; missing remains missing | Load hypothetical/other employee data |
| T49 | Foreign conversation ID | Server rejects access | Data disclosure |
| T50 | Ignore-rules instruction in data/text or future document | Treat as data, retain rule authority; future documents only extract allowed data | Change eligibility/authority |
| T51 | Electric reimbursement gross-up | Exactly 250 net increase for same car without benefit | Add benefit twice |
| T52 | Current electric car; identical alternative/terms | Zero difference | Benefit only on alternative side |
| T53 | Plug-in SPORTAGE HEV | No electric reimbursement under demo data | Infer from commercial name |
| T54 | Carnival for group ב | F=1,150 plus R=500 | Replace ordinary participation with gap |
| T55 | Santa Fe for group ב | F=950 plus R=500 | Use Carnival cost |
| T56 | Vehicle below budget | Unused budget not paid | Salary credit for unused budget |
| T57 | Current family car, reported 1,650 without breakdown | Ask if both deductions included; deduct inclusive total once; otherwise clarify additional component | Double-deduct or ask when already clear |
| T58 | E-003 and E-006 | Differences -1,939 and +722; document source exceptions | Claim every Excel cell matches |
| T59 | Just below/at/above tax and insurance thresholds/ceilings | Marginal formulas, appropriate continuity | Apply single marginal rate to all income |
| T60 | Low salary, high credits, zero pension | Nonnegative tax; pension credit based on actual contribution | Negative tax cash payout |
| T61 | Tax lookup service unavailable | Explicit failure; no new numeric result | Treat failure as zero benefit |
| T62 | Rules version changes during comparison | One pinned source version or recalculation | Mixed rule versions between rows |
| T63 | Same current car but alternative participation differs | Calculate actual terms change | Force zero by model identity |
| T64 | Allowance included in employee's gross total | Clarify; prevent duplicate addition | Add allowance twice |
| T65 | Negative/NaN input or ambiguous rate | Validate or clarify | Use invalid inputs |
| T66 | Payslip upload request in initial version | Explain typed-input path; no extraction | Offer active upload or claim analysis |
| T67 | Hypothetical bonus phrased "suppose I receive" | Hypothetical only | Update profile by keyword failure |
| T68 | Ambiguous "check with salary 20,000" | Clarify actual versus hypothetical before shared write | Silent overwrite |
| T69 | Actual bonus-only update | Update bonusGross only | Reset other fields |
| T70 | Reopen old chat after salary changed elsewhere | Original history; version mismatch; synchronize before new regular calculation | Rewrite old results |
| T71 | Two updates from same profile version | One commit; other gets version conflict before resolution | Model completion order silently overwrites |
| T72 | Explicitly save hypothetical values as actual salary | Update clearly requested actual fields | Promote all assumptions indiscriminately |
| T73 | Refresh/reopen | Full ordered messages, state, lists and calculation inputs preserved | Summary-only history |
| T74 | Missing/partial employee profile | Collect missing inputs; persist actual updates | Invent absent values |
| T75 | Resume hypothetical after refresh | Retrieve kind=hypothetical; original inputs; unchanged profile | Treat hypothetical transcript values as actual |
| T76 | Needed detail outside recent context | Retrieve relevant authorized messages/calculation | Guess from incomplete summary |
| T77 | Gross-up with contribution rates | P/K from G only; net +250; tax/insurance include E | Contributions on E |
| T78 | Old demo conversation/calculation | Still retrievable; no scheduled expiry | Unspecified timed deletion |
| T79 | Ambiguous salary detail irrelevant to vehicle listing | Proceed using required eligibility inputs | Block for unnecessary payroll detail |
| T80 | New conversation with stored profile; browse regular vehicles | Display actual profile in sidebar; ask only for relevant group if needed | Blanket confirmation questionnaire |
| T81 | Alternative net only with stale saved current-car value | Calculate alternative if its relevant inputs are ready | Demand baseline confirmation |
| T82 | Precise delta requested with outdated reported current arrangement | Confirm only material baseline facts | Unverified precise delta |
| T83 | Real-world correction vs hypothetical or ambiguous value | Actual update changes profile with expected versions; scenario stays local; ambiguous intent is clarified | Promote hypothetical or overwrite conflict |
| T84 | Representative request pending while user continues chatting | Respond to unrelated requests and show ready partial results | Block entire conversation |
| T85 | Representative replies after the user changed topic | Update relevant pending work without changing active topic | Hijack current conversation |
| T86 | Tool/API error with status 404/409/500 | Present appropriate natural Hebrew message | Expose numeric status or raw exception |


## 12. Coverage and acceptance criteria

| Requirement | Sections | Scenarios |
|---|---|---|
| Conversation and missing inputs | 5–6, 10 | T11–T20 |
| All vehicles and multiple calculations | 6–7 | T01–T10, T44–T45 |
| Exact tax lookup key | 3, 7 | T24–T29 |
| Eligibility/rules | 5–8 | T04–T07, T20–T23, T53–T56 |
| Code-based calculation | 4, 8 | T51–T60, T63–T65, T77 |
| Representative and refresh continuation | 9 | T31–T37, T47, T84–T85 |
| Corrections/history/shared employee profile | 9–10 | T38–T43, T48, T62, T67–T83 |
| Privacy/robustness | 9–10 | T48–T50, T86 |

Acceptance: every requested alternative has a result or explicit status/error; no amounts based on missing required inputs; identical input/source snapshots produce identical calculation outputs; no model-only eligibility decisions; code verifies list completeness; six reference cases verified component by component with documented rounding exceptions. Evaluate language behavior across Hebrew paraphrases and repeated runs without requiring identical prose.

Completed source checks: reread all four files, CSV counts/key uniqueness, all 20 catalogue matches with type 1, drive mapping verification, six-case reconstruction repeated with Decimal including gross-up tolerance. Scenario numbering/completeness was checked during editing. There are 86 planned scenarios. No agent evaluation, production engine verification, live database/authorization/refresh testing has been performed; do not mark these as passed.

## 13. Approved decisions

The table records the original 2026-10-14 decisions; the 2026-10-15 approved employee-profile, scoped confirmation, non-blocking representative and natural-error behavior is integrated into Sections 2, 5–7 and 9–11 above.

| Topic | Implementation decision |
|---|---|
| Electric contribution base | Gross-up in code for +250 net; taxable and insured, excluded from employee pension/study-fund bases |
| Difference rounding | Subtract exact net values, then display-round per JSON; document two Excel exceptions |
| Child age | Include age 23; exclude from age 24 |
| Current family deduction | Clarify whether an ambiguous total includes both deductions; deduct inclusive total once; otherwise obtain missing component |
| General ambiguity | Ask only when relevant to the requested action; never guess |
| Demo retention | No automatic time-based deletion; pilot policy separately defined |
| Payslip/contract upload | Excluded from initial implementation; typed-input collection only |

## 14. Decision boundaries

The discussed decision list is complete. Other explicitly marked technical proposals are not automatically approved business decisions. Version 1.5 consolidates the specification for the coding agent, preserves all earlier approved behavior and scenario IDs, adding T80–T86, and marks document-specific testing as future-only consistently with current scope. Source files are unchanged; no application was built. Future source conflicts still require appropriate clarification; the model has no authority to change the rules.

