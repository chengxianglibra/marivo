---
status: draft
created: 2026-05-10
updated: 2026-05-10
---

# OSI/AOI Static Model Cutover Design

**Date:** 2026-05-10
**Status:** Draft (CEO review complete)
**Scope:** Generate static Pydantic models from `osi-marivo-spec` and `aoi-spec`, then cut Marivo's semantic and analysis-operation runtime over to those generated contracts.

**Key Updates (2026-05-10):**
1. AOI `TimeScope` now includes required `field` parameter — time field selection is caller-specified at operation time, not semantic-layer metadata. `primary_time_field` removed from metric extensions and storage.
2. Generated models are base-layer primitives. Runtime code uses them directly or composes wrapper classes for Marivo-internal fields. No parallel model hierarchies allowed.

---

## 1. Objective

Marivo now has two standards-track contracts:

- **OSI-Marivo** defines semantic-layer objects: semantic models, datasets, fields, metrics, and MARIVO vendor extensions.
- **AOI** defines analysis-operation objects: atomic intent requests and artifacts for `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, and `forecast`.

The implementation is not yet aligned end to end. Semantic v2 is OSI-shaped but still hand-written and behind the latest `osi-marivo-spec`. Atomic/derived intent execution still uses older typed-intent objects, and AOI has only a published schema/spec, not runtime implementation.

This design makes the JSON Schemas the contract source of truth and the generated Pydantic models the implementation source of truth inside Marivo.

### 1.1 Goals

1. Generate static, committed Pydantic models from:
   - `osi-marivo-spec/schema/osi-marivo.schema.json`
   - `aoi-spec/schema/aoi.schema.json`
2. Use generated OSI models for semantic model CRUD, validation, persistence mapping, and semantic resolution.
3. Use generated AOI models for atomic intent request validation and artifact responses.
4. Keep MCP tools agent-friendly through a compact DTO layer that converts into OSI/AOI generated models.
5. Preserve `attribute`, `diagnose`, and `validate` as Marivo compatibility operations implemented on top of AOI atomic intents.
6. Delete or isolate the old semantic and operation contracts after the new path is runnable.
7. Prove the first complete flow on **DuckDB**: semantic model creation -> AOI atomic analysis -> derived compatibility operation -> MCP tool call.

### 1.2 Non-goals

- No backward-compatible v1 wire shape guarantee. Marivo is pre-launch and this is a breaking cutover.
- No runtime schema-to-model generation. Generated Python files are committed and reviewed.
- No new AOI derived-intent standard. `attribute`, `diagnose`, and `validate` remain Marivo compatibility/product operations, not AOI core.
- No Trino-first implementation. Trino stays behind the datasource/engine boundary, but the first end-to-end gate is DuckDB.
- No expansion of AOI v0.1 beyond the published seven atomic intents.

---

## 2. Current State

### 2.1 Semantic Layer

Current semantic v2 is close to OSI, but the model classes are hand-written under transport modules:

- `marivo/transports/http/models/osi.py`
- `marivo/transports/http/models/marivo_extensions.py`
- `marivo/runtime/semantic/osi_storage.py`
- `marivo/runtime/semantic/semantic_service.py`
- `marivo/transports/http/semantic_v2.py`

The storage/runtime boundary still carries raw dicts in several places. `marivo/contracts/semantic.py` contains a domain-level `SemanticModel` with `osi_document: dict[str, Any]`, which keeps old untyped semantic-contract assumptions alive.

### 2.2 Operation Layer

Current atomic and derived intents use older hand-written request models:

- `marivo/transports/http/models/intents.py`
- `marivo/transports/http/models/intent_responses.py`
- `marivo/runtime/intents/*.py`
- `marivo/runtime/intent_execution.py`
- `marivo/transports/mcp/tools/schemas.py`
- `marivo/transports/mcp/tools/intents.py`

AOI artifacts and request objects are not the canonical runtime input/output. Response models are mostly `RootModel[JsonObject]`, so OpenAPI/MCP schema quality is weaker than the published AOI contract.

### 2.3 MCP Tool Surface

MCP already has tool-specific DTOs and validators, which is the right boundary. The problem is that some DTOs still mirror old typed intents, and generic structured objects are used where AOI can provide concrete names and fields.

---

## 3. Target Architecture

```
Specification source
  osi-marivo-spec/schema/osi-marivo.schema.json
  aoi-spec/schema/aoi.schema.json
          |
          v
Static generated contract models
  marivo/contracts/generated/osi.py
  marivo/contracts/generated/aoi.py
          |
          +--------------------+
          |                    |
          v                    v
Runtime contracts        Transport DTOs
  semantic service         HTTP models
  atomic intents           MCP tool schemas
  artifact commits         legacy derived DTOs
          |
          v
DuckDB execution path
  datasource -> compiler -> query execution -> AOI artifact
```

The generated model layer is the only canonical schema implementation for spec-defined contracts. Runtime code uses generated models as the base; it may compose them into richer runtime objects but must not redefine spec fields. Transport DTOs may simplify the caller experience, but they must convert into generated OSI/AOI models before business logic runs.

### 3.1 Contract Ownership

| Layer | Owns | Must not own |
|---|---|---|
| `osi-marivo-spec/`, `aoi-spec/` | JSON Schema truth source | Runtime behavior |
| `marivo/contracts/generated/` | Static Pydantic model code generated from schema | Hand-written business logic |
| Runtime semantic layer | OSI validation, storage mapping, semantic resolution; may wrap generated models with Marivo-internal metadata | Parallel model hierarchies that redefine spec fields |
| Runtime intent layer | AOI atomic execution and artifact creation; may wrap generated models with step/session metadata | Legacy typed-intent semantics as primary path |
| MCP tools | Agent-friendly DTOs and conversion into generated models | Independent business contracts |
| Derived compatibility layer | Old product operation names mapped to atomic AOI pipelines | Reimplemented analytics |

---

## 4. Static Model Generation

### 4.1 Generated Files

Add a checked-in generator script:

```
scripts/generate_contract_models.py
```

Generate and commit:

```
marivo/contracts/generated/
  __init__.py
  osi.py
  aoi.py
```

The generated modules expose these version constants:

- `OSI_MARIVO_SPEC_VERSION`
- `OSI_CORE_SPEC_VERSION`
- `AOI_SPEC_VERSION`

Runtime and tests must read version values from generated modules rather than duplicating string literals.

### 4.2 Generation Rules

1. JSON Schemas remain the source of truth.
2. Generated Python files are deterministic: same schema input produces the same file output.
3. Generated files are never edited manually for business behavior.
4. Manual validators or convenience methods live outside generated files.
5. `additionalProperties: false` must become `extra="forbid"` or equivalent Pydantic behavior.
6. Requiredness and nullability must remain separate:
   - required controls whether a key must exist
   - `null` controls whether the value may be null
7. JSON Schema aliases such as `from` must be represented with Pydantic aliases, not renamed in the wire contract.
8. Generated models are base-layer primitives. Other Marivo modules import and use them directly, or compose wrapper classes that add Marivo-internal fields. No module may create parallel model classes that redefine fields already in the generated models.

### 4.3 Public Imports

Generated modules are the canonical import target for runtime code. Runtime code imports directly from `marivo/contracts/generated/{osi,aoi}.py` and uses those models as-is or wraps them when additional Marivo-specific fields are needed.

**Wrapping pattern:** When Marivo needs to add fields beyond the spec (e.g., internal IDs, computed state, caching metadata), create wrapper classes in the appropriate runtime module that **compose** the generated model, not duplicate it:

```python
from marivo.contracts.generated.osi import SemanticModel as OSISemanticModel

class SemanticModelWithMetadata:
    """Runtime wrapper adding Marivo-internal metadata to OSI SemanticModel."""
    osi_model: OSISemanticModel
    internal_id: int
    created_at: datetime
    updated_at: datetime
```

**Do not:** Create parallel model hierarchies that redefine OSI/AOI fields. The generated models are the single source of truth for spec-defined contracts.

Existing transport model modules have these target roles during and after cutover:

- `marivo/transports/http/models/osi.py` re-exports generated OSI models for FastAPI routes (preserves import paths during cutover, becomes pure re-export shim after Phase B).
- `marivo/transports/http/models/aoi.py` re-exports generated AOI request/artifact models for HTTP routes.
- `marivo/transports/http/models/legacy_intents.py` holds temporary DTOs for derived compatibility only.
- `marivo/transports/http/models/intents.py` is deleted after atomic routes move to AOI and derived compatibility DTOs move to `legacy_intents.py`.
- `marivo/transports/http/models/intent_responses.py` is deleted once AOI artifact response models cover every atomic response.

---

## 5. OSI Semantic Layer Cutover

### 5.1 Semantic CRUD

`/semantic-models` remains the main semantic-model HTTP surface, but the request and response bodies become generated OSI models.

`SemanticModelV2Service` is retargeted so all write paths validate through generated OSI classes before persistence. It no longer accepts arbitrary dicts from the transport layer.

### 5.2 Domain Contract Cleanup

The existing `marivo/contracts/semantic.py` contract is no longer allowed to hide an untyped `osi_document: dict[str, Any]` as the semantic payload.

Target state:

- Semantic payloads are generated OSI models.
- List/read summaries may keep a small domain summary model, but not the full semantic document.
- `ModelStore` ports either store generated OSI documents directly or wrap them in a domain object with a typed `osi_document` field.
- Any code that needs model internals must use generated OSI fields, not raw JSON dict traversal.

### 5.3 Storage Mapping

`marivo/runtime/semantic/osi_storage.py` remains the adapter between typed OSI models and storage rows. It becomes the only place that converts:

- OSI `custom_extensions` string payloads into typed MARIVO extension data
- typed extension data into storage columns such as `datasource_id` and metric `additive_dimensions`
- storage rows back into generated OSI objects

The old metric storage columns `primary_time_field`, `observation_grain`, `observed_dataset`, `additivity`, and `filters` are removed. These values are either caller-supplied at operation time (`TimeScope.field`) or inferred at readiness/execution time (dataset routing, grain).

All MARIVO extension parsing must happen through generated extension classes or narrow helper functions around them.

### 5.4 Readiness And Resolution

Semantic readiness, compiler resolution, and metric lookup must operate on generated OSI semantic objects. The resolution layer can still expose runtime-specific helper structures, but those structures must be derived from generated OSI models and must not become a second schema.

---

## 6. AOI Operation Layer Cutover

### 6.1 Atomic Intent Runtime

The seven AOI atomic intents become the canonical runtime operations:

- `observe`
- `compare`
- `decompose`
- `correlate`
- `detect`
- `test`
- `forecast`

Existing modules under `marivo/runtime/intents/` remain the implementation home, but their public internal boundary changes:

- input: generated AOI request models
- output: generated AOI artifact models
- artifact persistence: AOI artifact body plus Marivo-owned step/session metadata outside the AOI artifact body

Runtime code may keep internal helper objects for compiled queries, dense series, calendar alignment, and DuckDB execution. Those helpers are implementation detail and do not leak to HTTP/MCP.

### 6.2 Artifact References

AOI uses direct artifact identifiers. Marivo still owns session and step metadata outside AOI. The runtime boundary resolves:

1. MCP/HTTP input DTO -> generated AOI request
2. generated AOI request artifact IDs -> Marivo artifact store lookup
3. Marivo step/session metadata -> runtime execution context
4. runtime output -> generated AOI artifact
5. generated AOI artifact -> persisted artifact content

Producing-step identity is stored in Marivo step metadata, not embedded into the AOI artifact body unless AOI schema defines it.

### 6.3 Source-type Intents

`observe`, `detect`, and `test` resolve OSI semantic refs before query planning. The metric reference resolves through the generated OSI model and its MARIVO extension payload, especially:

- dataset datasource routing
- metric expression
- metric additive dimensions

Time field selection is **not** a semantic-layer concern. AOI `TimeScope` is self-contained: it requires `field`, `start`, and `end`. The caller (agent, MCP tool, or derived operation) specifies which time field to filter on. Runtime does not infer or store a `primary_time_field` on the metric. This eliminates the old `primary_time_field` from the MARIVO metric extension and from readiness validation for time field existence. Semantic validation may still verify that time-typed fields exist in a dataset, but it does not bind a metric to a specific time field.

The first execution gate uses DuckDB. If a semantic model references a non-DuckDB datasource in this phase, runtime should fail clearly rather than silently falling back.

### 6.4 Ref-type Intents

`compare`, `decompose`, `correlate`, and `forecast` consume AOI artifacts by ID. They should not re-interpret old step refs as the canonical contract. If MCP or legacy HTTP tools still pass step refs during migration, that conversion belongs in the transport/compatibility layer.

---

## 7. MCP Tool Schema

The MCP surface uses **agent-friendly DTOs**, not raw generated AOI/OSI models.

### 7.1 DTO Principles

1. No JSON-encoded string fields for objects. Nested inputs must be structured objects.
2. Required fields must appear as required in FastMCP `inputSchema`.
3. Keep DTOs shallow where possible; prefer named submodels over `dict[str, Any]`.
4. Keep `session_id` explicit and required for tools that operate inside a session.
5. Avoid overloaded unions when a separate tool or explicit mode field is clearer.
6. Convert DTOs into generated OSI/AOI models before calling runtime.
7. Snapshot or inspect the actual FastMCP tool `inputSchema`; do not rely only on local Pydantic `model_json_schema()`.

### 7.2 Atomic Tools

MCP exposes the seven AOI atomic intents with compact parameters:

- `observe(session_id, metric, time_scope, filter?, granularity?, dimensions?)`
- `compare(session_id, left_artifact_id, right_artifact_id, compare_type?)`
- `decompose(session_id, compare_artifact_id, dimension, limit?)`
- `correlate(session_id, left_artifact_id, right_artifact_id, method?)`
- `detect(session_id, metric, time_scope, granularity, filter?, split_by?, profile?, sensitivity?, limit?)`
- `test_intent(session_id, metric, left, right, kind, hypothesis)`
- `forecast(session_id, source_artifact_id, horizon, profile?)`

MCP keeps existing tool names for the seven atomic operations (`test_intent` remains the Python-safe tool name for AOI `test`). DTO fields match AOI vocabulary unless the field is transport-only, such as `session_id`.

### 7.3 Semantic Tools

Semantic MCP tools use compact wrappers for simple commands such as list/get/delete. Large model import accepts a single `semantic_model` or `osi_document` object because OSI is already the semantic contract. Every submitted semantic payload must validate as generated OSI before it reaches runtime.

---

## 8. Derived Intent Compatibility

`attribute`, `diagnose`, and `validate` are retained as Marivo compatibility operations. They are not AOI core.

### 8.1 Design Rule

Derived compatibility code orchestrates AOI atomic intents. It must not own independent analytics semantics.

### 8.2 Mapping

| Derived operation | New implementation shape |
|---|---|
| `attribute` | Build two AOI `observe` requests when legacy input contains slices; run `compare`; run `decompose` for requested dimensions; assemble compatibility result. |
| `diagnose` | For auto-detect mode, run AOI `detect` first, then run focused compare/decompose follow-ups. For explicit compare mode, reuse the `attribute` pipeline. |
| `validate` | Convert legacy validation input into AOI `test` with `kind` and `hypothesis`; return compatibility result from the AOI test artifact. |

If a legacy derived feature cannot be represented by the AOI atomic set without substantial custom logic, it should be deleted or narrowed. The compatibility layer is not a place to preserve every old branch.

### 8.3 Isolation

Old derived DTOs live in explicit legacy modules:

```
marivo/transports/http/models/legacy_intents.py
marivo/runtime/intents/legacy_derived.py
```

These modules are the only allowed place for old request names. They must not leak into the atomic AOI runtime.

---

## 9. Cleanup Strategy

Delete or isolate old surfaces only after the generated OSI/AOI path has tests.

### 9.1 Remove From Main Path

The following should not remain in the canonical runtime path:

- hand-written OSI model definitions that duplicate generated OSI classes
- `RootModel[JsonObject]` intent response placeholders for AOI atomic artifacts
- old typed-intent request classes for atomic intents
- step-ref-only request shapes as canonical AOI inputs
- raw semantic document dicts as the domain semantic payload

### 9.2 Keep Temporarily

Only these old pieces may remain temporarily:

- compatibility DTOs for `attribute`, `diagnose`, `validate`
- route aliases needed while HTTP/MCP tests move to AOI
- small summary models that are not full semantic documents

Every temporary compatibility module must have a narrow import boundary and tests that prove it delegates into AOI atomic runtime.

---

## 10. Execution Order

### Phase A: Static Models

1. Add contract-model generator script.
2. Generate OSI and AOI Pydantic modules.
3. Add tests proving examples from both specs validate through generated models.
4. Add typecheck/lint coverage for generated imports.

Gate: generated models parse current `osi-marivo-spec/examples/**` and `aoi-spec/examples/**`.

### Phase B: OSI Semantic Runtime

1. Replace hand-written OSI classes with generated imports.
2. Retype semantic CRUD and storage mapping.
3. Replace raw `osi_document: dict` with typed OSI payloads.
4. Add DuckDB semantic model creation/readiness fixture.

Gate: create/list/get/import semantic model works with generated OSI models.

### Phase C: AOI Atomic Runtime

1. Retype atomic intent requests/responses to generated AOI models.
2. Persist generated AOI artifacts.
3. Convert artifact ID resolution into the runtime boundary.
4. Update HTTP atomic routes and docs.

Gate: DuckDB observe -> compare -> decompose passes end to end and returns AOI artifacts.

### Phase D: MCP DTO Cutover

1. Rewrite MCP schemas around compact DTOs.
2. Convert DTOs into generated AOI/OSI models before runtime calls.
3. Add MCP tool inputSchema tests.
4. Add MCP E2E for the DuckDB atomic flow.

Gate: MCP exposes structured object schemas and runs the DuckDB atomic flow.

### Phase E: Derived Compatibility

1. Implement `attribute`, `diagnose`, and `validate` as AOI atomic pipelines.
2. Add compatibility tests against representative old requests.
3. Delete or narrow old derived branches that do not map cleanly.

Gate: each retained derived operation proves it delegates into atomic AOI runtime.

### Phase F: Legacy Cleanup

1. Delete old atomic typed-intent request/response models.
2. Delete duplicate semantic model definitions.
3. Remove main-path imports from legacy modules.
4. Update API/MCP/docs to AOI/OSI vocabulary.

Gate: grep/import tests show no old atomic contract modules on the canonical path.

---

## 11. Verification

Minimum verification before the cutover is considered complete:

1. `make test`
2. `make typecheck`
3. `make lint`
4. OSI schema example validation through generated Pydantic models.
5. AOI schema example validation through generated Pydantic models.
6. DuckDB E2E:
   - create/import OSI semantic model
   - run AOI `observe`
   - run AOI `compare`
   - run AOI `decompose`
   - persist and read generated AOI artifacts
7. MCP E2E:
   - tool schemas expose structured DTOs
   - tool call runs the DuckDB AOI flow
8. Derived compatibility E2E:
   - `attribute`
   - `diagnose`
   - `validate`

---

## 12. Acceptance Criteria

1. Static generated Pydantic models exist for OSI-Marivo and AOI and are committed.
2. Semantic CRUD no longer depends on hand-written duplicate OSI model classes.
3. AOI atomic intent requests and artifacts are the canonical operation contracts.
4. MCP tools expose compact, structured, agent-friendly input schemas and convert into generated models.
5. `attribute`, `diagnose`, and `validate` remain callable but execute through AOI atomic pipelines.
6. A DuckDB end-to-end flow proves semantic creation through analysis execution.
7. Old v1 semantic/operation contracts are deleted from the canonical path or isolated in explicitly named compatibility modules.

---

## Appendix A: CEO Plan Review — 2026-05-10

### Review Decisions

| ID | Topic | Decision | Rationale |
|---|---|---|---|
| D2 | Generator tooling | `datamodel-code-generator` | Best Pydantic v2 + JSON Schema draft 2020-12 support |
| D3 | Metric extension fields | Runtime infers; spec is authoritative | `MarivoMetricExtension` in spec has only `additive_dimensions`; implementation's 5 extra fields (`observed_dataset`, `observation_grain`, `primary_time_field`, `additivity`, `filters`) must be resolved at readiness/execution time, not stored in extensions. **Update:** AOI `TimeScope` now includes `field` (required), so `primary_time_field` is no longer a semantic-layer concern — callers specify the time field at operation time. |
| D5 | AOI runtime boundary | `(AOI request, SessionContext)` as separate args | AOI models stay pure; Marivo session state is a separate concern |
| D6 | Inference timing | Runtime inference at readiness/execution time | NOT infer-then-store; compiler resolves metric metadata when needed, not at write time |
| D7 | Error handling | Readiness issues + execution-time domain errors | Two-tier: readiness check catches structural problems; execution catches data/logic errors |
| D8 | Extension decode | Keep `extract_marivo_extension()` helper | Existing pattern works for `contentSchema` JSON-string payloads |
| D9 | Validation gaps | Document as known limitations | Don't block cutover on draft 2020-12 edge cases (`contentSchema`, `if/then/else`) |
| D10 | Observability | Add structured logging | Compiler inference and DTO conversion get structured log events |
| D11 | Import paths | Migrate runtime imports to `contracts/generated/` | Source of truth must be explicit; transport layer becomes pure FastAPI re-export |

### Review Findings

**Must-address (add to plan):**

1. **Storage migration (Phase B):** The 4 inferred metric columns (`observed_dataset`, `observation_grain`, `additivity`, `filters`) are removed from SQLite storage. `primary_time_field` is also removed since AOI `TimeScope.field` makes time field selection caller-specified, not semantic-layer metadata. Table recreation required. Add explicit migration script step to Phase B.

2. **CI freshness check (Phase A):** Add CI step that runs `generate_contract_models.py` and verifies `git diff --exit-code` to catch stale generated code.

3. **Import migration (Phase B):** Runtime code (`osi_storage.py`, `semantic_service.py`, etc.) migrates to `from marivo.contracts.generated.osi import ...`. Transport models become re-export shims only.

4. **100% gate policy:** State explicitly that phase gates require 100% pass — no partial advancement.

**Should-address (notes for implementers):**

5. **Phase B/C parallelism:** Phases B (OSI semantic) and C (AOI atomic) touch different codepaths and could partially overlap. Plan is overly sequential.

6. **Derived operation latency:** `attribute` decomposes into 2x observe + compare + Nx decompose (sequential I/O). Plan should acknowledge higher latency vs current single-query implementation.

7. **Test fixtures beyond examples:** The 4 OSI examples are smoke-level. Phase B should add test-only fixtures for edge cases (multiple datasets, coexisting non-MARIVO extensions, missing optional fields).

8. **`intent_responses.py` early deletion:** All 10 response models are `RootModel[JsonObject]` with zero importers. Can be deleted in Phase C instead of waiting for Phase F.

9. **Directory naming debt:** `marivo/runtime/intents/` uses "intents" vocabulary while AOI uses "operations". Phase F should note this as accepted tech debt or rename.

### Verification Additions

Add to Section 11:

9. CI check: regenerated models match committed code (`generate_contract_models.py` + `git diff --exit-code`)
10. Storage migration: metric table column changes applied cleanly
11. Import audit: no runtime code imports OSI/AOI models from `transports/http/models/` (except re-export shims)

---

## Appendix B: Engineering Plan Review — 2026-05-11

### Review Decisions

| ID | Topic | Decision | Rationale |
|---|---|---|---|
| D1 | Import-linter contracts | Add 2 contracts for `contracts/generated/` | Automated CI enforcement prevents silent regression to old import paths. Matches existing 10-contract pattern. |
| D2 | OSI wrapper location | Evolve `contracts/semantic.py` in place | Smallest diff: replace `osi_document: dict` with `osi_model: OSISemanticModel`. 5 importers stay stable. |
| D3 | Metric column removal | Drop columns in `schema.py` DDL | Schema uses destructive DDL (DROP + CREATE). No migration system. No production data. |
| D4 | `additive_dimensions` storage | Add `additive_dimensions TEXT` column to `semantic_metrics` | Spec-defined field gets first-class storage. Consistent with existing JSON-array-in-TEXT pattern. |
| D5 | Rollback strategy | Git revert is the plan | Pre-launch single-developer project. Feature branches isolate each phase. |
| D6 | Generation script scope | CLI wrapper + validation against spec examples | E1 (generated models reject valid examples) is the #1 error scenario. Catch it at generation time. |
| D7 | Test scope | Add all 7 critical tests | 7 critical tests cover regression, phase gates, and top error scenarios. 47 edge-case tests deferred. |

### Review Findings

**Architecture:**
1. `contracts/generated/` needs import-linter protection (D1). Phase A: isolation contract. Phase F: enforcement contract.
2. `contracts/semantic.py` wrapping location resolved (D2). Evolve in place, replace `osi_document: dict` with typed `osi_model`.
3. Storage column removal is a DDL change, not a migration (D3). Schema is already destructive.
4. `additive_dimensions` needs a dedicated storage column (D4). The only spec-defined metric extension field.
5. `semantic_service.py` is the largest migration target: 9 `primary_time_field` references across 4 methods.

**Code quality:**
6. Generation script must validate against spec examples (D6). ~80 lines, catches E1 at generation time.
7. DRY: extract metric extension enrichment helper during Phase B. Copy-pasted at 3 locations in `semantic_service.py`.
8. `intents.py` and `intent_responses.py` both have zero importers. Can be deleted in Phase C (confirming CEO Finding #8).

**Tests:**
9. 7 critical tests added: storage roundtrip (REGRESSION), generation validation, E2E flow, TimeScope.field, additive_dimensions, MCP E2E, extension decode failure.
10. 2 silent failure modes flagged: storage roundtrip data loss and TimeScope.field referencing non-existent field.

**Performance:**
11. Skipped per user request. CEO Finding #6 (derived operation latency) remains open.

### Parallelization Strategy

Phases B (OSI semantic) and C (AOI atomic) can run in parallel worktrees — they touch different modules (`runtime/semantic/` vs `runtime/intents/`). After both merge, Phases D and E can run in parallel.

```
Phase A ──┬── Phase B (OSI semantic) ──┬── Phase D (MCP DTO) ──┬── Phase F
          └── Phase C (AOI atomic)  ───┤                       │
                                       └── Phase E (derived) ──┘
```

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | 11 decisions, 9 findings |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 7 decisions, 11 findings |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **UNRESOLVED:** 0
- **VERDICT:** CEO + ENG CLEARED — ready to implement
