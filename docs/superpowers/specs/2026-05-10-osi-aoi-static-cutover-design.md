---
status: draft
created: 2026-05-10
---

# OSI/AOI Static Model Cutover Design

**Date:** 2026-05-10
**Status:** Draft (brainstorming approved)
**Scope:** Generate static Pydantic models from `osi-marivo-spec` and `aoi-spec`, then cut Marivo's semantic and analysis-operation runtime over to those generated contracts.

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

The generated model layer is the only canonical schema implementation. Transport DTOs may simplify the caller experience, but they must convert into generated OSI/AOI models before business logic runs.

### 3.1 Contract Ownership

| Layer | Owns | Must not own |
|---|---|---|
| `osi-marivo-spec/`, `aoi-spec/` | JSON Schema truth source | Runtime behavior |
| `marivo/contracts/generated/` | Static Pydantic model code generated from schema | Hand-written business logic |
| Runtime semantic layer | OSI validation, storage mapping, semantic resolution | MCP-specific shortcuts |
| Runtime intent layer | AOI atomic execution and artifact creation | Legacy typed-intent semantics as primary path |
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

### 4.3 Public Imports

Generated modules are the canonical import target for runtime code. Existing transport model modules have these target roles:

- `marivo/transports/http/models/osi.py` re-exports generated OSI models while preserving current FastAPI import paths during the cutover.
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
- typed extension data into storage columns such as `datasource_id` and metric additivity fields
- storage rows back into generated OSI objects

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
- primary time field or time dimension inference if still required by runtime

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
