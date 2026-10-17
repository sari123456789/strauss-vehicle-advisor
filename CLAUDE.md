# Strauss Vehicle Advisor — Claude Code Project Instructions

## Purpose and source of truth

Build a Hebrew conversational vehicle-benefit advisor for the Strauss hackathon. The implementation is a demonstration based on the supplied challenge materials, **not** an approved production payroll system.

Before planning or editing, read the relevant specifications in `docs/`:

- `docs/architecture.md` — authoritative backend architecture, boundaries, dependency rules and deployment approach.
- `docs/agent-spec.md` — approved conversational behavior, business decisions, tool semantics and acceptance scenarios.
- `docs/integration-contracts.md` — cross-team interfaces and contract status (some DTO/SSE details still require approval).
- `docs/frontend-handoff.md` — Angular implementation and UI handoff instructions.

Also consult the supplied challenge PDF, business JSON, Excel catalogue/reference cases and tax-value CSV in `data/source/` for source-specific rules. Keep these source files unchanged. English specifications are authoritative; Hebrew translations are for review only. Do not resolve conflicts by guessing: identify the exact conflicting sections and ask for a decision before implementing the affected behavior. Do not present proposals as approved requirements.

## Challenge source files and encoding — mandatory

Keep the original challenge files under `data/source/` (rename filenames for stable project paths **without altering their contents**):

| Repository path | Source type | Required reading behavior |
|---|---|---|
| `data/source/challenge-instructions.pdf` | Challenge instructions (PDF) | Read with a PDF parser; no text-file encoding parameter. |
| `data/source/business-rules.json` | Demo business rules (JSON) | Read with explicit `encoding="utf-8"`. |
| `data/source/challenge-data.xlsx` | Catalogue, occupations, example employees and calculations (Excel) | Read via `openpyxl`; no text-file encoding parameter. |
| `data/source/vehicle-tax-2026.csv` | Israel Tax Authority vehicle benefit dataset (CSV) | Read with **Windows-1255 (`cp1255`)** explicitly, never assume UTF-8. |

- All Markdown specs, Python source, JSON files written by the project, and ordinary text/configuration files must be UTF-8. PostgreSQL text must support Hebrew with UTF-8 database encoding.
- During CSV import, preserve Hebrew headers and source values, including meaningful whitespace and **leading zeros in identifier codes**. Normalize only as explicitly defined in the import contract; keep traceability to source data. Read code columns as strings, validate them, and convert numeric money fields explicitly to `Decimal` (never through binary float).
- Validate the CSV's expected schema and official five-part vehicle lookup key: `(שנת מס, שנת רישום, קוד סוג רכב, קוד תוצר, קוד דגם)`. Fail explicitly on decoding errors, missing columns, invalid codes, duplicates, or ambiguous matches; never silently replace undecodable characters, skip bad records, or guess a vehicle key.
- Preserve original source files as immutable inputs. Keep import/version metadata (including a SHA-256 content hash where appropriate) so calculations can be traced to the exact source version. Put reproducible generated artifacts under `data/processed/` where appropriate; do not manually edit originals to make tests pass.
- Before committing original files to Git, verify permission to redistribute them and verify that they contain no real confidential employee or organizational data. Do not commit secrets or private payroll records.
- Import/parsing of business sources belongs to **Business Infrastructure**. The AI Module must not parse the CSV/Excel directly or bypass Business Application to access imported business data.

## Fixed architecture — do not redesign

- **Frontend:** Angular.
- **Backend:** Python only, including AI orchestration and deterministic algorithms.
- **Database:** PostgreSQL.
- **Exactly two functional backend modules:**
  1. **Business System:** `Application` (use cases, authorization, coordination), `BL` / Business Logic (deterministic calculations and eligibility), `Domain / Core` (independent business concepts and models), `Infrastructure` (data persistence and source imports).
  2. **AI Engine:** LangGraph orchestration, conversational tools, LLM adapter, persisted conversation state/history and representative-response continuation.
- **One shared FastAPI boundary**, outside both functional modules: authenticate, validate, and dispatch business/catalogue/calculation/representative routes to Business Application, and chat/conversation/streaming routes to the AI Engine.
- **AI-to-business calls go through Business Application only.** No agent access to BL functions, business repositories, ORM, or business database queries directly. Business decisions and money calculations belong in deterministic Python services, never in the LLM or browser.
- **Preserve the architecture document's existing dependency inversion**: Domain/Core depends on no application layers; BL depends on Domain/Core; Application consumes BL and Domain/Core and calls Infrastructure through Application-owned ports; Infrastructure implements those ports and uses Domain/Core. Bootstrap wires implementations. Do not add circular direct imports or new functional modules. Workers/bootstrap are runtime entrypoints, not extra functional modules.

## Non-negotiable behavior

- An employee's **actual shared EmployeeProfile** includes the six salary fields plus reported occupation, entitlement group, qualifying-child count and last-reported current vehicle arrangement. Values may be stale; the profile is not employer-verified ground truth.
- Display saved relevant information in the sidebar. **Ask only for data necessary for the current request**; do not run a blanket confirmation questionnaire at conversation start. Confirm relevant stale or ambiguous facts when they affect a calculation.
- Distinguish actual corrections from hypothetical “what if” scenarios. Only clear actual updates change the shared profile, with optimistic version checks. Hypotheticals and alternatives remain conversation-specific.
- Persist all visible conversation messages, state/checkpoints and immutable calculation snapshots. Resume an existing conversation with its own context; do not overwrite its history. No automatic 30-day deletion in the demo.
- A pending transportation representative answer blocks only the dependent operation/alternative, **not the conversation**. Return partial results when possible; resume the affected work when an applicable answer arrives without hijacking the current conversational topic.
- Show errors as helpful, natural Hebrew messages; do not display raw HTTP status codes, stack traces, or secrets to end users. Retain structured backend error codes for diagnostics and mapping.
- Never invent missing values, benefit-in-kind records, employee entitlements or salary results. Use the source rules, official complete tax key, `Decimal`, and approved display-rounding rules. Protect credentials and enforce server-side ownership/role checks.

## Parallel-development rules

- **AI developer:** owns AI workflow, LangGraph graph/nodes, prompts, tool adapters and agent evaluations; use in-process fakes conforming to the Application contracts until services are ready.
- **Business developer:** owns Application, BL, Domain/Core, business Infrastructure, data imports and numerical/database tests; expose stable Application ports for the AI team.
- **Shared ownership:** FastAPI entry-point integration, contract DTOs/OpenAPI, DI/bootstrap, cross-module tests and end-to-end behavior. Agree on file ownership before editing shared files.
- Develop on separate Git branches; do not independently rename tools, alter shared schemas, or change business rules. The canonical agent-tool names are in `docs/integration-contracts.md` / `docs/agent-spec.md`. Python Pydantic/OpenAPI DTOs are the basis for generated TypeScript models; avoid a competing frontend contract.
- The exact unsettled DTO/SSE shapes are **not automatically approved**. Identify open items, propose one contract, and wait for joint approval before implementing boundary-dependent code.

## Work discipline

1. Begin substantial tasks in **Plan Mode**. Read the relevant docs and inspect the actual repository; proposed paths are not proof that files already exist.
2. State scope, files to be touched, dependencies, planned tests and unresolved decisions. Await approval before modifying architecture or shared contracts.
3. Implement the smallest independently testable vertical slice. Run available focused tests, then integration tests; report actual results, not presumed passes.
4. Add/regress tests for example employees, source discrepancies, partial readiness, authorization, concurrency, history, and representative continuation where applicable.
5. Keep documentation and code in sync. If requirements conflict, stop on the affected issue and request clarification rather than silently choosing a behavior.

## Documentation location

Keep these four authoritative English files in `docs/` with exactly the names listed above. A `docs/he/` directory may contain translations, but must not override the English source. Store `CLAUDE.md` in the repository root, alongside `README.md`. If these docs have not yet been copied/renamed into the repository, do that as an explicit setup step and confirm paths before starting implementation.
