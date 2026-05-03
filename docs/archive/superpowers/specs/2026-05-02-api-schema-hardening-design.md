---
status: archived
canonical-path: docs/api/
created: 2026-05-02
---

# API Schema Hardening — Non-Semantic Groups

**Date:** 2026-05-02
**Status:** approved for implementation

## Goal

Close the OpenAPI schema quality gap for five API groups currently excluded from the scoped quality
gate. The semantic layer and session/intent routes already pass `test_openapi_schema_quality.py`;
this work brings the remaining groups to the same standard so every public route produces a
deterministic JSON schema that agents can rely on.

**Success criteria:**

- No `dict[str, Any]`, `dict[str, object]`, or bare `Any` return types on any public route
- Every public request and response body uses a named Pydantic model or a typed scalar/list schema
- No `additionalProperties: true` in any scoped OpenAPI schema
- No array schema with missing or empty `items` in any scoped schema
- No schema leaf without `type`, `$ref`, composition, `enum`, or `const`
- The `SCOPED_PATH_PREFIXES` exclusion list in `test_openapi_schema_quality.py` is extended
  after each wave so the gate enforces the new schemas
- API docs for each group are updated to semantic.md quality: typed payload examples,
  component schema name references, error semantics, and `GET /openapi/schemas/{Name}` cross-refs

## Naming note

`sources.md` describes `/sources` endpoints but the router serves `/datasources` with
`datasource_type` and `datasource_id`. Docs will be corrected to match the code paths. Route
renames are out of scope.

## Common Patterns

All waves share these conventions, identical to the semantic layer:

### Model config

Every public request and response model uses `model_config = ConfigDict(extra="forbid")`. Internal
service models that never touch the wire do not require this.

### Discriminated unions

Polymorphic payload fields are typed with an `Annotated` union discriminated by a sibling
`Literal` type field:

```python
class DuckDbConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    datasource_type: Literal["duckdb"]
    path: str | None = None
    database: str | None = None   # alias accepted by service layer
    db_path: str | None = None    # alias accepted by service layer

class TrinoConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    datasource_type: Literal["trino"]
    host: str
    port: int = 8080
    user: str | None = None
    catalog: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    http_scheme: Literal["http", "https"] = "http"
    session_properties: dict[str, str] = Field(default_factory=dict)

DatasourceConnection = Annotated[
    DuckDbConnection | TrinoConnection,
    Field(discriminator="datasource_type"),
]
```

### Delete responses

Typed two-field models — not bare `dict[str, str]`:

```python
class DatasourceDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["deleted"]
    datasource_id: str
```

### Timestamps

Response models use `datetime` fields. String timestamps from the service layer are coerced by
Pydantic at `model_validate` time.

### List responses

Where the service currently returns a bare list, the response becomes
`{"items": [...], "total": int}` using the existing `ListResponseBase[T]` generic from
`app/api/models/base.py`. This applies to jobs; sources, engines, and approvals currently return
bare lists and can be wrapped in the same wave.

### Scalar-typed params

`GovernanceCheckRequest.params` becomes `dict[str, str | int | float | bool | None]`. Step params
are too varied to close with a discriminated union at this stage; removing `Any` with a typed
scalar map is the correct trade-off.

### OpenAPI fragment cross-refs

Docs reference component schema names using:

```text
GET /openapi/schemas/{SchemaName}
GET /openapi/paths/{base64url-encoded-path}
```

These routes are already registered by `openapi_fragments.py`.

---

## Wave 1 — Datasources and Routing

**Affected paths:** `/datasources/**`, `/routing/**`, `/engines/**`, `/mappings/**`

**Rationale:** foundational setup surfaces; agents need these to discover and configure sources
and engines before doing any analysis work.

### New models

**Datasource connections (discriminated by `datasource_type`):**

```
DuckDbConnection
TrinoConnection
DatasourceConnection = Annotated[DuckDbConnection | TrinoConnection, Field(discriminator="datasource_type")]
```

**Datasource request/response models:**

| Model | Replaces |
|-------|---------|
| `DatasourceRegisterRequest` | `connection: dict[str, Any]` → `DatasourceConnection` |
| `DatasourceUpdateRequest` | `connection: dict[str, Any] \| None` → `DatasourceConnection \| None` |
| `DatasourceResponse` | `connection: dict[str, Any]` → `DatasourceConnection` |
| `DatasourceResponse.sync_mode` | `str` → `Literal["selected", "all", "none"]` (already typed on request; fix response) |
| `SyncTriggerResponse` | replaces `dict[str, object]` on `POST /datasources/{id}/sync` |
| `SyncJobStatusResponse` | replaces `dict[str, object]` on `GET /datasources/{id}/sync/{job_id}` |
| `SyncSelectionResponse` | replaces `dict[str, object]` in selections list |
| `SyncClearedResponse` | replaces `dict[str, str]` on `DELETE .../sync/selections` |
| `SyncSelectionDeletedResponse` | replaces `dict[str, str]` on `DELETE .../sync/selections/{id}` |
| `BrowseSchemaItem` | replaces `dict[str, object]` in schema browse list |
| `BrowseTableItem` | replaces `dict[str, object]` in table browse list |
| `TablePreviewColumn` | new: `name: str`, `type: str` |
| `TablePreviewResponse` | replaces `dict[str, object]` on catalog preview |
| `SourceObjectAuthorityLocator` | new: `catalog: str \| None`, `schema: str \| None`, `table: str \| None` |
| `SourceObjectResponse` | replaces `dict[str, object]` on objects endpoints |

**Engine connections (discriminated by `engine_type`):**

```
DuckDbEngineConnection
TrinoEngineConnection
EngineConnection = Annotated[DuckDbEngineConnection | TrinoEngineConnection, Field(discriminator="engine_type")]
```

**Engine auth (discriminated by `mode`):**

```
NoneAuth(mode: Literal["none"])
UsernameOnlyAuth(mode: Literal["username_only"], username_source: Literal["session_user", "fixed"], fallback_username: str | None)
EngineAuth = Annotated[NoneAuth | UsernameOnlyAuth, Field(discriminator="mode")]
```

**Engine request/response models:**

| Model | Replaces |
|-------|---------|
| `EngineRegisterRequest` | `connection: dict[str, Any]` → `EngineConnection`; `auth: dict[str, Any]` → `EngineAuth` |
| `EngineUpdateRequest` | same substitutions, all fields optional |
| `EngineIntrinsicCapabilities` | new: typed read-only derived fields |
| `EngineDefaultNamespace` | new: `catalog: str \| None`, `schema: str \| None` |
| `EngineDeploymentCapabilities` | new: typed capability overrides |
| `EnginePolicy` | new: `allowed_step_types: list[str]`, `required_policy_support: list[str]` |
| `EngineResponse` | all sub-objects typed; replaces `dict[str, Any]` on engine routes |
| `EngineDeleteResponse` | typed delete result |

**Routing models:**

| Model | Replaces |
|-------|---------|
| `ExecutionLocator` | new: `catalog`, `schema`, `table`, `mapping_id`, `authority_catalog`, `execution_catalog`, `default_schema_applied`, `readiness_blockers`, `authority_locator` |
| `RoutingSourceDetail` | new: `candidate_engine_ids`, `ready_mapping_ids`, `failed_mappings`, `readiness_blockers` |
| `RoutingCandidate` | new: `engine_id`, `eligible`, `covered_sources`, `missing_sources`, `mapping_ids` |
| `RoutingDetail` | replaces `routing_detail: dict[str, Any]` in `RouteResolveResponse`: `resolution_status`, `selected_mapping_ids`, `execution_locators: dict[str, ExecutionLocator]`, `sources: dict[str, RoutingSourceDetail]`, `candidates`, `readiness_blockers`, `unresolved_tables` |
| `RouteCapabilityProfileResponse.metadata` | `metadata: dict[str, Any]` → `dict[str, str]` (capability metadata values are always strings; use typed scalar map) |

**Mappings models:**

`MappingCatalogEntry`, `MappingCreateRequest`, `MappingUpdateRequest`, `MappingResponse`,
`MappingDeleteResponse` — all currently return `dict[str, Any]`; each gets a named model.

### Route handler changes

Every handler returning `dict[str, Any]` / `dict[str, object]` / bare list gets an explicit
`response_model=` annotation and a typed return type.

### Quality gate extension

After Wave 1, add `/datasources`, `/routing`, `/engines`, `/mappings` to the scoped path set in
`test_openapi_schema_quality.py`.

### Docs updated

`sources.md` (corrected to `/datasources`), `engines.md`, `mappings.md`

---

## Wave 2 — Governance and Approvals

**Affected paths:** `/policies/**`, `/quality-rules/**`, `/governance/**`, `/approvals/**`,
`/sessions/{session_id}/approvals/**`

**Rationale:** agents need clear, machine-readable policy and decision contracts to reason about
what operations are permitted before submitting steps.

### New models

**Policy definition (discriminated by `policy_type`):**

```
AggregateOnlyDefinition(policy_type: Literal["aggregate_only"], min_group_size: int | None)
FieldMaskDefinition(policy_type: Literal["field_mask"], columns: list[str], mask_value: str = "***")
RowFilterDefinition(policy_type: Literal["row_filter"], filter_expr: str, reason: str = "")
MaxRowsDefinition(policy_type: Literal["max_rows"], limit: int)
PolicyDefinition = Annotated[..., Field(discriminator="policy_type")]
```

**Policy scope:**

```
PolicyScope(tables: list[str] | None, sources: list[str] | None, step_types: list[str] | None)
```

**Quality rule threshold (discriminated by `rule_type`):**

```
FreshnessThreshold(rule_type: Literal["freshness"], max_age_hours: int)
NullRateThreshold(rule_type: Literal["null_rate"], column: str, max_null_rate: float)
RowCountMinThreshold(rule_type: Literal["row_count_min"], min_rows: int)
QualityRuleThreshold = Annotated[..., Field(discriminator="rule_type")]
```

**Request/response models:**

| Model | Replaces |
|-------|---------|
| `PolicyCreateRequest` | `definition: dict[str, Any]` → `PolicyDefinition`; `scope: dict[str, Any]` → `PolicyScope` |
| `PolicyUpdateRequest` | same substitutions, all fields optional |
| `PolicyResponse` | `dict[str, Any]` → named model with `policy_id`, `name`, `policy_type`, `definition: PolicyDefinition`, `scope: PolicyScope`, `enabled`, timestamps |
| `PolicyDeleteResponse` | typed delete result |
| `QualityRuleCreateRequest` | `threshold: dict[str, Any]` → `QualityRuleThreshold` |
| `QualityRuleResponse` | `dict[str, Any]` → named model with all fields |
| `QualityRuleDeleteResponse` | typed delete result |
| `GovernanceCheckRequest` | `params: dict[str, Any]` → `dict[str, str \| int \| float \| bool \| None]` |
| `GovernanceViolation` | new: `policy_id`, `policy_name`, `policy_type`, `message` |
| `GovernanceWarning` | new: `policy_id`, `policy_name`, `message` |
| `GovernanceCheckResponse` | `dict[str, Any]` → `passed: bool`, `violations: list[GovernanceViolation]`, `warnings: list[GovernanceWarning]` |
| `ApprovalResponse` | replaces all `dict[str, object]` on approval routes: `request_id`, `session_id`, `rec_id`, `status`, `reviewer`, `reason`, timestamps |
| `ApprovalListResponse` | `ListResponseBase[ApprovalResponse]` |

### Quality gate extension

After Wave 2, add `/policies`, `/quality-rules`, `/governance`, `/approvals` to the scoped path set.

### Docs updated

`governance.md` (full rewrite to semantic.md quality standard)

---

## Wave 3 — Jobs and Metrics

**Affected paths:** `/jobs/**`, `/metrics`

**Rationale:** operational state surfaces; agents need typed job status to poll async work and
understand failure modes.

### `/metrics` special case

`GET /metrics` is a system telemetry endpoint serving either Prometheus text or a raw snapshot
dict. It is not an agent-facing data contract. This route is **excluded** from schema hardening
and marked in the quality gate as an explicit carve-out, not a schema violation.

### New models

**Job payload:**

```
JobPayload(step_type: str, params: dict[str, str | int | float | bool | None])
```

`payload` is read-only execution context, not an authoring form. A typed envelope with scalar
params is sufficient; per-step-type discrimination is out of scope.

**Job response:**

```
JobResponse(
    job_id: str,
    session_id: str,
    job_type: str,
    status: Literal["pending", "running", "completed", "failed", "cancelled"],
    payload: JobPayload,
    error_message: str | None,
    created_at: datetime,
    updated_at: datetime,
    submitted_at: datetime | None,
    started_at: datetime | None,
    completed_at: datetime | None,
)
```

`JobListResponse = ListResponseBase[JobResponse]` — the bare list currently returned by
`GET /jobs` becomes `{"items": [...], "total": int}`.

### Quality gate extension

After Wave 3, add `/jobs` to the scoped path set. The `/metrics` route is added to an explicit
exclusion comment in the test.

### Docs updated

`jobs.md` updated with typed shapes and list envelope. New `metrics.md` created to document the
Prometheus/snapshot surface and its agent-facing contract (or explicit non-contract status).

---

## Wave 4 — Calendar

**Affected paths:** `/calendar/**`

**Rationale:** calendar is noted as "mostly typed already" in the follow-up doc. This wave is a
targeted sweep to close any remaining `dict[str, Any]` leaves and extend the gate.

### Approach

1. Read `app/api/calendar.py` and `app/api/models/calendar.py` and enumerate any remaining
   untyped fields
2. Close each violation with a named model or typed field
3. Add `/calendar` to the scoped path set

### Docs updated

`calendar.md` created if it does not exist; existing context-surface references updated.

---

## Wave 5 — Catalog Legacy and OpenAPI Meta (deferred)

`/catalog` legacy routes (if any remain registered) and `/openapi/*` meta routes are deferred to
a future wave after the primary API surface is stable, as noted in
`docs/api/openapi-schema-hardening-followups.md`.

---

## File Locations

All new models live in `app/api/models/_infrastructure.py`. The file already holds all
non-semantic infrastructure models; new models follow the existing section structure
(`# === Datasource models ===`, etc.).

The quality gate is `tests/test_openapi_schema_quality.py`. The scoped path set grows after each
wave; the final state removes the path filter entirely (global enforcement).

---

## Exit Criteria

- Every public request and response body on `/datasources/**`, `/routing/**`, `/engines/**`,
  `/mappings/**`, `/policies/**`, `/quality-rules/**`, `/governance/**`, `/approvals/**`,
  `/jobs/**`, and `/calendar/**` uses a named Pydantic model
- `test_openapi_schema_quality.py` covers all of the above paths without violations
- `docs/api/sources.md`, `engines.md`, `mappings.md`, `governance.md`, `jobs.md`, and
  `calendar.md` contain full typed payload examples, component schema name references,
  and error semantics sections
- The follow-up doc `docs/api/openapi-schema-hardening-followups.md` is updated to reflect
  which groups have been closed and which remain open
