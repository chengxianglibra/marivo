---
status: archived
created: 2026-05-02
---

# API Schema Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the OpenAPI schema quality gap for four API groups — datasources/routing, governance/approvals, jobs, and calendar — so every public route produces a deterministic JSON schema that agents can rely on.

**Architecture:** Each wave adds named Pydantic response models to routes that currently return `dict[str, Any]` / `dict[str, object]`, replaces polymorphic `dict[str, Any]` request fields with discriminated unions, then extends the OpenAPI quality gate test to enforce the new schemas. Docs are updated alongside code in each wave.

**Tech Stack:** FastAPI, Pydantic v2, pytest. Python tools via `.venv/bin/` (e.g., `.venv/bin/pytest`, `.venv/bin/mypy`). Use `make test` and `make typecheck` rather than calling tools directly.

---

## File Map

**Modified files — models:**
- `app/api/models/_infrastructure.py` — all new infrastructure models (connection unions, response models, governance models, job models)
- `app/api/models/calendar.py` — add `model_config = ConfigDict(extra="forbid")` to existing models

**Modified files — routers:**
- `app/api/datasources.py` — add `response_model=` + typed return types
- `app/api/routing.py` — update `RouteResolveResponse` to use structured `RoutingDetail`
- `app/api/governance.py` — add `response_model=` + typed return types
- `app/api/approvals.py` — add `response_model=` + typed return types
- `app/api/jobs.py` — add `response_model=` + typed return types

**Modified files — models exports:**
- `app/api/models/__init__.py` — re-export every new model

**Modified files — test:**
- `tests/test_openapi_schema_quality.py` — extend `SCOPED_PATH_PREFIXES` after each wave

**Modified files — docs (one update per wave):**
- `docs/api/sources.md` — Wave 1
- `docs/api/governance.md` — Wave 2
- `docs/api/jobs.md` — Wave 3
- (Calendar docs: existing context-surface references, or new `docs/api/calendar.md`)

---

## Wave 1 — Datasources and Routing

> **Scope:** `/datasources/**` and `/routing/**`. `/engines` and `/mappings` routers do not exist yet; they are deferred.

---

### Task 1: Extend quality gate — Wave 1 paths (RED)

**Files:**
- Modify: `tests/test_openapi_schema_quality.py:10`

- [ ] Open `tests/test_openapi_schema_quality.py` and change `SCOPED_PATH_PREFIXES`:

```python
SCOPED_PATH_PREFIXES = (
    "/semantic-models",
    "/datasources",
    "/routing",
)
```

- [ ] Run the test to confirm it now fails with schema violations:

```bash
make test -- -k test_scoped_openapi_schemas_are_agent_friendly
```

Expected: FAIL — violations for routes returning `dict[str, Any]` / `dict[str, object]` without `response_model` (these generate `{}` response schemas).

- [ ] Commit the failing test:

```bash
git add tests/test_openapi_schema_quality.py
git commit -m "test: scope datasources and routing paths in openapi quality gate (RED)"
```

---

### Task 2: Datasource connection discriminated union

**Files:**
- Modify: `app/api/models/_infrastructure.py` (after the `DatasourcePolicyResponse` class, around line 90)

- [ ] Add the connection union models. Find the `# === Routing models ===` comment and insert BEFORE it:

```python
# =============================================================================
# Datasource connection models (discriminated union)
# =============================================================================


class DuckDbDatasourceConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    datasource_type: Literal["duckdb"]
    path: str | None = None
    database: str | None = None
    db_path: str | None = None


class TrinoDatasourceConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    datasource_type: Literal["trino"]
    host: str
    port: int = 8080
    user: str | None = None
    catalog: str | None = None
    http_scheme: Literal["http", "https"] = "http"
    session_properties: dict[str, str] = Field(default_factory=dict)


DatasourceConnection = Annotated[
    DuckDbDatasourceConnection | TrinoDatasourceConnection,
    Field(discriminator="datasource_type"),
]
```

- [ ] Add `Annotated` to the imports at the top of `_infrastructure.py`:

```python
from typing import Annotated, Any, Literal
```

- [ ] Update `DatasourceRegisterRequest`, `DatasourceUpdateRequest`, and `DatasourceResponse` to use the union:

```python
class DatasourceRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasource_type: Literal["duckdb", "trino"]
    display_name: str
    connection: DatasourceConnection = Field(default_factory=dict)  # type: ignore[assignment]
    sync_mode: Literal["selected", "all", "none"] = "selected"
    policy: DatasourcePolicyPayload = Field(default_factory=DatasourcePolicyPayload)

    @model_validator(mode="before")
    @classmethod
    def _inject_type_into_connection(cls, data: Any) -> Any:
        if isinstance(data, dict) and "datasource_type" in data:
            conn = data.get("connection")
            if isinstance(conn, dict) and "datasource_type" not in conn:
                data["connection"] = {**conn, "datasource_type": data["datasource_type"]}
        return data


class DatasourceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    connection: DatasourceConnection | None = None
    sync_mode: Literal["selected", "all", "none"] | None = None
    policy: DatasourcePolicyPayload | None = None


class DatasourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasource_id: str
    datasource_type: Literal["duckdb", "trino"]
    display_name: str
    connection: DatasourceConnection
    sync_mode: Literal["selected", "all", "none"] = "selected"
    policy: DatasourcePolicyResponse
    status: Literal["active", "inactive", "deprecated"] = "active"
    readiness_status: Literal["not_ready", "ready"] = "not_ready"
    failure_code: str | None = None
    created_at: str = ""
    updated_at: str = ""
```

> Note: `DatasourceUpdateRequest.connection` does not need the `model_validator` because the caller supplies a full connection object (with `datasource_type` inside) when updating.

- [ ] Run `make typecheck` to catch any type errors introduced:

```bash
make typecheck
```

---

### Task 3: Datasource sub-resource response models

**Files:**
- Modify: `app/api/models/_infrastructure.py` (append to the datasource section)

- [ ] Add all sub-resource response models after `DatasourceDeleteResponse`:

```python
class SyncTriggerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    datasource_id: str
    status: Literal["succeeded"]


class SyncJobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    datasource_id: str
    job_type: str
    status: Literal["pending", "running", "completed", "failed"]
    started_at: str | None = None
    finished_at: str | None = None
    objects_synced: int | None = None
    error_message: str | None = None


class SyncSelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_id: str
    datasource_id: str
    schema_name: str
    table_name: str
    created_at: str


class SyncClearedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["cleared"]
    datasource_id: str


class SyncSelectionDeletedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["deleted"]
    selection_id: str


class BrowseSchemaItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: str
    table_count: int


class BrowseTableItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_name: str
    schema_name: str
    row_count: int | None = None
    column_count: int | None = None


class TablePreviewColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str


class TablePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    schema_name: str
    table_name: str
    columns: list[TablePreviewColumn]
    rows: list[dict[str, str | int | float | bool | None]]
    row_count: int
    truncated: bool
    limit_requested: int
    limit_applied: int
    filters_applied: dict[str, str | int | float | bool | None] | None = None


class SourceObjectAuthorityLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    table: str | None = None


class SourceObjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    source_id: str
    object_type: str
    parent_id: str | None = None
    native_name: str
    native_id: str | None = None
    fqn: str | None = None
    authority_locator: SourceObjectAuthorityLocator | None = None
    properties: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    sync_version: str | None = None
    synced_at: str | None = None


class ObjectPropertiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    properties: dict[str, str | int | float | bool | None]
```

---

### Task 4: Routing detail structured model

**Files:**
- Modify: `app/api/models/_infrastructure.py` (update the `# === Routing models ===` section)

- [ ] Add structured routing detail models and update `RouteResolveResponse`. Replace the existing `RouteResolveResponse` definition:

```python
class AuthorityLocatorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    table: str | None = None


class ExecutionLocatorEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    table: str | None = None
    mapping_id: str | None = None
    authority_catalog: str | None = None
    execution_catalog: str | None = None
    default_schema_applied: bool = False
    readiness_blockers: list[str] = Field(default_factory=list)
    authority_locator: AuthorityLocatorDetail | None = None


class RoutingSourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_engine_ids: list[str] = Field(default_factory=list)
    ready_mapping_ids: list[str] = Field(default_factory=list)
    failed_mappings: list[str] = Field(default_factory=list)
    readiness_blockers: list[str] = Field(default_factory=list)


class RoutingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_id: str
    eligible: bool
    covered_sources: list[str] = Field(default_factory=list)
    missing_sources: list[str] = Field(default_factory=list)
    mapping_ids: list[str] = Field(default_factory=list)


class RoutingDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")  # routing engine may emit extra diagnostic keys

    resolution_status: str = ""
    selected_mapping_ids: list[str] = Field(default_factory=list)
    execution_locators: dict[str, ExecutionLocatorEntry] = Field(default_factory=dict)
    sources: dict[str, RoutingSourceSummary] = Field(default_factory=dict)
    candidates: list[RoutingCandidate] = Field(default_factory=list)
    readiness_blockers: list[str] = Field(default_factory=list)
    unresolved_tables: list[str] = Field(default_factory=list)
```

- [ ] Update `RouteResolveResponse` to use `RoutingDetail`:

```python
class RouteResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved: bool = Field(description="Whether routing resolved to a concrete execution engine.")
    failure_code: str | None = Field(default=None)
    table_names: list[str] = Field(default_factory=list)
    engine: RouteEngineResponse | None = Field(default=None)
    qualified_names: dict[str, str] = Field(default_factory=dict)
    selection_reason: str | None = Field(default=None)
    routing_detail: RoutingDetail = Field(default_factory=RoutingDetail)
    capability_profile: RouteCapabilityProfileResponse | None = Field(default=None)
```

- [ ] Also update `RouteCapabilityProfileResponse.metadata` from `dict[str, Any]` to `dict[str, str]`:

```python
class RouteCapabilityProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_type: str = Field(description="Engine type associated with this capability profile.")
    supported_sql_features: list[str] = Field(default_factory=list)
    supported_step_types: list[str] = Field(default_factory=list)
    materialization_support: str = Field(description="Materialization mode advertised by the engine.")
    policy_support: list[str] = Field(default_factory=list)
    performance_class: str = Field(description="Performance class used during routing.")
    min_staleness_minutes: int | None = Field(default=None)
    federation_support: str = Field(description="Federation capability advertised by the engine.")
    metadata: dict[str, str] = Field(default_factory=dict)
```

- [ ] Update `app/api/routing.py` to wrap `routing_detail` with the new model:

```python
# In the resolved branch:
routing_detail=RoutingDetail.model_validate(route.routing_detail),

# In the failure branch:
routing_detail=RoutingDetail.model_validate(failure.routing_detail if failure else {}),
```

Import `RoutingDetail` at the top of `routing.py`:

```python
from app.api.models import (
    RouteEngineResponse,
    RouteResolveRequest,
    RouteResolveResponse,
    RoutingDetail,
)
```

---

### Task 5: Wire datasource response models into route handlers

**Files:**
- Modify: `app/api/datasources.py`

- [ ] Update the imports at the top of `datasources.py` to include all new models:

```python
from app.api.models import (
    BrowseSchemaItem,
    BrowseTableItem,
    ColumnPropertiesUpdateRequest,
    DatasourceDeleteResponse,
    DatasourceRegisterRequest,
    DatasourceResponse,
    DatasourceUpdateRequest,
    ObjectPropertiesResponse,
    SourceObjectResponse,
    SyncClearedResponse,
    SyncJobStatusResponse,
    SyncSelectionDeletedResponse,
    SyncSelectionResponse,
    SyncSelectionRequest,
    SyncTriggerResponse,
    TablePreviewResponse,
    TablePreviewColumn,
)
```

- [ ] Update each route handler signature to use the typed return + `response_model=`:

```python
@router.delete("/datasources/{datasource_id}", response_model=DatasourceDeleteResponse)
def delete_datasource(datasource_id: str, request: Request) -> DatasourceDeleteResponse:
    services = get_services(request)
    try:
        services.datasource_service.delete_datasource(datasource_id)
        return DatasourceDeleteResponse(status="deleted", datasource_id=datasource_id)
    except KeyError as error:
        raise _http_error(error) from error
    except DependencyError as error:
        raise HTTPException(
            status_code=409, detail={"message": str(error), "dependencies": error.dependencies}
        ) from error


@router.post("/datasources/{datasource_id}/sync", response_model=SyncTriggerResponse)
def trigger_sync(datasource_id: str, request: Request) -> SyncTriggerResponse:
    # ... existing logic unchanged ...
    return SyncTriggerResponse(
        job_id=job_id, datasource_id=datasource_id, status="succeeded"
    )


@router.get(
    "/datasources/{datasource_id}/sync/selections",
    response_model=list[SyncSelectionResponse],
)
def list_sync_selections(
    datasource_id: str, request: Request
) -> list[SyncSelectionResponse]:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    rows = services.datasource_service.list_sync_selections(datasource_id)
    return [SyncSelectionResponse.model_validate(r) for r in rows]


@router.post(
    "/datasources/{datasource_id}/sync/selections",
    response_model=list[SyncSelectionResponse],
)
def add_sync_selections(
    datasource_id: str, payload: SyncSelectionRequest, request: Request
) -> list[SyncSelectionResponse]:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    results = []
    for selection in payload.selections:
        row = services.datasource_service.add_sync_selection(
            datasource_id,
            schema_name=selection.schema_name,
            table_name=selection.table_name,
        )
        results.append(SyncSelectionResponse.model_validate(row))
    return results


@router.delete(
    "/datasources/{datasource_id}/sync/selections",
    response_model=SyncClearedResponse,
)
def clear_sync_selections(
    datasource_id: str, request: Request
) -> SyncClearedResponse:
    try:
        get_services(request).datasource_service.clear_sync_selections(datasource_id)
        return SyncClearedResponse(status="cleared", datasource_id=datasource_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete(
    "/datasources/{datasource_id}/sync/selections/{selection_id}",
    response_model=SyncSelectionDeletedResponse,
)
def remove_sync_selection(
    datasource_id: str, selection_id: str, request: Request
) -> SyncSelectionDeletedResponse:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
        services.datasource_service.remove_sync_selection(selection_id)
        return SyncSelectionDeletedResponse(status="deleted", selection_id=selection_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/datasources/{datasource_id}/sync/{job_id}",
    response_model=SyncJobStatusResponse,
)
def get_sync_status(
    datasource_id: str, job_id: str, request: Request
) -> SyncJobStatusResponse:
    try:
        row = get_services(request).sync_engine.get_sync_status(job_id)
        return SyncJobStatusResponse.model_validate(row)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/datasources/{datasource_id}/browse/schemas",
    response_model=list[BrowseSchemaItem],
)
def browse_catalog_schemas(
    datasource_id: str, request: Request
) -> list[BrowseSchemaItem]:
    try:
        rows = get_services(request).datasource_service.browse_catalog_schemas(datasource_id)
        return [BrowseSchemaItem.model_validate(r) for r in rows]
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/datasources/{datasource_id}/browse/tables",
    response_model=list[BrowseTableItem],
)
def browse_catalog_tables(
    datasource_id: str, request: Request, schema_name: str | None = Query(None)
) -> list[BrowseTableItem]:
    try:
        if schema_name is None:
            raise ValueError("schema_name query parameter is required")
        rows = get_services(request).datasource_service.browse_catalog_tables(
            datasource_id, schema_name=schema_name
        )
        return [BrowseTableItem.model_validate(r) for r in rows]
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get(
    "/datasources/{datasource_id}/catalog/preview",
    response_model=TablePreviewResponse,
)
def preview_table(
    datasource_id: str,
    request: Request,
    schema: str = Query(...),
    table: str = Query(...),
    limit: int = Query(default=100, ge=1),
    columns: str | None = Query(default=None),
    filters: str | None = Query(default=None),
) -> TablePreviewResponse:
    services = get_services(request)
    column_list = None
    if columns:
        column_list = [c.strip() for c in columns.split(",") if c.strip()] or None
    try:
        filter_map = _parse_preview_filters(filters)
        result = services.datasource_service.preview_table(
            datasource_id=datasource_id,
            schema_name=schema,
            table_name=table,
            limit=limit,
            columns=column_list,
            filters=filter_map,
        )
        return TablePreviewResponse.model_validate(result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch(
    "/datasources/{datasource_id}/objects/{object_id}/properties",
    response_model=ObjectPropertiesResponse,
)
def patch_column_properties(
    datasource_id: str,
    object_id: str,
    payload: ColumnPropertiesUpdateRequest,
    request: Request,
) -> ObjectPropertiesResponse:
    services = get_services(request)
    user_props = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        result = services.datasource_service.patch_object_properties(
            datasource_id, object_id, user_props
        )
        return ObjectPropertiesResponse.model_validate(result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get(
    "/datasources/{datasource_id}/objects/{object_id}",
    response_model=SourceObjectResponse,
)
def get_datasource_object(
    datasource_id: str, object_id: str, request: Request
) -> SourceObjectResponse:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
        row = services.datasource_service.get_object(datasource_id, object_id)
        return SourceObjectResponse.model_validate(row)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/datasources/{datasource_id}/objects",
    response_model=list[SourceObjectResponse],
)
def list_datasource_objects(
    datasource_id: str,
    request: Request,
    type: str | None = Query(default=None),
    schema: str | None = Query(default=None, alias="schema"),
) -> list[SourceObjectResponse]:
    services = get_services(request)
    try:
        services.datasource_service.get_datasource(datasource_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    rows = services.datasource_service.list_objects(
        datasource_id, object_type=type, schema_name=schema
    )
    return [SourceObjectResponse.model_validate(r) for r in rows]
```

---

### Task 6: Export new models and verify Wave 1 (GREEN)

**Files:**
- Modify: `app/api/models/__init__.py`

- [ ] Add all new Wave 1 models to the `from ._infrastructure import (` block and to the `__all__` list:

```python
from ._infrastructure import (
    # ... existing imports ...
    # New Wave 1 datasource models
    AuthorityLocatorDetail,
    BrowseSchemaItem,
    BrowseTableItem,
    DuckDbDatasourceConnection,
    ExecutionLocatorEntry,
    ObjectPropertiesResponse,
    RoutingCandidate,
    RoutingDetail,
    RoutingSourceSummary,
    SourceObjectAuthorityLocator,
    SourceObjectResponse,
    SyncClearedResponse,
    SyncJobStatusResponse,
    SyncSelectionDeletedResponse,
    SyncSelectionResponse,
    SyncTriggerResponse,
    TablePreviewColumn,
    TablePreviewResponse,
    TrinoDatasourceConnection,
)
```

- [ ] Run tests and typecheck:

```bash
make test
make typecheck
```

Expected: `test_scoped_openapi_schemas_are_agent_friendly` now passes for `/datasources/**` and `/routing/**`.

- [ ] Fix any type errors or validation failures before committing.

- [ ] Commit Wave 1 implementation:

```bash
git add app/api/models/_infrastructure.py \
        app/api/models/__init__.py \
        app/api/datasources.py \
        app/api/routing.py \
        tests/test_openapi_schema_quality.py
git commit -m "feat: Wave 1 — typed schemas for datasources and routing"
```

---

### Task 7: Update sources.md doc

**Files:**
- Modify: `docs/api/sources.md`

- [ ] Add a **Component Schemas** section at the top after the endpoint table, listing the named models:

```markdown
## Component Schemas

| Schema name | Used by |
|-------------|---------|
| `DatasourceRegisterRequest` | `POST /datasources` request |
| `DatasourceUpdateRequest` | `PUT /datasources/{id}` request |
| `DatasourceResponse` | all datasource CRUD responses |
| `DuckDbDatasourceConnection` | `connection` variant for `duckdb` |
| `TrinoDatasourceConnection` | `connection` variant for `trino` |
| `SyncTriggerResponse` | `POST /datasources/{id}/sync` |
| `SyncJobStatusResponse` | `GET /datasources/{id}/sync/{job_id}` |
| `SyncSelectionResponse` | selections list and create |
| `BrowseSchemaItem` | schema browse list |
| `BrowseTableItem` | table browse list |
| `TablePreviewResponse` | catalog preview |
| `SourceObjectResponse` | synced object list and detail |

Retrieve a schema fragment: `GET /openapi/schemas/DatasourceResponse`
```

- [ ] Correct the endpoint table to use `/datasources` (not `/sources`) and `datasource_id` (not `source_id`) throughout — the router serves `/datasources/**`.

- [ ] Add an **Error semantics** section at the bottom matching the pattern in `semantic.md`:

```markdown
## Error semantics

- `400`: sync disabled, no selections configured, invalid query parameters
- `404`: datasource or object not found
- `409`: datasource has dependent mappings (delete conflict)
- `422`: request validation failed (malformed connection payload, unknown `datasource_type`)
```

- [ ] Commit:

```bash
git add docs/api/sources.md
git commit -m "docs: update sources.md for Wave 1 typed schema contracts"
```

---

## Wave 2 — Governance and Approvals

---

### Task 8: Extend quality gate — Wave 2 paths (RED)

**Files:**
- Modify: `tests/test_openapi_schema_quality.py:10`

- [ ] Extend `SCOPED_PATH_PREFIXES`:

```python
SCOPED_PATH_PREFIXES = (
    "/semantic-models",
    "/datasources",
    "/routing",
    "/policies",
    "/quality-rules",
    "/governance",
    "/approvals",
)
```

Also add the session-scoped approvals path to `SCOPED_SESSION_PATHS`:

```python
SCOPED_SESSION_PATHS = {
    # ... existing entries ...
    "/sessions/{session_id}/approvals/auto-flag",
}
```

- [ ] Run to confirm RED:

```bash
make test -- -k test_scoped_openapi_schemas_are_agent_friendly
```

- [ ] Commit the failing test:

```bash
git add tests/test_openapi_schema_quality.py
git commit -m "test: scope governance and approvals paths in quality gate (RED)"
```

---

### Task 9: Policy discriminated union models

**Files:**
- Modify: `app/api/models/_infrastructure.py`

- [ ] Add policy definition union and scope model. Insert in the `# === Sync / Policy / Quality / Governance / Job / Approval models ===` section, replacing `PolicyCreateRequest`, `PolicyUpdateRequest`:

```python
# --- Policy models ---

class AggregateOnlyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_type: Literal["aggregate_only"]
    min_group_size: int | None = None


class FieldMaskDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_type: Literal["field_mask"]
    columns: list[str]
    mask_value: str = "***"


class RowFilterDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_type: Literal["row_filter"]
    filter_expr: str
    reason: str = ""


class MaxRowsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_type: Literal["max_rows"]
    limit: int


PolicyDefinition = Annotated[
    AggregateOnlyDefinition | FieldMaskDefinition | RowFilterDefinition | MaxRowsDefinition,
    Field(discriminator="policy_type"),
]


class PolicyScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tables: list[str] | None = None
    sources: list[str] | None = None
    step_types: list[str] | None = None


class PolicyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    policy_type: Literal["aggregate_only", "field_mask", "row_filter", "max_rows"]
    definition: PolicyDefinition
    scope: PolicyScope = Field(default_factory=PolicyScope)

    @model_validator(mode="before")
    @classmethod
    def _inject_type_into_definition(cls, data: Any) -> Any:
        if isinstance(data, dict) and "policy_type" in data:
            defn = data.get("definition")
            if isinstance(defn, dict) and "policy_type" not in defn:
                data["definition"] = {**defn, "policy_type": data["policy_type"]}
        return data


class PolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    definition: PolicyDefinition | None = None

    @model_validator(mode="before")
    @classmethod
    def _inject_type_into_definition(cls, data: Any) -> Any:
        # When updating, callers may omit policy_type on definition if it's clear from context.
        # Accept dicts that already have policy_type; do not inject from unknown context here.
        return data


class PolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    name: str
    policy_type: Literal["aggregate_only", "field_mask", "row_filter", "max_rows"]
    definition: PolicyDefinition
    scope: PolicyScope
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    @model_validator(mode="before")
    @classmethod
    def _inject_type_into_definition(cls, data: Any) -> Any:
        if isinstance(data, dict) and "policy_type" in data:
            defn = data.get("definition")
            if isinstance(defn, dict) and "policy_type" not in defn:
                data["definition"] = {**defn, "policy_type": data["policy_type"]}
            sc = data.get("scope")
            if sc is None:
                data["scope"] = {}
        return data


class PolicyDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["deleted"]
    policy_id: str
```

---

### Task 10: Quality rule and governance check response models

**Files:**
- Modify: `app/api/models/_infrastructure.py`

- [ ] Replace `QualityRuleCreateRequest` and `GovernanceCheckRequest` with typed versions and add response models:

```python
# --- Quality rule models ---

class FreshnessThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_type: Literal["freshness"]
    max_age_hours: int


class NullRateThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_type: Literal["null_rate"]
    column: str
    max_null_rate: float


class RowCountMinThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_type: Literal["row_count_min"]
    min_rows: int


QualityRuleThreshold = Annotated[
    FreshnessThreshold | NullRateThreshold | RowCountMinThreshold,
    Field(discriminator="rule_type"),
]


class QualityRuleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    rule_type: Literal["freshness", "null_rate", "row_count_min"]
    table_name: str
    threshold: QualityRuleThreshold
    severity: Literal["warn", "error"] = "warn"

    @model_validator(mode="before")
    @classmethod
    def _inject_type_into_threshold(cls, data: Any) -> Any:
        if isinstance(data, dict) and "rule_type" in data:
            thresh = data.get("threshold")
            if isinstance(thresh, dict) and "rule_type" not in thresh:
                data["threshold"] = {**thresh, "rule_type": data["rule_type"]}
        return data


class QualityRuleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    name: str
    rule_type: Literal["freshness", "null_rate", "row_count_min"]
    table_name: str
    threshold: QualityRuleThreshold
    severity: Literal["warn", "error"] = "warn"
    enabled: bool = True
    created_at: str = ""

    @model_validator(mode="before")
    @classmethod
    def _inject_type_into_threshold(cls, data: Any) -> Any:
        if isinstance(data, dict) and "rule_type" in data:
            thresh = data.get("threshold")
            if isinstance(thresh, dict) and "rule_type" not in thresh:
                data["threshold"] = {**thresh, "rule_type": data["rule_type"]}
        return data


class QualityRuleDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["deleted"]
    rule_id: str


# --- Governance check models ---

class GovernanceCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    step_type: str
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class GovernanceViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_name: str
    policy_type: str
    message: str


class GovernanceWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_name: str
    message: str


class GovernanceCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    violations: list[GovernanceViolation] = Field(default_factory=list)
    warnings: list[GovernanceWarning] = Field(default_factory=list)
```

---

### Task 11: Approval response model

**Files:**
- Modify: `app/api/models/_infrastructure.py`

- [ ] Replace `ApprovalCreateRequest` / `ApprovalDecisionRequest` section with typed request + new response models:

```python
# --- Approval models ---

class ApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    rec_id: str


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str
    reason: str = ""


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    session_id: str
    rec_id: str
    status: str
    reviewer: str | None = None
    reason: str | None = None
    created_at: str = ""
    updated_at: str = ""
```

---

### Task 12: Wire governance/approval response models into route handlers

**Files:**
- Modify: `app/api/governance.py`
- Modify: `app/api/approvals.py`

- [ ] Update `governance.py` imports and all handler signatures:

```python
from app.api.models import (
    GovernanceCheckRequest,
    GovernanceCheckResponse,
    GovernanceViolation,
    GovernanceWarning,
    PolicyCreateRequest,
    PolicyDeleteResponse,
    PolicyResponse,
    PolicyUpdateRequest,
    QualityRuleCreateRequest,
    QualityRuleDeleteResponse,
    QualityRuleResponse,
)
```

Update handlers:

```python
@router.post("/policies", response_model=PolicyResponse)
def create_policy(payload: PolicyCreateRequest, request: Request) -> PolicyResponse:
    governance = require_governance(get_services(request))
    try:
        result = governance.create_policy(
            name=payload.name,
            policy_type=payload.policy_type,
            definition=payload.definition.model_dump(),
            scope=payload.scope.model_dump(),
        )
        return PolicyResponse.model_validate(result)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/policies", response_model=list[PolicyResponse])
def list_policies(request: Request) -> list[PolicyResponse]:
    services = get_services(request)
    if services.governance_service is None:
        return []
    rows = services.governance_service.list_policies(enabled_only=False)
    return [PolicyResponse.model_validate(r) for r in rows]


@router.get("/policies/{policy_id}", response_model=PolicyResponse)
def get_policy(policy_id: str, request: Request) -> PolicyResponse:
    governance = require_governance(get_services(request))
    try:
        return PolicyResponse.model_validate(governance.get_policy(policy_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/policies/{policy_id}", response_model=PolicyResponse)
def update_policy(
    policy_id: str, payload: PolicyUpdateRequest, request: Request
) -> PolicyResponse:
    governance = require_governance(get_services(request))
    defn = payload.definition.model_dump() if payload.definition is not None else None
    try:
        result = governance.update_policy(policy_id, enabled=payload.enabled, definition=defn)
        return PolicyResponse.model_validate(result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/policies/{policy_id}", response_model=PolicyDeleteResponse)
def delete_policy(policy_id: str, request: Request) -> PolicyDeleteResponse:
    governance = require_governance(get_services(request))
    try:
        governance.delete_policy(policy_id)
        return PolicyDeleteResponse(status="deleted", policy_id=policy_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/quality-rules", response_model=QualityRuleResponse)
def create_quality_rule(
    payload: QualityRuleCreateRequest, request: Request
) -> QualityRuleResponse:
    governance = require_governance(get_services(request))
    try:
        result = governance.create_quality_rule(
            name=payload.name,
            rule_type=payload.rule_type,
            table_name=payload.table_name,
            threshold=payload.threshold.model_dump(),
            severity=payload.severity,
        )
        return QualityRuleResponse.model_validate(result)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/quality-rules", response_model=list[QualityRuleResponse])
def list_quality_rules(
    request: Request, table: str | None = Query(default=None)
) -> list[QualityRuleResponse]:
    services = get_services(request)
    if services.governance_service is None:
        return []
    rows = services.governance_service.list_quality_rules(table_name=table)
    return [QualityRuleResponse.model_validate(r) for r in rows]


@router.delete("/quality-rules/{rule_id}", response_model=QualityRuleDeleteResponse)
def delete_quality_rule(rule_id: str, request: Request) -> QualityRuleDeleteResponse:
    governance = require_governance(get_services(request))
    try:
        governance.delete_quality_rule(rule_id)
        return QualityRuleDeleteResponse(status="deleted", rule_id=rule_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/governance/check", response_model=GovernanceCheckResponse)
def governance_check(
    payload: GovernanceCheckRequest, request: Request
) -> GovernanceCheckResponse:
    services = get_services(request)
    if services.governance_service is None:
        return GovernanceCheckResponse(passed=True)
    result = services.governance_service.check_step(
        session_id=payload.session_id,
        step_type=payload.step_type,
        params=payload.params,
    )
    return GovernanceCheckResponse.model_validate(result)
```

- [ ] Update `approvals.py` imports and all handler signatures:

```python
from app.api.models import (
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
    ApprovalResponse,
    AutoFlagRequest,
)
```

```python
@router.post("/approvals", response_model=ApprovalResponse)
def create_approval(
    payload: ApprovalCreateRequest, request: Request
) -> ApprovalResponse:
    try:
        result = get_services(request).approval_service.request_approval(
            session_id=payload.session_id,
            rec_id=payload.rec_id,
        )
        return ApprovalResponse.model_validate(result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/approvals", response_model=list[ApprovalResponse])
def list_approvals(
    request: Request,
    session_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[ApprovalResponse]:
    rows = get_services(request).approval_service.list_requests(
        session_id=session_id, status=status
    )
    return [ApprovalResponse.model_validate(r) for r in rows]


@router.get("/approvals/{request_id}", response_model=ApprovalResponse)
def get_approval(request_id: str, request: Request) -> ApprovalResponse:
    try:
        result = get_services(request).approval_service.get_request(request_id)
        return ApprovalResponse.model_validate(result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/approvals/{request_id}/approve", response_model=ApprovalResponse)
def approve_request(
    request_id: str, payload: ApprovalDecisionRequest, request: Request
) -> ApprovalResponse:
    try:
        result = get_services(request).approval_service.approve(
            request_id, reviewer=payload.reviewer, reason=payload.reason,
        )
        return ApprovalResponse.model_validate(result)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.post("/approvals/{request_id}/reject", response_model=ApprovalResponse)
def reject_request(
    request_id: str, payload: ApprovalDecisionRequest, request: Request
) -> ApprovalResponse:
    try:
        result = get_services(request).approval_service.reject(
            request_id, reviewer=payload.reviewer, reason=payload.reason,
        )
        return ApprovalResponse.model_validate(result)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.post(
    "/sessions/{session_id}/approvals/auto-flag",
    response_model=list[ApprovalResponse],
)
def auto_flag_approvals(
    session_id: str,
    request: Request,
    payload: AutoFlagRequest | None = None,
) -> list[ApprovalResponse]:
    services = get_services(request)
    try:
        services.service._assert_session_exists(session_id)
        threshold = payload.risk_threshold if payload else "P0"
        rows = services.approval_service.auto_flag_recommendations(
            session_id, risk_threshold=threshold
        )
        return [ApprovalResponse.model_validate(r) for r in rows]
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
```

---

### Task 13: Export Wave 2 models and verify (GREEN)

**Files:**
- Modify: `app/api/models/__init__.py`

- [ ] Add all Wave 2 models to the import block:

```python
from ._infrastructure import (
    # ... existing imports ...
    # Wave 2 governance models
    AggregateOnlyDefinition,
    ApprovalResponse,
    FieldMaskDefinition,
    FreshnessThreshold,
    GovernanceCheckResponse,
    GovernanceViolation,
    GovernanceWarning,
    MaxRowsDefinition,
    NullRateThreshold,
    PolicyDeleteResponse,
    PolicyResponse,
    PolicyScope,
    QualityRuleDeleteResponse,
    QualityRuleResponse,
    RowCountMinThreshold,
    RowFilterDefinition,
)
```

- [ ] Run tests and typecheck:

```bash
make test
make typecheck
```

Expected: `test_scoped_openapi_schemas_are_agent_friendly` now passes for all Wave 2 paths.

- [ ] Fix any failures, then commit:

```bash
git add app/api/models/_infrastructure.py \
        app/api/models/__init__.py \
        app/api/governance.py \
        app/api/approvals.py \
        tests/test_openapi_schema_quality.py
git commit -m "feat: Wave 2 — typed schemas for governance and approvals"
```

---

### Task 14: Update governance.md doc

**Files:**
- Modify: `docs/api/governance.md`

- [ ] Add a **Component Schemas** section at the top:

```markdown
## Component Schemas

| Schema name | Used by |
|-------------|---------|
| `PolicyCreateRequest` | `POST /policies` request |
| `PolicyUpdateRequest` | `PUT /policies/{id}` request |
| `PolicyResponse` | all policy CRUD responses |
| `AggregateOnlyDefinition` | `definition` variant for `aggregate_only` policies |
| `FieldMaskDefinition` | `definition` variant for `field_mask` policies |
| `RowFilterDefinition` | `definition` variant for `row_filter` policies |
| `MaxRowsDefinition` | `definition` variant for `max_rows` policies |
| `PolicyScope` | `scope` sub-object on policy requests/responses |
| `QualityRuleCreateRequest` | `POST /quality-rules` request |
| `QualityRuleResponse` | quality rule responses |
| `FreshnessThreshold` | `threshold` variant for `freshness` rules |
| `NullRateThreshold` | `threshold` variant for `null_rate` rules |
| `RowCountMinThreshold` | `threshold` variant for `row_count_min` rules |
| `GovernanceCheckRequest` | `POST /governance/check` request |
| `GovernanceCheckResponse` | governance check response |

Retrieve a schema fragment: `GET /openapi/schemas/PolicyResponse`
```

- [ ] Update the `POST /policies` request example to show the discriminated `definition` field explicitly:

```json
{
  "name": "no_raw_pii",
  "policy_type": "aggregate_only",
  "definition": {
    "policy_type": "aggregate_only",
    "min_group_size": 100
  },
  "scope": {
    "tables": ["events.user_video_watch"]
  }
}
```

- [ ] Add an **Error semantics** section:

```markdown
## Error semantics

- `400`: unknown policy type, invalid definition shape, missing required threshold fields
- `404`: policy or quality rule not found
- `422`: request validation failed
- `503`: governance service not configured (policies/rules return empty lists; check returns `passed: true`)
```

- [ ] Commit:

```bash
git add docs/api/governance.md
git commit -m "docs: update governance.md for Wave 2 typed schema contracts"
```

---

## Wave 3 — Jobs

---

### Task 15: Extend quality gate — jobs paths (RED)

**Files:**
- Modify: `tests/test_openapi_schema_quality.py:10`

- [ ] Extend `SCOPED_PATH_PREFIXES`:

```python
SCOPED_PATH_PREFIXES = (
    "/semantic-models",
    "/datasources",
    "/routing",
    "/policies",
    "/quality-rules",
    "/governance",
    "/approvals",
    "/jobs",
)
```

- [ ] Run to confirm RED:

```bash
make test -- -k test_scoped_openapi_schemas_are_agent_friendly
```

- [ ] Commit:

```bash
git add tests/test_openapi_schema_quality.py
git commit -m "test: scope jobs paths in quality gate (RED)"
```

---

### Task 16: Job response models

**Files:**
- Modify: `app/api/models/_infrastructure.py`

- [ ] Replace `JobSubmitRequest` with typed models, add `JobPayload` and `JobResponse`:

```python
# --- Job models ---

class JobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_type: str
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class JobSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    job_type: str
    payload: JobPayload = Field(default_factory=JobPayload)


class JobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    session_id: str
    job_type: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    payload: JobPayload
    error_message: str | None = None
    created_at: str = ""
    updated_at: str = ""
    submitted_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
```

---

### Task 17: Wire job response models and verify (GREEN)

**Files:**
- Modify: `app/api/jobs.py`
- Modify: `app/api/models/__init__.py`

- [ ] Update `jobs.py` imports and handler signatures:

```python
from app.api.models import JobPayload, JobResponse, JobSubmitRequest
```

```python
@router.post("/jobs", response_model=JobResponse)
def submit_job(payload: JobSubmitRequest, request: Request) -> JobResponse:
    try:
        result = get_services(request).job_service.submit_job(
            session_id=payload.session_id,
            job_type=payload.job_type,
            payload=payload.payload.model_dump(),
        )
        return JobResponse.model_validate(result)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error


@router.get("/jobs", response_model=list[JobResponse])
def list_jobs(
    request: Request,
    session_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[JobResponse]:
    rows = get_services(request).job_service.list_jobs(
        session_id=session_id, status=status
    )
    return [JobResponse.model_validate(r) for r in rows]


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, request: Request) -> JobResponse:
    try:
        result = get_services(request).job_service.get_job(job_id)
        return JobResponse.model_validate(result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, request: Request) -> JobResponse:
    try:
        result = get_services(request).job_service.cancel_job(job_id)
        return JobResponse.model_validate(result)
    except (KeyError, ValueError) as error:
        raise http_error(error) from error
```

- [ ] Add `JobPayload` and `JobResponse` to `app/api/models/__init__.py` exports.

- [ ] Run tests and typecheck:

```bash
make test
make typecheck
```

- [ ] Fix any failures, then commit:

```bash
git add app/api/models/_infrastructure.py \
        app/api/models/__init__.py \
        app/api/jobs.py \
        tests/test_openapi_schema_quality.py
git commit -m "feat: Wave 3 — typed schemas for jobs"
```

---

### Task 18: Update jobs.md doc

**Files:**
- Modify: `docs/api/jobs.md`

- [ ] Add a **Component Schemas** section:

```markdown
## Component Schemas

| Schema name | Used by |
|-------------|---------|
| `JobResponse` | all job read responses |
| `JobPayload` | `payload` sub-object on job responses |

Retrieve a schema fragment: `GET /openapi/schemas/JobResponse`
```

- [ ] Update the list endpoint description to reflect the typed shape and note `POST /jobs` and `POST /jobs/{job_id}/cancel` exist for internal use but should not be used by agents directly.

- [ ] Add **Error semantics**:

```markdown
## Error semantics

- `404`: job not found
- `422`: request validation failed (invalid session_id or payload shape)
```

- [ ] Commit:

```bash
git add docs/api/jobs.md
git commit -m "docs: update jobs.md for Wave 3 typed schema contracts"
```

---

## Wave 4 — Calendar

> Calendar routes already have `response_model=` and named models. This wave adds `extra="forbid"` to the models and confirms the quality gate passes.

---

### Task 19: Extend quality gate — calendar (may already be GREEN)

**Files:**
- Modify: `tests/test_openapi_schema_quality.py:10`

- [ ] Add `/calendar` to `SCOPED_PATH_PREFIXES`:

```python
SCOPED_PATH_PREFIXES = (
    "/semantic-models",
    "/datasources",
    "/routing",
    "/policies",
    "/quality-rules",
    "/governance",
    "/approvals",
    "/jobs",
    "/calendar",
)
```

- [ ] Run the test:

```bash
make test -- -k test_scoped_openapi_schemas_are_agent_friendly
```

If it passes immediately, skip Task 20 and go straight to the commit. If it fails, proceed to Task 20.

---

### Task 20: Harden calendar models (only if Task 19 failed)

**Files:**
- Modify: `app/api/models/calendar.py`

- [ ] Add `model_config = ConfigDict(extra="forbid")` and tighten the `status` literal on each model:

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CalendarDataRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar_date: str = Field(..., description="Date in YYYY-MM-DD format")
    region_code: str = Field(default="CN", description="Region code (e.g. CN)")
    weekday: int = Field(..., ge=1, le=7, description="Day of week, 1=Monday .. 7=Sunday")
    is_weekend: int = Field(..., ge=0, le=1, description="1 if weekend, 0 otherwise")
    is_workday: int = Field(..., ge=0, le=1, description="1 if workday, 0 otherwise")
    holiday_name: str | None = Field(default=None)
    holiday_group_id: str | None = Field(default=None)
    year_relative_holiday_key: str | None = Field(default=None)
    event_group_id: str | None = Field(default=None)
    year_relative_event_key: str | None = Field(default=None)


class CalendarDataLoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar_version: str = Field(..., description="Version identifier for this calendar dataset")
    rows: list[CalendarDataRow] = Field(..., min_length=1, description="Calendar data rows")


class CalendarDataLoadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["loaded"]
    calendar_version: str
    row_count: int


class CalendarVersionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar_version: str
    region_code: str
```

Add `from typing import Literal` at the top.

- [ ] Run tests and typecheck:

```bash
make test
make typecheck
```

---

### Task 21: Commit Wave 4 and update hardening follow-up doc

- [ ] Commit the calendar wave:

```bash
git add app/api/models/calendar.py \
        tests/test_openapi_schema_quality.py
git commit -m "feat: Wave 4 — typed schemas for calendar, extend quality gate globally"
```

- [ ] Update `docs/api/openapi-schema-hardening-followups.md` to reflect the new state. Replace the **Current Phase Exclusions** section contents with:

```markdown
## Current Phase Exclusions

The following API groups have been closed in Waves 1–4 and are now fully
covered by the scoped quality gate:

- datasources (`/datasources/**`)
- routing (`/routing/**`)
- governance and approvals (`/policies/**`, `/quality-rules/**`, `/governance/**`, `/approvals/**`)
- jobs (`/jobs/**`)
- calendar (`/calendar/**`)

The following remain outside the quality gate and are deferred to Wave 5:

- `/engines/**` — engine inventory API (router not yet implemented)
- `/mappings/**` — mapping API (router not yet implemented)
- `/catalog` legacy and stub routes
- `/openapi/*` meta routes
- `/sessions/{session_id}/...` routes not listed in the scoped contract,
  such as planner-context surfaces
```

- [ ] Commit the doc update:

```bash
git add docs/api/openapi-schema-hardening-followups.md
git commit -m "docs: mark Waves 1–4 closed in schema hardening follow-up doc"
```

---

## Self-Review

**Spec coverage:**
- Wave 1 datasources/routing ✓
- Wave 2 governance/approvals ✓
- Wave 3 jobs ✓
- Wave 4 calendar ✓
- Quality gate extension after each wave ✓
- Docs updated per wave ✓
- Discriminated unions for connection, definition, threshold ✓
- `GovernanceCheckRequest.params` → typed scalar map ✓
- `RouteCapabilityProfileResponse.metadata` → `dict[str, str]` ✓
- `RoutingDetail` structured model ✓
- `/engines` and `/mappings` excluded (routers not implemented) ✓
- `/metrics` excluded (telemetry endpoint, not agent contract) ✓

**Type consistency check:**
- `DatasourceConnection` union defined in Task 2, used in Task 2 models ✓
- `RoutingDetail` defined in Task 4, imported in `routing.py` Task 4 ✓
- `PolicyDefinition` union defined in Task 9, used in `PolicyCreateRequest` + `PolicyResponse` Task 9 ✓
- `QualityRuleThreshold` union defined in Task 10, used in `QualityRuleCreateRequest` + `QualityRuleResponse` Task 10 ✓
- `JobPayload` defined in Task 16, used in `JobResponse` Task 16 and `jobs.py` Task 17 ✓
- All `model_validate(row)` calls assume the service layer returns dicts matching the model shape — if validation fails at runtime, check whether the service returns the field names used in the models (e.g., `schema_name` vs `schema`, `datasource_id` vs `source_id`).
