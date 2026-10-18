# Business Module roadmap

**Status:** approved implementation plan, not a specification. It is subordinate to `CLAUDE.md`
and the authoritative documents in `docs/` (architecture, agent-spec, integration-contracts,
frontend-handoff). Decisions and verification results are recorded in
`docs/business-decisions.md` (§12 package layout, §13 Step 3A).

Origin: the Steps 3–4 plan appended to the Git-integration plan of 2026-10-17 (local Claude Code
plan file `curious-inventing-stallman.md`, not in the repository), revised on 2026-10-18.

## Stages

| Stage | Scope | Status |
|---|---|---|
| 0–2 | Setup, provisional contracts, Domain + deterministic BL, importers, reference data (`0001`) | Done |
| Layout | `app/{api,business,ai,contracts}` package layout, architecture tests | Done (2026-10-18) |
| 3A | Persistence foundation: migration `0002_business_cases`, repositories, `SqlUnitOfWork`, optimistic versions, idempotency, immutable records, demo seed | Done, approved (2026-10-18) |
| 3B | `IdentityService` + `AuthorizationPolicy` (revalidated actors), `EmployeeProfileService`, `CaseService` (create/context/actual vs hypothetical updates), alternatives helpers, freshness, contracts C1–C4 | Done (2026-10-18), awaiting review |
| 4A | BL eligibility (group, family, waiver), `CatalogueService` + result sets, `ValuationService`, `check_eligibility`; contract changes G1–G5 (`get_representative_request`, `resolve_occupation`, `confirm_profile_fields`, `fieldsRequiringConfirmation`, typed `fieldCode`) | Accepted 2026-10-18; G1–G5 await AI-side compatibility confirmation |
| 4B | `ReadinessService`, `ComparisonService`, run snapshots, details + freshness; six reference employees end to end | Implemented 2026-10-18 without contract changes, awaiting review; Q1 (`ReadinessRequest.kind`) paused for shared-contract approval |
| 4C | `BusinessToolsFacade` (13 tools), envelope/error mapping, representative questions (employee side) | Implemented 2026-10-18 without contract changes, awaiting review (§17) |
| 4D | FastAPI vertical slice, token authentication (B1), Problem Details, OpenAPI export + drift check; frontend verified against `origin/feature/frontend` first | Not started |

Each stage ends with its Definition of Done and a report; the next stage starts only after the
owner's approval.

## Pending decisions (not implemented)

| ID | Topic | Status |
|---|---|---|
| D1 | Separate least-privilege DB logins + Business Outbox instead of cross-schema transactions | Direction approved and documented (2026-10-18); Outbox and feed implementation deferred to the representative-answer stage; recovery to be proven by tests |
| D2 | `DurableInputQueue` into the AI module; `BusinessEventFeed` in contracts | Done as provisional interfaces (C3, C4) |
| D3 | FK `agent.conversations.case_id → business.cases` | Needs a PostgreSQL permissions test |
| D8 | PostgreSQL role provisioning | Separate approval before AI persistence |

Open for joint review with the AI developer (contracts stay provisional until then): DTO/SSE
shapes, `ComparisonDetails.inputSnapshot` shape, profile route name, representative field-code
allowlist, conversation-service ownership (architecture A2 vs D4).
