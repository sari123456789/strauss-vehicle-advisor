# Business Decisions, Source Findings and Step 0 Record

Working record for the Business Module. It does not override `agent-spec.md`, `architecture.md` or
`integration-contracts.md`. Items marked **Pending** require the project owner's approval.

## 1. Approved implementation decisions (2026-10-15)

| ID | Decision |
|---|---|
| D1 | Documents and sources normalized to CLAUDE.md paths, byte-for-byte (see `data/source-manifest.json`). Originals kept unchanged in place. |
| D2 | Architecture persistence model: `business.cases`, `alternatives`, immutable `comparison_runs`, `operations`, `option_result_sets`; no duplicated AI conversation storage. Audit: see §4. |
| D3 | Shared contracts in `app/contracts/`. |
| D4 | Conversation management → AI Engine; business case management → Business Application; atomic boundary documented in `api-contracts.md` §6. |
| D5 | Canonical names `qualifyingChildrenCount`, `expectedCaseVersion`, `requestId`; mapping in `api-contracts.md` §5. |
| D6 | No propulsion inference for non-catalogue vehicles (`needs_clarification`). |
| D7 | Stable, versioned occupation identifiers keyed by exact Hebrew name (§5). |
| D8 | Persisted option result sets. |
| D9 | Python 3.12, SQLAlchemy 2 async, psycopg 3 — compatibility verified (§6). |
| D10 | Full-precision `Decimal`; round for display only. |
| D11 | Demo representatives see authorized requests with minimal context; no profiles or salary histories. |
| D12 | Shared DTOs/envelopes/errors/interfaces are provisional until integration review. |

## 2. Source findings (verified read-only during planning)

| ID | Finding | Handling |
|---|---|---|
| SRC-01 | CSV: 77,988 records, 19 columns, cp1255 decodes strictly, no BOM, no duplicate complete five-part keys; manufacturer/model codes are always 5 digits with leading zeros; surrounding whitespace in headers and values; dates `dd/mm/yyyy`. | Revalidate on every import. |
| SRC-02 | Workbook text says "20 + 2" vehicles; actual rows are 18 regular + 2 family. | Import actual rows; warning in import report. |
| SRC-03 | Catalogue lacks vehicle-type code. All 20 catalogue vehicles match a type-1 CSV record with identical value of use and drive code. For CAR-10 the partial key (2025, 01014, 00003) also matches a **type-2 record named TANG** (value 6,950) — a different vehicle, not a second ATTO 3 record. | Explicit per-vehicle type-1 mapping in the derived policy; partial key → `TAX_KEY_AMBIGUOUS`. |
| SRC-04 | CSV drive-column header (`1-חשמלי.2-היברידי.3-פלג-אין`) conflicts with the JSON mapping (0 petrol, 1 hybrid, 2 plug-in, 3 electric); the JSON mapping matches all 20 catalogue vehicles. | Catalogue propulsion for catalogue vehicles; raw drive code stored uninterpreted; no dataset-wide classification. |
| SRC-05 | Workbook mentions a "250 gross" alternative in the rules file; JSON defines only `gross_up`. | 250 net via gross-up only. |
| SRC-06 | 2025 payroll tables with 2026 vehicle data. | Disclose both years. |
| SRC-07 | Workbook deltas E-003 (−1938) and E-006 (721) differ from rounding the precise difference (−1939, +722). | Approved rule: round the precise difference; document exceptions. |
| SRC-08 | The challenge PDF has 5 pages (page 5 is contact information); documents refer to "four pages". | Informational. |

Reference reconstruction (independent Decimal script, not application code): all six employees'
net/tax/insurance/gross-up/family values match the workbook, with the two delta-display exceptions above.

## 3. Step 0 environment record

- Python 3.12.15 (uv-managed) for the backend; packages resolved with `exclude-newer = 2026-10-01` so every
  dependency is at least two weeks old; exact versions pinned in `src/backend/uv.lock`.
- Local TLS: Avast Web/Mail Shield intercepts HTTPS. Windows trusts its root; uv does not. During setup the
  Avast root was exported from the Windows store into a temporary CA bundle passed to uv via
  `SSL_CERT_FILE` for those commands only. No system setting was changed.
- PostgreSQL 16.4 in Docker (`compose.yaml`), host port 55432, `server_encoding=UTF8`, no tables yet.

## 4. Audit requirement analysis (D2) — **Pending approval**

- **Mandatory:** Architecture §17–§18 require an audit record in the representative-answer transaction;
  §20 lists `business.audit_entries` (restricted access); Appendix A lists `infrastructure/database/audit.py`.
- **Excluded:** Integration Contracts §3 and Frontend Handoff exclude a *salary-change-history /
  audit-ledger* table; Agent Spec §9 requires no profile change history; Architecture §7.1 forbids promising
  a complete profile-change audit without implementing one.
- **Recommendation:** implement a minimal `business.audit_entries` scoped to representative-answer
  application (actor, case, request, field code, outcome, source reference, timestamp; no salary values),
  and no profile change-history table.

## 5. Occupation identifiers (D7) — proposed scheme `v1`, **Pending approval**

Keyed by the exact (trimmed) Hebrew occupation name; never derived from row order; never reused. An
unmapped workbook occupation, or a mapped occupation missing from the workbook, fails the import. To be
stored in `data/rules/derived-demo-policy.json` (Phase 1/2).

| Occupation | ID |
|---|---|
| נציג/ת מכירות שטח | `OCC-FIELD-SALES-REP` |
| מנהל/ת אזור מכירות | `OCC-REGIONAL-SALES-MGR` |
| טכנאי/ת שירות | `OCC-SERVICE-TECH` |
| נהג/ת הפצה | `OCC-DISTRIBUTION-DRIVER` |
| מפקח/ת איכות במפעל | `OCC-PLANT-QA-INSPECTOR` |
| רכז/ת רכש | `OCC-PROCUREMENT-COORD` |
| מהנדס/ת תהליך | `OCC-PROCESS-ENGINEER` |
| מפתח/ת תוכנה | `OCC-SOFTWARE-DEV` |
| אנליסט/ית נתונים | `OCC-DATA-ANALYST` |
| מנהל/ת מוצר | `OCC-PRODUCT-MGR` |
| מנהל/ת פיתוח | `OCC-DEV-MGR` |
| מנהל/ת משאבי אנוש | `OCC-HR-MGR` |
| מנהל/ת מחלקה | `OCC-DEPT-MGR` |
| מנהל/ת חטיבה | `OCC-DIVISION-MGR` |

## 6. Dependency compatibility check (D9)

Resolution-only check (nothing installed into the project) on Python 3.12 with
`fastapi, pydantic, pydantic-settings, sqlalchemy[asyncio], psycopg[binary], alembic, openpyxl, langgraph,
langgraph-checkpoint-postgres`: resolved 56 packages, including `langgraph 1.2.12`,
`langgraph-checkpoint-postgres 3.1.2`, `psycopg 3.3.6`, `psycopg-pool 3.3.3`, `sqlalchemy 2.1.1`,
`pydantic 2.13.5`, `fastapi 0.142.2`. No conflict between the business stack and the LangGraph PostgreSQL
checkpointer. LangGraph is **not** a backend dependency yet (AI developer's decision).

## 7. Step 1 implementation notes (Domain + deterministic engine)

- Approved: R1 (minimal audit for representative-answer application only) and R6 (occupation IDs above),
  2026-10-15.
- `data/rules/derived-demo-policy.json` holds the explicit demo policy flags (architecture §5), gross-up
  settings (§12) and support limits (§6), bound to the rules source by SHA-256; loading fails if the
  rules file hash differs. Occupation and vehicle-type mappings will be added with the importers (Step 2).
- Policy-support validation lives in BL (`app/business/bl/rules/validator.py`) and is invoked by BL/Application;
  Infrastructure (`rules_loader.py`) maps JSON to Domain only, preserving the dependency table
  (Infrastructure → Domain). This differs from the Appendix A description of `rules_loader.py`
  ("coordinates … business validation"), which would otherwise require Infrastructure → BL.
- Electric scenarios: the gross-up is solved by bisection to |net(E) − net(0) − 250| ≤ 0.000001, so raw nets
  of electric scenarios carry that tolerance (e.g. E-001 alternative net 11692.817000982…). Displayed and
  3-decimal values are unaffected.
- `httpx` is used for the FastAPI/Starlette TestClient (Starlette 1.7 supports it via fallback and emits a
  deprecation warning recommending `httpx2`; no technical requirement for `httpx2` was found).

## 8a. Step 2 environment note

- `pypdf 6.19.0` added (PDF readability verification only), resolved under the existing
  `exclude-newer` date. Added with the same temporary CA-bundle method as §3 (`--native-tls` did not
  help); no system setting was changed.

## 9. Step 2 implementation notes (importers + reference-data persistence, approved 2026-10-17)

Scope: Step 2 importers combined with the reference-data part of Step 3 (owner decision). Moved
forward: Alembic + `business` schema; tables `rules_snapshots`, `catalogue_versions`, `vehicles`,
`occupation_policies`, `tax_dataset_versions`, `value_of_use`, plus `reference_releases`; async
engine/session factory; reference-data repository; `app/cli/import_data.py`. Not moved forward:
profiles, cases, alternatives, provenance, comparison runs, operations, representative tables, audit,
`auth.*`, `agent.*`, the full Unit of Work, catalogue/eligibility services, partial-key resolution,
API routes, contract changes.

- **Derived policy v2.** `data/rules/derived-demo-policy.json` → `derived-demo-v2`: added
  `occupationIds` (R6) and `catalogueTaxKeys` (SRC-03, type `1` for CAR-01…CAR-20, tax year `2026`).
  Payroll flags, gross-up, rounding and limits unchanged (`payrollSemanticsChanged: false`). Rule:
  every policy change bumps the version and appends to `revisions`; the DB rejects a reused version
  string with different content. The supplied rules `version` is not enforced this way (§5.4 of the
  architecture: two editions share it); hashes identify the effective rules.
- **Manifest integrity.** All four sources are read once and must match `data/source-manifest.json`
  (SHA-256 and size). The PDF is checked for existence, hash and parser readability (5 pages); nothing
  is extracted from it.
- **CSV.** Strict cp1255; header (trimmed) must equal the 19 expected names in order; 19 fields per
  record; codes validated as strings; value of use as a plain decimal string → `Decimal`; date
  `dd/mm/yyyy`; drive code kept raw; duplicate complete key fails. Verified on the real file: 77,988
  records, one tax year (2026), no invalid value.
- **Workbook.** Sheets/columns by exact Hebrew names; codes must be text cells; money/years integer
  cells; `משפחתי` → family track; propulsion via the rules `driveField` labels. SRC-02 is reported as a
  warning. Example-employee and calculation sheets are not imported.
- **Traceability (R3).** Normalized values go to Domain objects and typed columns; the original source
  values (untrimmed CSV strings, original cells) and row numbers are stored in `source_values` /
  `source_row_number`; the original CSV header is stored per dataset version.
- **Consistency (BL).** `app/business/bl/reference_data/consistency.py`: every catalogue key exists in the
  dataset with the same value of use and the propulsion matching `driveField[drive code]` (catalogue
  vehicles only, SRC-04); family-track cars match the rules; no duplicate IDs. Failure →
  `SOURCE_CONFLICT` (a BL subclass of `DomainError`; Domain unchanged).
- **Publication.** Whole set validated first (no writes); then one transaction under an advisory lock:
  identical versions are reused, new ones inserted, a release row appended. Failure → rollback; the
  previous release stays active. **Active set = highest `release_seq`**; rules, catalogue and tax
  dataset are always read from the same release. All reference tables are insert-only (triggers reject
  UPDATE/DELETE/TRUNCATE). Historical versions remain readable by ID for future calculation snapshots.
- **Deviation (flagged, approved in plan).** `catalogue_versions` is unique on (workbook hash, policy
  hash) instead of the workbook hash alone, because the vehicle-type and occupation mappings live in the
  policy. FK names follow `fk_<table>_<column>` (PostgreSQL 63-character identifier limit).
- **Infrastructure notes.** psycopg async needs a selector event loop on Windows; entry points use
  `run_async`. The 77,988 value-of-use rows are written with `COPY` on the transaction's own connection
  (≈5 s import instead of ≈30 s). A separate migration URL (`STRAUSS_MIGRATION_DATABASE_URL`) exists; it
  is the same local dev user by default.
- **Verification (2026-10-17).** `STRAUSS_REQUIRE_DB=1` full suite on PostgreSQL 16.4: 341 passed,
  0 skipped (12 DB integration tests on `strauss_test`: migration round trip + model drift, UTF-8,
  complete import, idempotent re-import, complete-key lookups, byte-exact Hebrew/whitespace round trip,
  rollback, version activation, version-reuse rejection, immutability). Dev DB `strauss`: migrated to
  `0001_reference_data`, release 1 published, re-import `unchanged`.

## 10. Git integration (2026-10-17)

- The Business Module is integrated on branch `feature/business-backend`, created from `origin/main`
  (b5e8334) of `sari123456789/strauss-vehicle-advisor`; it is independent of `feature/frontend`. The
  original local folder is kept unchanged.
- The approved docs from `main` are authoritative (their internal references use the repository file
  names). `data/source-manifest.json` now distinguishes the immutable challenge `sources` (unchanged hashes,
  strict tests) from project-maintained `documents` (`sha256` = reviewed baseline on `main`,
  `originalSha256` = supplied edition, `revisionNote`). `.gitattributes` enforces LF for Markdown, so the
  document hashes are platform-independent.
- Not committed: challenge source data (`data/source/`, `data/src/`; redistribution not approved), the
  original-named document copies, virtual environments, caches and local settings. The test suite needs
  `data/source/` present locally.

## 11. Other pending items

- Publishing of challenge source files remains postponed until redistribution is approved.
- Originals under `data/src/` and the original-named documents are kept in the original local folder;
  removing them requires approval.

## 12. Package-layout refactor (approved 2026-10-18)

Scope: structural only (plan phases 0–5 and 7). No change to business logic, calculations,
eligibility, API routes, JSON contract schemas, migration history, source files or the
transaction model. No Git operations were performed by the refactor.

| Earlier package | Now |
|---|---|
| `app/domain/` | `app/business/domain/` |
| `app/business/` (BL) | `app/business/bl/` (`app/business/` is the module root) |
| `app/application/` | `app/business/application/` |
| `app/infrastructure/` | `app/business/infrastructure/` |
| `run_async` in `infrastructure/database/engine.py` | `app/runtime.py` (shared startup helper, D6) |
| — | `app/ai/{application,workflow,tools,infrastructure}/` (empty skeleton) |
| `tests/unit/{domain,business,application,infrastructure}/` | `tests/unit/business/{domain,bl,application,infrastructure}/` |
| `tests/integration/database/` | `tests/integration/business/database/` |

- **Architecture tests** (`tests/architecture/`): layer rules for the new prefixes; exact package
  layout of `app/`, `app/business/`, `app/ai/`; AI never imports `app.business` (tools see only
  `app.contracts`); no DB drivers outside module database infrastructure; no business-schema SQL
  strings in AI code; API used only by `app.main`; shared entry points import no BL/Domain/AI
  workflow; no import cycles (Tarjan SCC); every Application port implemented or explicitly
  pending; every business tool takes the server-injected `ToolContext`; no contract except
  `ToolContext`/`ActorRef` carries identity fields. Each rule was checked against a synthetic
  violation. Runtime authorization tests (foreign case, invented IDs) belong to 3B/4C.
- mypy strict scope: `app.contracts`, `app.business.domain`, `app.business.bl`,
  `app.business.application` (Infrastructure stays non-strict: openpyxl is untyped).
- Authoritative docs: a path-mapping addendum was appended to `architecture.md` and
  `integration-contracts.md`; their `sha256` in `data/source-manifest.json` was updated with a
  `revisionNote`.

Decisions recorded with the refactor plan:

| ID | Decision | Status |
|---|---|---|
| D1 | Separate least-privilege DB logins + Business Outbox instead of cross-schema transactions | Approved in principle; **not implemented**. Needs separate approval of the consistency model, failure windows, retries and recovery (proven by tests) |
| D2 | `DurableInputQueue` → AI module, `BusinessEventFeed` in contracts | Conditional on D1; port unchanged at `app/business/application/ports/input_queue.py` |
| D3 | Cross-schema FK `agent.conversations.case_id → business.cases` | To be verified by a real PostgreSQL permissions test before deciding |
| D4 | Event feed as a dedicated internal capability with runtime authorization | Approved (not implemented) |
| D5 | Migrations stay in `src/backend/migrations/` (Business-owned) | Approved |
| D6 | `run_async` in `app/runtime.py` | Approved, done |
| D7 | Tests mirror the package layout | Approved, done |
| D8 | DB role provisioning | Separate approval required before AI persistence |
| D9 | `auth.users` owned by Business Infrastructure | Approved |
| D10 | Refactor before Step 3A | Approved |

Constraints recorded with the approval: the AI module never receives SQL access or Business
Infrastructure objects; database credentials are never exposed to LangGraph nodes, tools or
model-controlled code; separate transactions are never described as cross-module atomic.

Fact corrections: Steps 0–2 (not only Step 0) were complete before the refactor; the baseline was
341 collected tests (337 passed, 4 skipped), not 140; the repository is under Git on
`feature/business-backend`.

## 13. Step 3A: persistence foundation (approved 2026-10-18)

Scope: migration `0002_business_cases`, Business ORM models and repositories, persistence
ports, `SqlUnitOfWork`, optimistic concurrency, idempotency, immutable calculation records and
demo seeding. No change to shared contracts, AI code, BL, source files or migration 0001.
D1/D2/D3/D8 remain pending: no Outbox, no separate DB roles, no AI persistence, no change to
cross-module transaction contracts (`UnitOfWork.input_queue` and `CaseLifecyclePort` unchanged).

Approved decisions (Q1-Q12) as implemented:

| ID | Implementation |
|---|---|
| Q1 | Facts documents (`employee_profiles.fields`, `cases.actual_facts`) are JSONB, schema version 1: exactly 16 fact paths + `schemaVersion`; each field `value, status, source, sourceMessageId, updatedAt, revision, lastConfirmedAt`. Missing is explicit (`null`/`missing`), never zero. Decimals are strings (`format(d, "f")`). Checked by the codec (`repositories/codec.py`), Domain invariants and `CHECK (business.is_valid_facts_document(...))`. Engine-wide JSON: serializer `allow_nan=False` and no implicit types; deserializer rejects non-integer numbers and NaN/Infinity. |
| Q2 | `BusinessUnitOfWork(UnitOfWork)` adds the repositories; Business-internal. `UnitOfWork` and the shared fake are unchanged. |
| Q3 | `operations`: `scope_kind` (`conversation`, `case`, reserved `representative_request`) + `scope_id uuid`; all contract operation IDs are UUIDs. |
| Q4 | `uq_reference_releases_lineage` (constraint only) + composite FK from `comparison_runs`. |
| Q5 | Extra columns as planned; composite FK `(case_id, option_result_set_id, reference_release_id)`. |
| Q6 | `reject_delete` (DELETE/TRUNCATE) on profiles, cases, alternatives, operations, representative requests; `reject_snapshot_mutation` on runs and result sets. |
| Q7 | `UnavailableInputQueue` raises `InputQueueUnavailableError` on every call; never accepts or drops an event. |
| Q8 | Option A with the owner's correction: seeded facts are `usable` for the demo, `source=employee` (propulsion: `catalogue`), `lastConfirmedAt=null`, `sourceMessageId=null` - never employee-confirmed. Internal provenance in `employee_profiles.origin` (`kind=demo_seed`, seed version, seed-file and workbook SHA-256, employee ref, `seededAt`); not in any contract. |
| Q9 | New Domain files `cases.py`, `representatives.py`, `json_values.py` (stdlib only). |
| Q10 | Runtime engine (`create_runtime_engine`, `bootstrap.build_business_persistence`) sets the PostgreSQL connection option `-c idle_in_transaction_session_timeout=15000` (setting `STRAUSS_IDLE_IN_TRANSACTION_TIMEOUT_MS`). PostgreSQL terminates a session idle inside an open transaction for longer than this (rollback, nothing committed). Executing statements do not count (verified: a 1.5 s `pg_sleep` under a 0.5 s limit completes); no `statement_timeout`. Import, migration and seed engines do not set it. |
| Q11 | Marker `sources` + `STRAUSS_REQUIRE_SOURCES=1` for the new source-dependent tests. |
| Q12 | Roadmap kept in `docs/plans/business-module-roadmap.md`. |

Further notes:

- `auth.users.login` is stored lowercase (CHECK `^[a-z0-9._-]{2,64}$`) with a plain UNIQUE
  constraint, instead of a unique index on `lower(login)` (same guarantee, drift-safe).
  The open-request uniqueness index uses `NULLS NOT DISTINCT` (PostgreSQL 15+) instead of
  `coalesce(alternative_id, nil-uuid)`. Composite indexes are ascending (`(case_id,
  created_at)`); btree scans serve newest-first queries.
- Constraint names: 0002 check constraints are named `ck_<table>_<name>`. Migration 0001's
  check constraints carry a doubled prefix (`ck_<table>_ck_<table>_<name>`) because the naming
  convention wraps the explicit name; this is pre-existing and left unchanged (0001 is frozen).
- Seed reader: openpyxl returns the workbook's non-integer percentages/credit points as binary
  floats; each is converted with `Decimal(repr(value))` only if it round-trips exactly and has
  at most 6 decimals (otherwise the seed fails). Money and counts must be integer cells. The
  fictional ID-number column is never read into seed records or stored. Current vehicles are
  mapped explicitly in `data/demo/demo-accounts.json` and verified against the catalogue name.
- Repository scoping (owner/case) supports Application authorization (3B); it is not a
  substitute. Triggers protect against application errors, not against owner/superuser roles
  (the local user is a superuser); role separation is D8.
- Verification (2026-10-18): `STRAUSS_REQUIRE_DB=1 STRAUSS_REQUIRE_SOURCES=1` full suite 492
  passed, 4 skipped (original-edition documents not in the clone); ruff, mypy, schema drift
  clean; dev DB migrated to `0002_business_cases`, `alembic check` clean, seed run twice
  (second run created nothing). Bridge test: seeded DB profiles + stored reference data ->
  existing BL reproduce Agent Spec §8 for E-001..E-006 (E-003 -1939, E-006 +722).

## 14. Step 3B: Business Application services (approved 2026-10-18)

D1 (approved direction): separate least-privilege DB identities for Business and AI, no shared
transactions, Business Outbox for cross-module events, recoverable idempotent coordination for
conversation/case creation. Documented in `architecture.md` / `integration-contracts.md`
("D1 module isolation"). Outbox, AI persistence and role provisioning (D8) are not implemented;
D3 (cross-schema FK) is still pending.

Contract changes (Python only; **no exported JSON Schema changed**):

| ID | Change |
|---|---|
| C1 | `CaseLifecyclePort.create_case_for_conversation(actor, command) -> Envelope[CreateCaseResult]`; `TransactionHandle` removed |
| C2 | Business `UnitOfWork.input_queue` removed (and the 3A placeholder `UnavailableInputQueue`) |
| C3 | `DurableInputQueue` moved to `app/ai/application/ports/input_queue.py` (provisional); fakes to `tests/fakes/ai/` |
| C4 | New provisional `app/contracts/business_events.py` (`BusinessEvent`, `BusinessEventFeed`); not exported |

Decisions B1–B9: token authentication deferred to 4D (B1); missing profile = all-missing at
version 0, first actual update inserts version 1 (B2); unchanged values are no-ops, never
reconfirmation (B3); hypothetical `clear` removes the override (B4); no close-case operation,
closed cases read-only (B5); `CaseLifecyclePort` returns `Envelope` (B6); foreign conversations and
cases are `NOT_FOUND` without details (B7); `ReferenceDataRepository.get_rules` (B8); services tested
on real PostgreSQL, unit fakes only for the policy (B9).

Services (`app/business/application/`): `identity/` (`IdentityService`, `revalidate_actor`,
`AuthorizationPolicy`), `profiles/profile_service.py`, `cases/` (`case_service.py`, `changes.py`,
`mapping.py`, `freshness.py`, `alternatives.py`, `lifecycle_adapter.py`), `errors.py`, `ports/clock.py`.
Every protected operation revalidates the actor against `auth.users` inside its Unit of Work
(roles from the store, never from the presented `ActorRef`), then checks ownership; this also
applies to worker-originated calls.

Note: the 3B service tests live in `tests/integration/business/database/` (not a separate
`application/` directory) to reuse the DB fixtures and the `db` marker.

## 15. Step 4A: eligibility, catalogue and valuation + contract changes G1–G5 (approved 2026-10-18)

Status: implemented, awaiting review. The shared contract changes are **not yet synchronized**
with the AI developer and must not be committed before that synchronization.

Contract changes (exported JSON Schemas: 5 new, `CaseContext`/`ReadinessResult`/`index.json`
changed, the other 25 byte-identical):

| ID | Change |
|---|---|
| G1 | New tool `get_representative_request(ctx, RepresentativeRequestLookup) -> Envelope[RequestStatusView]`; only within the bound case; no Outbox |
| G2 | New tool `resolve_occupation(ctx, ResolveOccupationRequest) -> Envelope[ResolveOccupationResult]` (+ `OccupationCandidate`) |
| G3 | New tool `confirm_profile_fields(ctx, ConfirmProfileFieldsRequest) -> Envelope[ConfirmProfileFieldsResult]` |
| G4 | `CaseContext.fieldsRequiringConfirmation: list[FieldPath]` (default empty) |
| G5 | `PendingRepresentativeField.fieldCode: RepresentativeFieldCode` (was a free string) |

`BusinessToolsPort` therefore has 13 tools; `CANONICAL_TOOL_NAMES` lists them.

Decisions A1–A12 as implemented:

- **A1** A free-text query never resolves a vehicle; even a unique candidate is returned as
  `ambiguous` for confirmation. Catalogue ID or complete key -> `resolved`/`not_found`.
- **A2** Query = case-insensitive substring of the trimmed commercial name (`כינוי מסחרי`), plus
  exact code filters; ordered by key; at most 20 candidates (`truncated`). LIKE wildcards in the
  query are literal. With codes in the query, `TAX_KEY_AMBIGUOUS` when several candidates match
  or when the given codes alone match several official records (e.g. ATTO 3 / TANG share
  01014/00003/2025 and differ only by vehicle type, T25); otherwise `AMBIGUOUS_INPUT`.
- **A3** Occupation/group conflict: eligible only if eligible under both the reported and the
  table group; ineligible only if ineligible under both; otherwise `needs_information` with
  `occupation_group_conflict`. The reported group is never overwritten.
- **A4/A5** Effective facts = latest shared actual profile + this case's hypothetical overrides.
- **A6** Listing shows eligible and `needs_information` items, ineligible vehicles are omitted;
  the waiver item is shown whenever `waiver` is requested (also when ineligible). A category with
  a `needs_information` item is in `uncheckedCategories`; `scopeComplete` only without them
  (envelope `partial`). Missing facts are reported once per field. Filters: `propulsion` on all
  vehicles; `groups` on regular vehicles only (the family track is not a ranked group).
- **A7** Unknown catalogue ID / key for a value or eligibility check -> `NOT_FOUND` with a
  `VEHICLE_NOT_FOUND` issue (`BusinessError.issues`, carried into the error envelope).
- **A8** An occupation that is neither mandatory-car nor waiver-allowed, or an unsupported
  rules policy, fails explicitly (`UnsupportedPolicy`, `INFRA_UNAVAILABLE`).
- **A9** A closed case cannot list options (`INVALID_STATE_TRANSITION`); reads stay allowed.
- **A10** No new index (ILIKE over 77,988 rows measured at ~46 ms).
- **A11** `purpose=alternative` is restricted to catalogue vehicles; a valid key outside the
  catalogue -> `not_found` + `UNSUPPORTED_INPUT`/`not_in_catalogue` (T29). The current car may
  be outside the catalogue (T28).
- **A12** Identical listing retries do not multiply result sets: the request JSON of a result
  set stores `inputFingerprint` = SHA-256 over (case version, latest profile version, release,
  request); `OptionResultSetRepository.add_or_get_identical` takes a transaction-scoped advisory
  lock per (case, fingerprint) and returns the existing immutable set. No new contract field
  or public operation ID; no migration.

G-rules as implemented:

- **G2** Exact official ID (whitespace/case ignored) or exact normalized official name (NFC,
  collapsed whitespace, casefold) resolves; for names like "מפתח/ת תוכנה" both written forms
  ("מפתח תוכנה", "מפתחת תוכנה") are exact. Substring or whole-word overlap -> `ambiguous`
  candidates; no query -> all occupations, `ambiguous`; otherwise `not_found`. No synonyms.
- **G3** Only usable fields are confirmed: `lastConfirmedAt = now`, `source = employee`,
  `sourceMessageId`, revision + 1; value and `updatedAt` unchanged. Missing/unclear fields are
  returned in `notConfirmable` (`MISSING_INPUT`/`AMBIGUOUS_INPUT`). Profile CAS then case CAS in
  one Unit of Work; idempotent (operation scope = case, type `confirm_profile_fields`); a closed
  case is read-only. Nothing confirmed -> no write, versions unchanged.
- **G4** A usable fact requires confirmation when neither `updatedAt` nor `lastConfirmedAt` is at
  or after the case's creation. Informational only: no blanket confirmation at conversation
  start, only facts relevant to the current goal are asked (listing vehicles never needs salary
  confirmation), no time-based expiry; distinct from stale business data and stale runs.

Services (`app/business/application/`): `vehicles/` (`catalogue_service.py` — `CatalogueService`;
`valuation_service.py` — `ValuationService`; `facts.py`; `mapping.py`), `occupations/`
(`OccupationService`), `representatives/` (`RepresentativeRequestService`),
`cases/confirmation.py` (G3/G4 rules; `CaseService.confirm_profile_fields`), `outcomes.py`
(`Outcome[T]`; the tools facade of 4C turns it into an `Envelope`), `reference.py` (pinned
release). BL: `bl/eligibility/` (group, family, waiver policies, evaluator); Domain:
`domain/eligibility.py`. Ports: `ReferenceDataRepository.list_occupations`,
`get_tax_record_details`, `find_tax_records`; `OptionResultSetRepository.add_or_get_identical`.
Every public service method takes the server-built `ToolContext` first and authorizes through
`AuthorizationPolicy.tool_case` before reading business data.

## 16. Step 4B: calculation readiness and scenario comparisons (approved 2026-10-18)

Status: implemented without shared-contract changes, awaiting review. **Q1 is paused** until
shared-contract approval, so readiness evaluates regular calculations only.

### What was added
- **Services** (`app/business/application/comparisons/`): `ReadinessService.check_calculation_readiness`
  and `ComparisonService.compare_scenarios` / `get_comparison_details`. Each takes the
  server-built `ToolContext` first.
- **Supporting modules:** `planner.py` (shared by readiness and comparison), `facts.py`,
  `selection.py`, `mapping.py`, `snapshot.py`.
- **BL:** `bl/readiness/assessor.py`. It reuses the existing builder and component functions
  and adds no rule.
- **Domain:** `domain/readiness.py`.
- **Ports:** `EmployeeProfileRepository.lock` and `CaseRepository.lock` (`FOR UPDATE`).
- **Unchanged:** the payroll formulas, builder, gross-up and display rounding. No migration.

### Decisions as implemented

**Q1 (implemented 2026-10-20 as E2, see §20).** The proposal was `ReadinessRequest.kind: RunKind = "regular"`, which is optional
and backward compatible. The planner already takes the fact basis; only the contract field is
missing.

**Q2: baseline reliability.**
- The contracts have no marker for a conditional estimate: `ScenarioResult.delta` has no flag,
  and `Assumption` is run-level. An unreliable baseline is therefore not calculated and every
  delta is null. Independent alternatives are still calculated.
- A baseline is unreliable when a material current-arrangement fact is any of:
  - missing or unclear;
  - not found;
  - in contradiction with the catalogue (`SOURCE_CONFLICT`);
  - blocked by an open representative request;
  - usable, but neither updated nor confirmed in this conversation (G4).
- The last case is reported as an issue `AMBIGUOUS_INPUT` with `reason="confirmation_required"`.
  It is **not** listed in `missingEmployeeFields` or `ambiguousFields`; `baselineReady=false`.
- Material facts: `kind`, then either `allowance`, or `vehicle` + `participation`. Add
  `propulsion` for a car outside the catalogue. On the family track, add the inclusion flag,
  plus the family deduction when it is not included.
- Salary facts never require confirmation.

**Q3: the current car.**
- Catalogue car: the catalogue's track and propulsion. A different reported propulsion is
  `SOURCE_CONFLICT`.
- Car outside the catalogue: regular track and the reported propulsion (D6). A reported family
  deduction is treated as unclear.

**Q4.** An unknown option key returns `NOT_FOUND` + `VEHICLE_NOT_FOUND`, and nothing is
persisted.

**Q5/Q7: consistent facts.**
- One request uses one effective fact set and one pinned release for eligibility, readiness and
  calculation.
- Fact bases:
  - `actual`: regular runs; the case copy is synchronized to the latest profile first;
  - `case_copy_with_overrides`: hypothetical runs, with a fixed baseline;
  - `latest_with_overrides`: listing (A4/A5).
- A stored option list contributes only its keys and their order; eligibility is always
  re-evaluated.
  - List from another release: `VERSION_CONFLICT` + `STALE_REVISION` / `result_set_release_changed`.
  - List built from other facts: `STALE_REVISION` / `result_set_built_from_other_facts`, and per
    option `eligibility_changed_since_list`.
- Lists store their `factBasis`, which is also part of the dedupe fingerprint.
- Facts count as the same when the case and profile versions match and overrides are applied in
  both or in neither (or the case has none).

**Q6.** An open request tied to an option blocks only that option (`PENDING_REPRESENTATIVE`). An
open `currentArrangement.*` request blocks only the baseline.

**Q8: assumption codes.** All are verified against the sources:
- `participation.catalogue_default`: per calculated vehicle (agent-spec §2.6).
- `demo_policy.family_participation_base_plus_gap`: per calculated family vehicle, when the
  derived policy says so.
- `demo_policy.payroll_tables_<rules tax year>_vehicle_data_<key tax year>`: only when the years
  differ (SRC-06). Currently `..._2025_vehicle_data_2026`, the code the fake already uses.

**Q9: input snapshot v1.** Contents:
- `schemaVersion` and `factBasis`;
- the month and reference date;
- the case and profile versions used, and the source versions;
- every fact with its provenance, and the overrides applied;
- the resolved selection;
- per-scenario components and blockers, and the baseline;
- the issues.

It is internal JSON inside `ComparisonDetails.inputSnapshot`; no schema change.

**Q10: locks.**
- Order: operation claim, then profile, then case (`FOR UPDATE`). This is the same order as
  every profile + case write.
- Versions are re-checked under the lock, and there is no lock upgrade. (The earlier
  `FOR SHARE` idea could deadlock on the regular synchronization.)
- A concurrent write waits for the comparison, and then makes the run stale (T42).
- A profile row that does not exist yet cannot be locked. A run created meanwhile is reported
  stale on read.

**Q11.** `expectedProfileVersion` is required for regular runs and must equal the latest
version. For hypothetical runs it is optional; when given, it must equal the case copy's
version.

**Q12.** `all_eligible` defaults to regular + family; the waiver is included only when
requested. The list is persisted and linked to the run (`option_result_set_id`).

**Family listing.**
- Readiness for `list_family` needs only the qualifying-child count. Family eligibility does not
  depend on group ranking (4A).
- The group is needed only to calculate a family alternative, because F uses the group budget.
- Architecture §14 and agent-spec §6 list the group as a prerequisite for family listing. Per
  the owner's instruction, listing and calculation requirements are kept separate.

### Envelope status
- **Comparison:** `ok` when every requested item is calculated or ineligible; `partial` when some
  are calculated; otherwise `needs_information`.
- **Readiness:** `ok` when the scope is complete; `partial` when some scenarios are ready.

## 17. Step 4C: `BusinessToolsFacade` and representative questions (approved 2026-10-18)

Status: implemented with the shared contracts exactly as defined in the Business branch, with
no contract change; awaiting review. Q1 remains deferred.

### What was added
- **Facade:** `app/business/application/tools_facade.py`, class `BusinessToolsFacade`. It
  implements `BusinessToolsPort` for all 13 canonical tools.
  - It is a thin adapter: it calls the Application services and turns each `Outcome` or
    `BusinessError` into a contract `Envelope`.
  - It holds no business logic.
  - Every method takes the server-built `ToolContext` first. `traceId` is `ToolContext.trace_id`.
  - Bootstrap exposes it as `BusinessServices.tools`.
- **Representative questions (employee side):** `representatives/question_service.py`,
  `create_representative_question`.
  - **Authorization:** through the server-bound tool context. A closed case is read-only. A
    stale `expectedCaseVersion` is `VERSION_CONFLICT`. The case does not change.
  - **Scope allowlist:**
    - `alternative.participation` requires a catalogue vehicle option, which is persisted as the
      case's alternative;
    - `occupation.waiverPolicy` takes the waiver or no option;
    - `employee.entitlementGroup` and `currentArrangement.*` take no option.
  - **Idempotency:** `idempotencyKey` acts as the operation ID. The same payload returns the
    stored request with `created=false`; a different payload is `OPERATION_PAYLOAD_MISMATCH`.
    An equivalent open request (same case, alternative, field and subject) is reused (T34).
  - **Subject snapshot:** minimal context only (D11). `{"catalogueId"}`, `{"occupationId"}`, or
    `{"currentVehicle"}`; never salary.
  - **`expectedValueType`:** follows the field code one to one: money, boolean,
    entitlement_group, propulsion, tax_key. PROVISIONAL (api-contracts §8 open item).
  - **Blocking:** a pending question blocks only the dependent option, or only the baseline for
    `currentArrangement.*` (4B Q6). The conversation and every other tool stay usable.
- **T61:** a port-level `ReferenceDataUnavailableError` is mapped to `INFRA_UNAVAILABLE` with
  `safe_details.reason="reference_data_unavailable"`.
  - No numeric result is returned and nothing is persisted, not even the operation. A retry with
    the same operation ID then works.
  - It is covered deterministically by a test double. The production code paths are unchanged.

### Envelope mapping
- **Success:** the service `Outcome`, carrying its status, issues, versions and sources.
- **`update_conversation_inputs`:** `ok`, with conflicts and missing information as issues.
- **`confirm_profile_fields`:**
  - `ok` when every field was confirmed;
  - `partial` when some were;
  - `needs_information` when none were (`notConfirmable` as issues).
- **`BusinessError`:** `status=error`, with the stable code, safe details and any issues.
- **Unexpected exception:** `status=error`, `INFRA_UNAVAILABLE`, no details.
  - The log records the tool name, the exception type and the trace ID only (no arguments or
    salary data).
  - **Open for joint review:** the contract has no internal-error code.

### Outbox and business event feed
No responsibility is assigned to 4C. architecture.md ("D1 module isolation") and
integration-contracts assign the Outbox and the feed to the representative-answer stage.
Nothing was implemented.

## 18. Step 4D (Business-owned part): HTTP API, authentication and sessions (2026-10-18)

Status: implemented as **isolated Business components**, awaiting review. The shared
`app/main.py`, `app/bootstrap.py` and `app/settings.py` are **not** changed; integration is the
coordinated step described below. No shared contract or DTO was changed, and Q1 remains deferred.
Not implemented: AI conversation endpoints, SSE, and workers.

### What was added

**Authentication (architecture §22):**
- **Ports:** `application/ports/credentials.py` defines `PasswordHasher`, `AccessTokenCodec`,
  `CredentialStore` and `RefreshSessionRepository`.
- **Service:** `application/identity/auth_service.py`, `AuthService`, with `login`, `refresh`,
  `logout` and `authenticate`.
  - **Login:** a failure is a uniform `UNAUTHENTICATED`. An unknown login still pays the full hash
    cost, and repeated failures are throttled (`RATE_LIMITED` with `Retry-After`, an in-process
    window).
  - **Access token:** 15 minutes. It carries only the subject and the session family; roles are
    re-read from `auth.users` on every request, with `Origin.HTTP`.
  - **Refresh token:** 8 hours and one-time use. Rotation happens under a row lock; reusing a
    rotated token revokes the whole family. Logout revokes the family immediately.
- **Operator path:** `application/identity/credential_admin.py`, `CredentialAdminService`. It
  works only for the import operator from the CLI, and requires at least 12 characters.
- **Infrastructure:** `infrastructure/security/password_hasher.py` (`hashlib.scrypt`, salted, with
  constant-time verification) and `infrastructure/security/token_codec.py` (`Hs256TokenCodec`).
  - The codec is standard library only: PyPI was not reachable (TLS interception,
    `UnknownIssuer`), so PyJWT could not be added.
  - It accepts a single fixed header, so `alg=none` and algorithm swaps are rejected. It uses a
    constant-time signature compare, and checks `iss`, `aud`, `typ`, `exp` and `iat`.
  - It requires a secret of at least 32 bytes. PyJWT can replace it behind the same port.
- **Storage:** `repositories/credentials.py`, the ORM rows in `models/identity.py`, and a new
  migration `0003_auth_credentials`.
  - It adds `auth.credentials` and `auth.refresh_sessions` and is additive only: 0001/0002 are
    unchanged.
  - Only hashes are stored: scrypt for passwords, SHA-256 digests for refresh tokens.
- **CLI:** `app/cli/set_password.py`. The password comes from `STRAUSS_NEW_PASSWORD` or a hidden
  prompt, and is never printed. No password is in the repository.

**Trusted context:** `CaseService.context_for_case(actor, case_id, trace_id) -> ToolContext`.
- The actor comes only from the verified token. The case must be owned, else `NOT_FOUND`.
- The conversation is taken from the stored case, so HTTP passes the same checks as the tools.

**HTTP API** (`app/api/business/`), installed with `install_business_api(app, api)`:
- **Routes:**
  - `/api/v1/auth/{login,refresh,logout,me}` and `/api/v1/me/profile`;
  - `/api/v1/cases/{caseId}`: `GET` and `PATCH`, plus `/profile-confirmations`, `/option-lists`,
    `/vehicle-resolutions`, `/vehicle-tax-values`, `/eligibility-checks`,
    `/occupation-resolutions`, `/readiness`, `/comparisons`, `/comparisons/{runId}`,
    `/representative-requests` and `/representative-requests/{requestId}`.
- **Facade integration:** each case route calls `BusinessToolsPort` (the 4C facade) with the
  shared request DTOs unchanged. There is no case-creation route; cases come with
  AI-owned conversations.
- **Responses:** success is 200 with the contract `Envelope`, including missing information or
  ineligibility. Errors are Problem Details (`application/problem+json`; provisional HTTP DTO):
  `type`, `title`, `status`, `code`, `traceId`, `details`, `issues`.
- **Status mapping:**

  | Code | HTTP |
  |---|---|
  | `MALFORMED_REQUEST` | 400 |
  | `UNAUTHENTICATED` | 401, with `WWW-Authenticate` |
  | `FORBIDDEN` | 403 |
  | `NOT_FOUND` | 404 |
  | version, payload or state conflicts | 409 |
  | invalid input or range | 422 |
  | `RATE_LIMITED` | 429 |
  | `INFRA_UNAVAILABLE` | 503 |
  | `INFRA_UNAVAILABLE` with `reason=internal_error`, or anything unexpected | 500, no details |

- **`BusinessRoute`:** scoped to the Business routers only.
  - It sets a trace ID (from `X-Request-ID`, or generated) and echoes it.
  - Validation errors list field locations and types, never the submitted values.
  - It adds `Cache-Control: no-store`, `nosniff` and `no-referrer`.
- **Cookies and CSRF:** the refresh cookie is `HttpOnly; Secure; SameSite=Strict;
  Path=/api/v1/auth`. Refresh and logout require `X-CSRF-Protection: 1` and an allowed `Origin`,
  when one is sent.
- **OpenAPI:** `business_openapi()` builds the document. It is exported to
  `contracts/openapi/business-api.json` by `tests/contract/business_openapi.py`; the API may be
  imported only by `app.main`, so the exporter lives on the test side. `test_business_openapi.py`
  fails on drift and checks that every non-auth route is bearer-protected.

### Integration (coordinated change to shared files; not applied)

1. **`settings.py`:**
   - `auth_token_secret: SecretStr` (required, at least 32 bytes; startup fails without it
     outside development);
   - `auth_allowed_origins: list[str]`;
   - `auth_cookie_secure: bool = True`.
2. **`bootstrap.py`:** build
   `AuthService(uow, ScryptPasswordHasher(), Hs256TokenCodec(secret, issuer=..., audience=...), clock)`
   and `BusinessApi(auth=..., tools=services.tools, cases=services.cases,
   profiles=services.profiles, http=HttpConfig(...))`.
3. **`main.py`:**
   - `install_business_api(app, business_api)`;
   - `CORSMiddleware` for the Angular origin, with credentials and the `X-CSRF-Protection`
     header;
   - dispose the engine on shutdown.
4. **Deployment:** add `uvicorn` as a dependency, run behind HTTPS, and limit request sizes at
   the proxy.

### Open items for joint review
- The HTTP route names and the Problem Details shape (D4D-1/3).
- SSE authentication (EventSource cannot send `Authorization`, D4D-7).
- The ownership of `main.py` / `bootstrap.py` (D4D-8).
- The `origin/feature/frontend` check (D4D-9, needs approval for a read-only Git command).
- The throttle is in-process (single API instance in the MVP).

## 19. Step B3a: representative answers, case-local facts and the Business outbox (approved 2026-10-20)

Status: implemented in the Business track, awaiting review. **Not** included: HTTP routes (B3b),
the waiver allowance (B4), bootstrap/`main.py` wiring, and any AI code. No shared DTO or enum
changed.

### Approved decisions as implemented
- **Q1, case-local answers:** an applied answer changes only the case it was asked in; the
  shared EmployeeProfile is never changed.
  - `alternative.participation`: written to that alternative's `inputs.participation` (decimal
    string, ILS, `source=representative`, request ID, answer time, effective dates). Alternative
    version + 1.
  - `employee.entitlementGroup`, `currentArrangement.propulsion`: written as an insert-only
    `business.case_representative_facts` row.
  - Facts survive regular synchronization, which replaces only `cases.actual_facts`.
- **Effective dates:** an answer applies only while its inclusive, optional period covers
  `case.reference_date`. Otherwise it is saved as `not_applicable` / `outside_effective_period`
  and not used (AC30, T36/T37).
  - A participation answer for an alternative that is inactive, or changed since the question,
    is `not_applicable` / `alternative_changed`.
  - When several facts exist for a field, the latest applicable one wins. An expired fact
    never hides another value.
- **Precedence:** a hypothetical override (where overrides apply) wins over an applicable
  representative fact, which wins over the employee-reported value. Precedence is computed in
  `application/representatives/case_answers.py` and applied by every fact basis (compare,
  readiness, listing, eligibility).
- **Source conflict:** an applicable representative fact, a usable employee-reported value of
  the same field in the same basis that differs from it, and no applied hypothetical override.
  - It is reported as `SOURCE_CONFLICT` / `representative_fact_differs`, with the request ID.
  - It is informational: readiness is unchanged and calculations proceed with the
    representative value.
  - An actual employee update to such a field is still saved to the profile and reports the
    same issue.
- **`cannot_determine`:** accepted for every field code; nothing is applied.
  - It keeps the request's own option uncalculated (`MISSING_INPUT` /
    `representative_cannot_determine`, AC33), but only while that alternative is unchanged
    since the question.
  - Case-level requests stop being pending.
- **Q2/Q3, deferred codes:** a value for `occupation.waiverPolicy` or
  `currentArrangement.vehicle` is rejected (`INVALID_BUSINESS_INPUT`, `safeDetails.reason =
  field_code_not_answerable_yet`). Nothing is stored and the request stays open.
- **Q4, demo-only visibility:** any active `representative` lists, reads and answers every
  request, with the role revalidated on every call.
  - The view is the minimal `RepresentativeRequestView`: the vehicle context, plus the
    entitlement group for participation questions only.
  - It never contains identity, salary, profile, case or conversation IDs.
  - Employees and other roles get `FORBIDDEN`.
- **Value formats:**
  - money: `^\d{1,9}(\.\d{1,2})?$`, unit `ILS` or omitted, at most the published
    `max_monthly_amount`;
  - group: one of `א ב ג ד`;
  - propulsion: a contract propulsion value;
  - no unit outside money.
- **Versions:** every committed answer increments the request version and the case version, so
  earlier runs become stale and their stored snapshots are kept.

### Transactions and lock order
Global order for every operation that holds more than one lock: operation claim, then
employee profile, then case, then representative requests, then alternatives. No earlier lock
is taken after a later one.

| Path | Locks, in order |
|---|---|
| `update_inputs` (actual) / `confirm_profile_fields` | claim, profile (UPDATE CAS), case (UPDATE CAS) |
| `update_inputs` (hypothetical) | claim, case (UPDATE CAS) |
| `compare_scenarios` | claim, profile `FOR UPDATE`, case `FOR UPDATE`, alternatives, result-set advisory lock |
| `create_representative_question` | claim, case `FOR UPDATE` (**added in B3a**), alternative, request |
| `submit_answer` (new) | unlocked request read (to find the case), claim, case `FOR UPDATE`, request `FOR UPDATE` (status and version re-checked), alternative CAS, inserts |
| `list_eligible_options` | no row lock; result-set advisory lock (its key never matches a comparison's, because the fact basis is part of the key) |
| `deactivate_alternative` | not used yet; documented to require the case lock first |

- **Retries:** a PostgreSQL deadlock (`40P01`) or serialization abort (`40001`) leaves the
  Unit of Work as `TransientConflictError`, with everything rolled back. `submit_answer` retries
  the whole idempotent operation at most 3 times, then returns `INFRA_UNAVAILABLE` /
  `transient_conflict`. Version conflicts are never retried.
- **No external calls under locks:** no LLM call, network request or user wait happens while
  a transaction is open.

### Outbox and delivery (D1)
- **One transaction:** the answer, request status, any case change (alternative inputs or
  case fact), case version, audit entry, outbox event and operation completion are committed
  together.
- **Stable event:** the `event_id` is generated once in that transaction. A replay of the same
  `operationId` returns the stored answer and adds no event.
- **Payload:** `RepresentativeAnsweredEvent` v1, with identifiers and versions only.
- **`SqlBusinessEventFeed` (`infrastructure/database/event_feed.py`):**
  - `pending(limit)` returns unacknowledged rows in `sequence` order, and keeps returning them
    until they are acknowledged;
  - `acknowledge(ids)` sets `acknowledged_at` once; it is idempotent, and unknown IDs are
    ignored;
  - a trigger rejects every other update and every delete;
  - it never touches AI tables.
- **Not wired yet:** the AI worker's consumption and the bootstrap injection.

### Migration `0004_representative_answers` (additive)
- **New tables:**
  - `representative_answers`: one per request, immutable;
  - `case_representative_facts`: values checked by `is_valid_fact_value`, immutable;
  - `integration_outbox`: identity `sequence`, acknowledge-only updates;
  - `audit_entries`: minimal, immutable, no read API.
- **Constraint added to an existing table:** `uq_representative_requests_case_id_id` on
  `representative_requests`, for the composite FKs that keep an answer or fact inside its
  request's case.
- **Functions:** `reject_record_mutation()` and `outbox_acknowledge_only()`.
- **Downgrade:** removes all of the above.

### Coordination still required (not done in B3a)
- **`bootstrap.py` (shared):** construct `RepresentativeAnswerService(uow, policy, reference,
  clock)` and `SqlBusinessEventFeed(sessions)`; inject the feed only into the AI worker
  (decision D4).
- **AI track:** the worker pulls `pending`, enqueues each event deduplicated by `eventId`, and
  only then calls `acknowledge`.
- **B3b:** representative HTTP routes over `list_requests`, `get_request` and `submit_answer`
  (after decision R).

## 20. Full-system integration (2026-10-20, approved plan M1–M6 + M3B)

Branch `feature/full-system-integration` (from `origin/develop` `20e9361`). Single-developer
phase; the §19 coordination items are done. Progress and test results:
`docs/task-progress.md`.

### Shared contract changes (explicitly approved)

**E1 — `BusinessEventFeed.pending(limit, *, after_sequence=None)`.**
- **Compatibility:** it is additive, and the earlier calls (`pending(500)`) are unchanged.
- **Implementations:** `SqlBusinessEventFeed` adds `WHERE sequence > after_sequence ORDER BY sequence`.
- **Cursor semantics:** the cursor is per scan only. Every scan restarts from `None`, because
  sequences may become visible out of order. Its only purpose is to let the relay page past
  events it cannot accept yet.

**E2 — `ReadinessRequest.kind: RunKind = "regular"`.**
- It is the former Q1. Readiness now uses the same fact basis as `compare_scenarios`
  (`basis_for(kind)`).
- The agent passes its goal kind.
- An omitted kind keeps the previous behavior.

**Not approved (E3):** a representative answer for the waiver or allowance of an occupation that
is outside the occupation table. There is no authoritative rule, and it would need a contract
change and a migration. Such answers remain rejected (`field_code_not_answerable_yet`, Q2).

### Composition (M1)
- **One FastAPI app** (`app.main.create_app`). Its lifespan builds, in order:
  1. Business services;
  2. `BusinessApi` (`AuthService` with HS256, issuer `strauss`, audience `strauss-api`);
  3. the AI runtime over the shared ports;
  4. a read-only check of both migration heads and the checkpointer tables;
  5. optionally, the worker and the relay.
- **Shutdown** is ordered and safe after a partial startup.
- **Settings:**
  - `STRAUSS_AUTH_TOKEN_SECRET` is required outside development/test; otherwise a random
    per-process secret is used.
  - `STRAUSS_AUTH_ALLOWED_ORIGINS` (CSRF origins and CORS), `STRAUSS_AUTH_COOKIE_SECURE`,
    `STRAUSS_AUTH_COOKIE_SAMESITE` (`none` requires `Secure`).
  - `STRAUSS_WORKER_IN_PROCESS` (one instance) and `STRAUSS_LOG_LEVEL`.
- **Readiness** (`/api/v1/health/ready`) returns booleans and counters only; when not ready, it
  returns 503 Problem Details.
- **Deployment step:** `python -m app.cli.setup_checkpointer`. The API never migrates or creates
  tables by itself.

### Representative routes (M2, B3b)
- **Routes:** `GET /api/v1/representative-requests` (repeatable `status`, `limit`),
  `GET /{requestId}`, and `POST /{requestId}/answers`.
- **Shapes:** they reuse the existing `RepresentativeRequestView`, `AnswerSubmit` and `AnswerView`
  in the usual `Envelope`, with status 200.
- **Errors:**
  - a second answer is 409 `INVALID_STATE_TRANSITION`;
  - a stale version is 409 `VERSION_CONFLICT`;
  - an employee gets 403, and an anonymous caller gets 401.

### Outbox → AI relay (M3)
- **Component:** `app/ai/application/event_relay.py` (`BusinessEventRelay`).
- **Delivery:**
  - An event is acknowledged only after `enqueue_representative_answered` committed. A duplicate
    is also acknowledged, because the queue deduplicates by `uuid5(event_id)`.
  - A permanent per-event failure (no conversation, or a payload mismatch) is never acknowledged.
    It backs off per event (30 s, 2 min, 10 min, then 1 h) and is quarantined after 10 attempts
    (hourly retries, ERROR log). It stays in the outbox, so it is recoverable.
  - Transient failures back off globally (1 s, 5 s, 30 s, 2 min, then 5 min).
- **Separation:** delivery retries are independent of turn retries (`agent.inputs`).
- **Runtime:** the relay runs with the in-process worker. Its counters appear in `info.relay`.

### B4 — waiver and maintenance allowance (M3B)
- **Already implemented and authoritative:**
  - The data comes from the workbook sheet "עיסוקים": 14 occupations, each with a mandatory car
    flag, whether waiver is allowed, and an allowance of 1,200 / 1,500 / 2,000 / 2,600 / 3,000.
  - The allowance comes from the occupation, never from the entitlement group.
- **Verified end to end over HTTP and in a conversation:**
  - E-005: waiver gives "17806" with allowance "2000".
  - E-003: MG4 gives "13109" with no residual allowance.
  - T21: a mandatory-car occupation is ineligible.
  - T23: an occupation outside the table is not resolved, and setting it is refused with 422.
- **Partial (E3):** only the representative-answer path for occupations outside the table.

### Contracts and smoke (M4)
- `contracts/openapi/ai-api.json` is exported from the conversation routes, with a drift test.
- `scripts/smoke.py` runs against any base URL. It also runs in the test suite against the real
  app over ASGI.
- **Fix:** `GeminiProviderClient` keeps the `genai.Client` alive. Before this, a
  garbage-collected client closed its HTTP client, and later calls failed.

### Angular (M5)
- **Types and errors:** types are generated from both OpenAPI documents (`npm run api:types`),
  with no hand-written DTOs. Problem Details codes map to Hebrew text, and raw codes are never
  shown.
- **Session:** the access token is held in memory. The refresh cookie is sent with the CSRF
  header, and the session is restored after a reload. A 401 triggers one refresh and a retry.
  Role guards protect the routes.
- **Same origin:** `ng serve` proxies `/api`, and Vercel rewrites it (runbook §3).
- **Screens:**
  - The chat uses polling (decision P), with resend after a failed turn.
  - The profile is read-only; corrections go through the chat.
  - The catalogue and eligibility come from `option-lists`; the browser rule was removed.
  - The comparison and calculation pages show Business `display` values only.
  - The history is rebuilt from conversations and stored runs (`recentRuns`, newest 20 per case).
  - The representative answer form is structured.
- **Removed:** demo stores, localStorage data and fixed amounts.

### Known limitations
- **Gemini key:** the configured key is rejected by Google (403 PERMISSION_DENIED, "project denied
  access"). The automated tests use deterministic models. A valid billing-free AI Studio key is
  needed for the manual Gemini check.
- **Pre-existing visual test failure:** `home.visual` "logo: top" (11px against 4px). It is caused
  by the logo change in `b0411be` and also fails on `develop`.
- **History limit:** at most 20 runs per case appear in the history (`RECENT_RUNS`).

## 21. Representative claiming (requested 2026-10-21; implemented on `feature/representative-claiming`, pending review)

- **Status:** requested by the developer for the demo. The repository contains no approved claiming
  plan, so this section is not an approved business decision until it is reviewed here.
- **Rules:** an open, unclaimed request can be claimed by one representative. The handler answers
  or releases it. Others see the handler's display name and cannot answer or release it. Logout does
  not release a claim. There is no lease and no expiry. Cancelling a request cancels its active
  assignment in the same transaction. A cancelled request cannot be claimed. **Reachability:**
  `deactivate_alternative` has no production caller yet (only tests call it), so no HTTP or agent
  path cancels a request today. No cancellation workflow was added without approval.
- **Search:** case-insensitive substring match on the question text or the request ID. `%` and `_`
  are matched literally. `after` is an exclusive keyset cursor on (createdAt, requestId).
- **Angular impact (open):** the existing representative page posts answers without claiming. Under
  this rule each such answer returns `409 INVALID_STATE_TRANSITION`. The frontend must claim before it
  answers, or the business must decide otherwise. The backend does not auto-claim, because the
  claimed-before-answer rule is a requirement.
- **Data:** `business.request_assignments` (migration `0005_request_assignments`). At most one row
  per request is `active`, enforced by the partial unique index `uq_request_assignments_one_active`.
  Released, completed and cancelled rows are kept as history.
- **Lock order (one order for every operation):** operation claim → case `FOR UPDATE` → request
  `FOR UPDATE` → assignment. Claim is request → assignment insert. Release is request → assignment.
  Answer is case → request → assignment, and it completes the assignment in the same transaction as
  the answer and its outbox event. Cancel is case → request → assignment. Before this change the
  answer path took the assignment lock first, against the order used by release.
- **Verification:** `tests/integration/business/database/test_representative_claiming.py` runs
  claim vs claim, claim vs cancel, claim vs answer, release vs answer, release vs claim and duplicate
  operations on real PostgreSQL. It checks the invariants afterwards and never mocks the database.

---
