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
- Policy-support validation lives in BL (`app/business/rules/validator.py`) and is invoked by BL/Application;
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
- **Consistency (BL).** `app/business/reference_data/consistency.py`: every catalogue key exists in the
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
