---
status: archived
canonical-path: docs/api/openapi.md
created: 2026-05-01
---

# OpenAPI Schema Contract Hardening Design

## Context

Marivo serves agents through HTTP APIs. The intended contract shape is
`application/json` transport plus typed Pydantic models, JSON Schema, and
OpenAPI. The current OpenAPI surface does not fully meet that bar.

The latest schema audit found 69 paths and 65 component schemas, but many
agent-facing responses collapse to free-form objects such as:

```json
{"type": "object", "additionalProperties": true}
```

That weakens the contract for agents because they can see that JSON is returned
but cannot know which fields, references, status values, artifacts, or evidence
objects are stable.

The largest gaps are:

- Session and intent routes return `dict[str, object]` or `dict[str, Any]`,
  causing success responses to lose typed schema.
- Semantic-model routes already have OSI Pydantic models available, but the
  routes accept and return `dict[str, Any]`, so those models are not exposed in
  OpenAPI.
- A small number of request fields still use free-form dictionaries, including
  scope constraints, deprecated predicates, session budget, session policy, and
  state query slices.
- Non-core APIs such as datasources, governance, jobs, approvals, metrics,
  calendar, catalog stubs, and OpenAPI fragment routes also have schema debt but
  are outside this phase.

## Goal

Make the semantic layer and session/intent API surfaces conform to an
agent-friendly OpenAPI contract:

- JSON remains the wire format.
- Pydantic models become the source of truth for request and response schemas.
- OpenAPI for the scoped APIs exposes typed request and response contracts.
- Scoped APIs have no `additionalProperties: true`, no empty `items`, and no
  schema leaf that lacks `type`, `$ref`, composition, enum, or const.
- Remaining non-scoped API schema debt is recorded in a dedicated follow-up
  TODO document instead of being silently ignored.

## Scope

This phase covers:

- `/semantic-models`
- `/semantic-models/import`
- `/semantic-models/{model}`
- `/semantic-models/{model}/datasets/**`
- `/semantic-models/{model}/relationships/**`
- `/semantic-models/{model}/metrics/**`
- `/semantic-models/{model}/readiness`
- `/sessions`
- `/sessions/{session_id}`
- `/sessions/{session_id}/state`
- `/sessions/{session_id}/state/query`
- `/sessions/{session_id}/runtime-status`
- `/sessions/{session_id}/artifacts/{artifact_id}/runtime-status`
- `/sessions/{session_id}/propositions/{proposition_id}/context`
- `/sessions/{session_id}/propositions/{proposition_id}/runtime-status`
- `/sessions/{session_id}/intents/*`
- `/sessions/{session_id}/terminate`

This phase does not change runtime behavior, storage schema, execution
semantics, lifecycle semantics, or service-side result construction beyond the
minimum needed to expose typed HTTP contracts.

## Non-Goals

This phase does not harden these API groups:

- datasources
- routing
- governance
- jobs
- approvals
- metrics
- calendar
- `/catalog` legacy or stub routes
- `/openapi/*` meta routes

Those surfaces must be listed in the follow-up TODO document with their current
schema violation categories and a proposed hardening order.

## Design

### 1. Semantic-Model API

The semantic-model API should use the existing OSI-aligned Pydantic models as
the external contract instead of accepting generic dictionaries.

Use `docs/api/osi-marivo-schema.json` as the reference schema for
`/semantic-models/import` and the semantic object request/response models. The
Pydantic models should align with that schema, including OSI 0.1.1 fields and
MARIVO vendor extension structures, instead of defining an unrelated parallel
shape.

Target shape:

- `POST /semantic-models/import` accepts `OSIDocument`.
- `GET /semantic-models` returns `OSIDocument` or a named list envelope whose
  `semantic_model` field is `list[SemanticModel]`.
- `POST /semantic-models` and `PUT /semantic-models/{model}` accept typed
  semantic model payloads or a named partial-update model.
- Dataset, relationship, and metric CRUD routes accept and return `Dataset`,
  `Relationship`, and `Metric` models or named update models.
- `GET /semantic-models/{model}/readiness` returns a named readiness response
  model with explicit status, blockers, and object-level details.

If update routes need partial semantics, introduce explicit update models rather
than `dict[str, Any]`.

### 2. Session Lifecycle And State API

Session routes should expose named response envelopes:

- `SessionCreateResponse`
- `SessionListResponse`
- `SessionDetailResponse`
- `SessionTerminateResponse`
- `SessionRuntimeStatusResponse`
- `ArtifactRuntimeStatusResponse`
- `PropositionRuntimeStatusResponse`
- `SessionStateView`
- `PropositionContextView`

Existing service return payloads may remain dictionaries internally, but route
handlers must validate or serialize them through Pydantic response models so
OpenAPI reflects the stable external contract.

Request-side free-form objects should be replaced with named models:

- `SessionBudget`
- `SessionPolicy`
- `SessionStateSlice`

If a field is intentionally extensible, it must be represented with a named
extension field whose value schema is explicit. It must not use
`additionalProperties: true`.

### 3. Intent API

Intent request models are mostly typed already. The key work is response
modeling.

Each intent route should expose a typed response model:

- `ObserveResponse`
- `CompareResponse`
- `DecomposeResponse`
- `CorrelateResponse`
- `DetectResponse`
- `IntentTestResponse`
- `ForecastResponse`
- `AttributeResponse`
- `DiagnoseResponse`
- `ValidateResponse`

The response models should include stable agent-facing fields:

- `schema_version`
- `step_ref`
- `artifact_id`
- `artifact_type`
- normalized request or scope details when part of the artifact contract
- source lineage
- analytical metadata
- execution metadata
- result payload appropriate to the intent

For the first pass, result payloads may be modeled at envelope and row-shape
level rather than fully reworking every internal artifact type, but every JSON
leaf must still have a schema. Use named nested models and scalar unions rather
than `Any`.

### 4. JSON Value Modeling

Replace unbounded `dict[str, Any]` fields on scoped APIs with explicit JSON
value aliases or named models.

Allowed patterns:

- `JsonScalar = str | int | float | bool | None`
- `ScalarMap = dict[str, JsonScalar]`
- Named models for known objects such as budget, policy, slice, metadata,
  lineage, status, and blockers
- Named extension containers where extensibility is explicitly part of the
  contract

Disallowed on scoped APIs:

- `Any`
- `object`
- `dict[str, Any]`
- `dict[str, object]`
- `list[dict[str, Any]]`
- `additionalProperties: true`
- arrays with missing or empty `items`
- schema nodes with description/title/default but no type, ref, composition,
  enum, or const

### 5. OpenAPI Quality Test

Add an OpenAPI schema quality test that builds a router-only FastAPI app and
scans the generated OpenAPI document.

For scoped paths, the test must fail on:

- `additionalProperties: true`
- empty object schemas used as leaves
- arrays without typed `items`
- schema leaves that have no `type`, `$ref`, `oneOf`, `anyOf`, `allOf`, `enum`,
  or `const`

The test should report exact JSON-pointer-like paths for violations.

For non-scoped paths, the test should not fail in this phase. Instead, the
follow-up TODO document should capture the remaining violation groups. The
eventual target is to remove the scope restriction and enforce the same quality
rule globally.

## Validation

Implementation should run:

- `make test` or the narrow affected `.venv/bin/pytest ...` command while the
  suite is being iterated.
- `make typecheck`
- `make lint`

The scoped OpenAPI quality test must pass before the work is complete.

Manual validation should inspect at least:

- `/semantic-models/import` request schema references `OSIDocument`.
- `OSIDocument`, `SemanticModel`, `Dataset`, `Relationship`, and `Metric`
  schemas align with `docs/api/osi-marivo-schema.json`.
- `/sessions/{session_id}/intents/observe` response schema references
  `ObserveResponse`.
- Scoped paths have no `additionalProperties: true`, empty `items`, or untyped
  leaves.
- Non-scoped violations are represented in the follow-up TODO document.

## Risks

The main risk is over-modeling internal artifacts as public contracts. Avoid
that by modeling only stable agent-facing surfaces and keeping storage or
runtime-only details internal.

Another risk is introducing response models that do not match actual service
payloads. Mitigate this with focused endpoint tests for representative semantic,
session, and intent routes.

## Acceptance Criteria

- Scoped semantic-model and session/intent routes expose typed request and
  response schemas in OpenAPI.
- No scoped route contains `additionalProperties: true`, empty `items`, or an
  untyped schema leaf.
- Existing behavior remains unchanged except for stricter request/response
  contract validation where the previous contract was under-specified.
- A follow-up TODO document records all non-scoped API schema debt.
- Relevant tests, typecheck, and lint pass or any inability to run them is
  explicitly documented.
