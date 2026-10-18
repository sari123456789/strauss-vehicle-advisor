# Contract sync: G1–G5 (Business Step 4A) — for the AI developer

Status: **approved by the Business owner, implemented on `feature/business-backend`, not yet
committed**. Please confirm that the AI side can adopt these changes before they are committed.
Contract version stays `0.1.0` (provisional).

## 1. What you need to update

1. Your `BusinessToolsPort` adapter/fake must implement **3 new methods** (13 tools in total).
2. `CANONICAL_TOOL_NAMES` has 3 new names: `get_representative_request`, `resolve_occupation`,
   `confirm_profile_fields`.
3. `CaseContext` has a new field, `fieldsRequiringConfirmation` (defaults to an empty list, so
   existing payloads stay valid).
4. `PendingRepresentativeField.fieldCode` is now the `RepresentativeFieldCode` enum instead of a
   free string. Values outside the enum are now rejected.
5. Regenerate the TypeScript models from `contracts/schemas/`, if you consume them.

No other DTO changed. The other 25 JSON Schemas are byte-identical.

## 2. New port methods (`app/contracts/business_port.py`)

```python
async def get_representative_request(
    self, ctx: ToolContext, request: RepresentativeRequestLookup
) -> Envelope[RequestStatusView]: ...

async def resolve_occupation(
    self, ctx: ToolContext, request: ResolveOccupationRequest
) -> Envelope[ResolveOccupationResult]: ...

async def confirm_profile_fields(
    self, ctx: ToolContext, request: ConfirmProfileFieldsRequest
) -> Envelope[ConfirmProfileFieldsResult]: ...
```

## 3. New and changed DTOs

```python
# app/contracts/representatives.py  (G1)
class RepresentativeRequestLookup(ContractModel):
    request_id: UUID                       # JSON: requestId
# result: the existing RequestStatusView (answer is null until the answer stage)

# app/contracts/catalogue.py  (G2)
class ResolveOccupationRequest(ContractModel):
    query: str | None = None               # 1..120 chars; omitted -> all occupations
class OccupationCandidate(ContractModel):
    occupation_id: OccupationIdStr         # occupationId, e.g. "OCC-SOFTWARE-DEV"
    name_he: str                           # nameHe, official Hebrew name
class ResolveOccupationResult(ContractModel):
    status: ResolveStatus                  # resolved | ambiguous | not_found
    candidates: list[OccupationCandidate] = []   # max 20

# app/contracts/case_inputs.py  (G3)
class ConfirmProfileFieldsRequest(ContractModel):
    expected_case_version: int             # >= 0
    expected_profile_version: int          # >= 0
    operation_id: UUID
    source_message_id: UUID | None = None
    fields: list[FieldPath]                # 1..16, unique
class ConfirmProfileFieldsResult(ContractModel):
    confirmed_fields: list[FieldPath]
    not_confirmable: list[Issue] = []      # MISSING_INPUT / AMBIGUOUS_INPUT
    new_case_version: int
    new_profile_version: int

# app/contracts/cases.py  (G4)
class CaseContext(ContractModel):
    ...
    fields_requiring_confirmation: list[FieldPath] = []

# app/contracts/readiness.py  (G5)
class PendingRepresentativeField(ContractModel):
    field_code: RepresentativeFieldCode    # was: str
```

## 4. JSON Schema changes (`contracts/schemas/`)

| File | Change |
|---|---|
| `RepresentativeRequestLookup.schema.json` | new |
| `ResolveOccupationRequest.schema.json` | new |
| `ResolveOccupationResult.schema.json` | new (includes `OccupationCandidate`) |
| `ConfirmProfileFieldsRequest.schema.json` | new |
| `ConfirmProfileFieldsResult.schema.json` | new |
| `CaseContext.schema.json` | added the optional `fieldsRequiringConfirmation` array of `FieldPath` |
| `ReadinessResult.schema.json` | `PendingRepresentativeField.fieldCode` now references the `RepresentativeFieldCode` enum |
| `index.json` | lists the 5 new schemas |

## 5. Behavior the agent can rely on

- **Errors.** An invented request ID, or one from another case or employee, returns
  `status=error` with `NOT_FOUND` and no details. Representatives get `FORBIDDEN`; inactive
  accounts get `UNAUTHENTICATED`.
- **`get_representative_request`** is read-only and only sees requests of the case in
  `ToolContext`.
- **`resolve_occupation`** returns `resolved` only for:
  - an exact official ID;
  - the exact official name in either written form, e.g. "מפתח תוכנה" or "מפתחת תוכנה".

  Partial wording returns `ambiguous` candidates (ask the employee to choose), even when there is
  only one candidate. Nothing found returns `not_found` (no synonyms). This tool never writes the
  profile: after the employee chooses, call `update_conversation_inputs` with
  `employee.occupationId`.
- **`confirm_profile_fields`** marks saved values as still correct. It does not change their
  values, and nothing in a hypothetical scenario is ever confirmed. Use it only after an
  employee's explicit confirmation of the relevant facts. A retry with the same `operationId`
  returns the same result. A stale version returns `VERSION_CONFLICT`: reload the context and
  ask again.
- **`fieldsRequiringConfirmation`** is a hint. A field is listed when it is usable but has not
  been stated or confirmed in this conversation.
  - Ask only about the fields the current goal needs (listing vehicles never needs salary).
  - Never run a blanket confirmation at conversation start.
  - It is not time-based, and it is unrelated to `recentRuns[].freshness`.
- **Envelope statuses in Step 4A:**
  - `list_eligible_options` returns `partial` when a category is unchecked.
  - `resolve_vehicle`, `resolve_occupation` and `check_eligibility` return `needs_information` when
    not resolved or not decidable.
  - An unknown vehicle in `get_vehicle_tax_value` or `check_eligibility` returns
    `status=error` with `NOT_FOUND`, plus a `VEHICLE_NOT_FOUND` issue in `issues`.
- **Free-text vehicle queries** never resolve, even with a single candidate. Show the candidates
  and ask for confirmation. `TAX_KEY_AMBIGUOUS` means the given codes do not identify one record
  (e.g. the vehicle type is missing).

The fake (`tests/fakes/fake_business_port.py` and `fixtures/success.json`) already implements the
3 new tools, for contract tests.

## 6. Addendum: Step 4B (no DTO change, one pending proposal)

Step 4B uses the existing DTOs unchanged. It introduces new reason tokens in `Issue.reason`,
which the agent needs to handle.

**A current-arrangement fact that is saved but not stated or confirmed in this conversation.**
- Appears in the readiness issues and in `current.missingInformation` as:
  `AMBIGUOUS_INPUT`, `reason="confirmation_required"`, `field=<FieldPath>`.
- The field is **not** listed in `missingEmployeeFields` or `ambiguousFields`.
- Readiness sets `baselineReady=false`. A comparison returns `current.status=needs_information`,
  and every `delta` is `null`.
- After the employee confirms, call `confirm_profile_fields` with exactly those fields.

**Other situations:**

| Situation | Where | Representation |
|---|---|---|
| Reported propulsion differs from the catalogue | baseline | `SOURCE_CONFLICT`, `reason="catalogue_propulsion_differs"` |
| Stored option list built from other facts | comparison issues | `STALE_REVISION`, `reason="result_set_built_from_other_facts"`; per option, `reason="eligibility_changed_since_list"` |
| Stored option list from an older release | error | `VERSION_CONFLICT` with an issue `STALE_REVISION`, `reason="result_set_release_changed"` |
| A missing component fact that is not an employee field | `missingInformation` | `reason="<path>:<missing or needs_clarification>"`, e.g. `alternative.participation:missing` |

**Proposal Q1: requires joint approval; not implemented.**

```python
class ReadinessRequest(ContractModel):
    ...
    kind: RunKind = RunKind.REGULAR   # new, optional; JSON "kind": "regular" | "hypothetical"
```

The field is optional, so existing requests keep working and default to `regular`. Until it is
approved, readiness evaluates regular calculations only, using the latest actual profile.

`ComparisonDetails.inputSnapshot` now has a stable internal shape, `schemaVersion: 1`, described
in `docs/business-decisions.md` §16. Its type is unchanged: still a free-form JSON object.

## 7. Addendum: Step 4C (Business facade available; no DTO change)

`BusinessServices.tools` (`app/business/application/tools_facade.py`, `BusinessToolsFacade`) is
the real `BusinessToolsPort`. It implements all 13 tools and behaves like the fake, so the AI
tool adapter can switch from the fake to it through bootstrap without code changes.

**Calling it:**
- **Never raises for business outcomes.** Every result is an `Envelope`. Errors carry
  `status=error`, a stable `error.code` and safe details. The fields `data` are always `null` on
  errors, and `issues` may carry, for example, `VEHICLE_NOT_FOUND`.
- **`traceId`:** echoes `ToolContext.traceId`.
- **Unexpected failures:** return `INFRA_UNAVAILABLE` with no details. Show a natural "temporary
  problem" message, and do not retry automatically unless the call was idempotent.
- **T61:** a tax-lookup outage returns `INFRA_UNAVAILABLE`, `safeDetails.reason =
  "reference_data_unavailable"`, and no run is stored. A later retry with the same `operationId`
  calculates normally.
- **`create_representative_question`:**
  - `idempotencyKey` acts as the operation ID: the same key with a different payload is
    `OPERATION_PAYLOAD_MISMATCH`, and a replay returns `created=false`.
  - Asking about an already-open fact returns the existing `requestId` with `created=false`.
  - Scope allowlist: participation requires `optionKey` `vehicle:...`; `occupation.waiverPolicy`
    takes `waiver` or none; group and `currentArrangement.*` take none.
  - The case version is not changed.

**Open items for joint review (no DTO change made):**
1. **No generic internal-error `ErrorCode`.** Unexpected failures use `INFRA_UNAVAILABLE`.
2. **`expectedValueType` per `fieldCode`** is provisional and one to one. In particular,
   `occupation.waiverPolicy` maps to `boolean`, although it might need the allowance amount.
3. **Envelope status of `update_conversation_inputs`** is always `ok`; conflicts and missing
   information are returned as issues.
