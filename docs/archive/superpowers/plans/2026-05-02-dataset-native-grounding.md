---
status: archived
created: 2026-05-02
---

# Dataset-Native Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace sync/cache/binding physical grounding with v2 dataset-native grounding based on `dataset.custom_extensions[].data.datasource_id`, `dataset.source`, and `field.expression`.

**Architecture:** Datasources keep registration, live browse, preview, and execution only. The semantic model is the only persisted physical grounding source, and runtime/readiness derives execution context from v2 datasets and fields. This is a breaking cleanup: removed routes, tables, MCP surfaces, and frontend entries are deleted rather than deprecated.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite/MySQL metadata DDL, DuckDB/Trino catalog adapters, React/TypeScript frontend, Marivo MCP package, repository entrypoints (`make test`, `make typecheck`, `make lint`, frontend npm scripts).

---

## Current Worktree Warning

Before executing this plan, protect existing worktree changes. At plan creation time these files had uncommitted or untracked changes:

- `app/api/models/_infrastructure.py`
- `app/api/routing.py`
- `package.json`
- `package-lock.json`

Run:

```bash
git status --short --untracked-files=all
```

Expected before starting a task: either a clean worktree, or only changes intentionally owned by the current task. Do not revert unrelated user changes.

## File Structure

Backend datasource contract:

- `app/api/models/_infrastructure.py` owns datasource request/response models. Remove sync/object models and add live column browse models here.
- `app/api/models/__init__.py` exports only surviving datasource models.
- `app/api/datasources.py` owns `/datasources` routes. Remove sync/object/property routes; add `/browse/columns`; use typed response models.
- `app/registry/datasource_registry.py` owns datasource persistence, live catalog browse, preview, and execution engine creation. Remove sync selection/object persistence methods and datasource delete binding checks.
- `app/registry/sync_runtime.py` and `app/sync.py` are removed when no callers remain.
- `app/storage/schema.py` owns metadata DDL. Remove cache/binding tables and datasource sync columns.
- `app/storage/sqlite_metadata.py` owns destructive SQLite bootstrap cleanup. Remove legacy cache/sync cleanup paths that mention removed tables.

Semantic v2 grounding and readiness:

- `app/api/models/marivo_extensions.py` keeps `MarivoDatasetExtension.datasource_id`; no new binding-shaped extension fields.
- `app/semantic_service_v2/validation.py` validates datasource-native dataset grounding structurally.
- `app/semantic_service_v2/service.py` performs datasource existence checks and readiness live checks.
- `app/semantic_service_v2/storage.py` remains the OSI/storage mapper for dataset datasource IDs and field expressions.
- `app/routing.py`, `app/api/routing.py`, and `app/execution/routing_runtime.py` stop resolving tables through `source_objects`; route from explicit datasource/dataset context only.

Runtime cleanup:

- `app/semantic_runtime/repository.py`, `app/time_axis_metadata.py`, `app/service.py`, and `app/analysis_core/*` remove public binding dependencies or isolate any temporary dataset-derived execution context behind private helpers.
- No task may add storage or API fields named `binding`, `carrier_binding`, `field_binding`, or `time_binding` as the replacement grounding contract.

MCP and frontend:

- `marivo-mcp/src/marivo_mcp/inventory.py`, `marivo-mcp/src/marivo_mcp/tools/__init__.py`, `marivo-mcp/src/marivo_mcp/resources/__init__.py`, and `marivo-mcp/README.md` remove binding, datasource objects, and sync tools/resources.
- `frontend/src/api/hooks.ts`, `frontend/src/pages/OperationsPage.tsx`, `frontend/src/pages/OperationsPage.test.tsx`, `frontend/src/pages/SemanticLayerPage.tsx`, `frontend/src/fixtures/mockApi.ts`, and `frontend/src/fixtures/mockData.ts` remove sync/object/binding UI and mocks.

Docs and tests:

- `tests/test_datasources.py`, `tests/test_openapi_schema_quality.py`, `tests/test_metadata_schema_bootstrap.py`, `tests/test_semantic_v2_api.py`, `tests/test_semantic_v2_service.py`, `tests/shared_fixtures.py`, MCP tests, frontend tests, and legacy intent/binding tests are updated or removed.
- `docs/api/semantic.md`, `docs/api/sources.md`, `docs/api/quickstart.md`, `docs/api/errors.md`, `marivo-skill/marivo/references/semantic-layer.md`, and `marivo-skill/marivo/references/http-contracts.md` document dataset-native grounding.

## Task 1: Add RED Contract Tests For Removed Surfaces

**Files:**
- Modify: `tests/test_openapi_schema_quality.py`
- Modify: `tests/test_metadata_schema_bootstrap.py`
- Test: `tests/test_openapi_schema_quality.py`
- Test: `tests/test_metadata_schema_bootstrap.py`

- [ ] **Step 1: Add OpenAPI absence test**

Append this test to `tests/test_openapi_schema_quality.py` after `test_legacy_catalog_routes_are_not_registered`:

```python
def test_dataset_native_grounding_removed_routes_are_not_registered() -> None:
    openapi = _router_only_openapi()
    paths = set(openapi["paths"])

    removed_paths = {
        "/datasources/{datasource_id}/sync",
        "/datasources/{datasource_id}/sync/{job_id}",
        "/datasources/{datasource_id}/sync/selections",
        "/datasources/{datasource_id}/sync/selections/{selection_id}",
        "/datasources/{datasource_id}/objects",
        "/datasources/{datasource_id}/objects/{object_id}",
        "/datasources/{datasource_id}/objects/{object_id}/properties",
        "/semantic/bindings",
        "/semantic/bindings/{binding_id}",
        "/semantic/bindings/{binding_id}/validate",
        "/semantic/bindings/{binding_id}/activate",
        "/semantic/bindings/{binding_id}/deprecate",
        "/semantic/bindings/{binding_id}/publish",
    }

    assert paths.isdisjoint(removed_paths)
    assert "/datasources/{datasource_id}/browse/columns" in paths
```

- [ ] **Step 2: Add metadata table absence test**

Append this test to `tests/test_metadata_schema_bootstrap.py` inside `MetadataSchemaBootstrapTests`:

```python
    def test_dataset_native_grounding_removed_tables_are_not_expected(self) -> None:
        removed_tables = {
            "source_objects",
            "sync_jobs",
            "sync_selections",
            "typed_bindings",
            "binding_imports",
            "carrier_bindings",
            "carrier_field_surfaces",
            "carrier_time_surfaces",
            "field_bindings",
            "time_bindings",
            "join_relations",
            "consumption_policies",
        }

        expected_tables = expected_metadata_tables("sqlite")

        self.assertTrue(removed_tables.isdisjoint(expected_tables))
        self.assertIn("datasources", expected_tables)
```

- [ ] **Step 3: Run the focused tests and verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_openapi_schema_quality.py::test_dataset_native_grounding_removed_routes_are_not_registered tests/test_metadata_schema_bootstrap.py::MetadataSchemaBootstrapTests::test_dataset_native_grounding_removed_tables_are_not_expected -q
```

Expected: both tests fail. The OpenAPI test reports sync/object routes or missing `/browse/columns`; the metadata test reports removed tables still present.

- [ ] **Step 4: Commit the RED tests**

Run:

```bash
git add tests/test_openapi_schema_quality.py tests/test_metadata_schema_bootstrap.py
git commit -m "test: capture dataset-native grounding cleanup contract" -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 2: Remove Sync And Binding Storage Schema

**Files:**
- Modify: `app/storage/schema.py`
- Modify: `app/storage/sqlite_metadata.py`
- Modify: `tests/shared_fixtures.py`
- Test: `tests/test_metadata_schema_bootstrap.py`

- [ ] **Step 1: Remove datasource sync state from DDL**

In `app/storage/schema.py`, replace the `datasources` DDL block with:

```python
    """
    CREATE TABLE IF NOT EXISTS datasources (
        datasource_id   TEXT PRIMARY KEY,
        datasource_type TEXT NOT NULL,
        display_name    TEXT NOT NULL,
        connection_json TEXT NOT NULL DEFAULT '{}',
        policy_json     TEXT NOT NULL DEFAULT '{}',
        status          TEXT NOT NULL DEFAULT 'active',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
```

- [ ] **Step 2: Delete removed table DDL blocks**

In `app/storage/schema.py`, delete the full DDL strings and index strings for these table families:

```text
source_objects
sync_jobs
sync_selections
typed_bindings
binding_imports
carrier_bindings
carrier_field_surfaces
carrier_time_surfaces
field_bindings
time_bindings
join_relations
consumption_policies
idx_source_objects_*
idx_typed_bindings_*
idx_binding_imports_*
idx_carrier_bindings_*
idx_carrier_field_surfaces_*
idx_carrier_time_surfaces_*
idx_field_bindings_*
idx_time_bindings_*
idx_join_relations_*
idx_consumption_policies_*
```

- [ ] **Step 3: Remove SQLite legacy cleanup for removed tables**

In `app/storage/sqlite_metadata.py`, delete cleanup entries and comments that mention:

```text
source_objects__legacy_fk
sync_jobs__legacy_fk
sync_selections__legacy_fk
source_objects
sync_jobs
sync_selections
```

If the file has a list of tables dropped for old source/datasource FK shape, leave only tables that still exist in the target schema.

- [ ] **Step 4: Update fixture schema assertions**

In `tests/shared_fixtures.py`, remove checks that inspect `typed_bindings`, `carrier_bindings`, `field_bindings`, `source_objects`, `sync_jobs`, or `sync_selections`. Keep checks for current semantic v2 tables such as `semantic_models`, `semantic_datasets`, `semantic_fields`, `semantic_relationships`, and `semantic_metrics`.

- [ ] **Step 5: Run schema tests**

Run:

```bash
.venv/bin/pytest tests/test_metadata_schema_bootstrap.py -q
```

Expected: all tests pass, including `test_dataset_native_grounding_removed_tables_are_not_expected`.

- [ ] **Step 6: Commit schema cleanup**

Run:

```bash
git add app/storage/schema.py app/storage/sqlite_metadata.py tests/shared_fixtures.py tests/test_metadata_schema_bootstrap.py
git commit -m "refactor: remove sync and binding metadata tables" -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 3: Simplify Datasource Models And Registry

**Files:**
- Modify: `app/api/models/_infrastructure.py`
- Modify: `app/api/models/__init__.py`
- Modify: `app/registry/datasource_registry.py`
- Test: `tests/test_datasources.py`
- Test: `tests/test_openapi_schema_quality.py`

- [ ] **Step 1: Replace datasource policy and response models**

In `app/api/models/_infrastructure.py`, remove `allow_sync`, `sync_mode`, and all sync/object models. The datasource section should expose these models:

```python
class DatasourcePolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_live_browse: bool = True
    allow_identity_reuse: bool = False


class DatasourceRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasource_type: Literal["duckdb", "trino"]
    display_name: str
    connection: DatasourceConnection
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
    connection: DatasourceConnection | None = Field(
        default=None,
        description="Full connection object including datasource_type; required when provided.",
    )
    policy: DatasourcePolicyPayload | None = None


class DatasourcePolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_live_browse: bool = True
    allow_identity_reuse: bool = False


class DatasourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasource_id: str
    datasource_type: Literal["duckdb", "trino"]
    display_name: str
    connection: DatasourceConnection
    policy: DatasourcePolicyResponse
    status: Literal["active", "inactive", "deprecated"] = "active"
    readiness_status: Literal["not_ready", "ready"] = "not_ready"
    failure_code: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @model_validator(mode="before")
    @classmethod
    def _inject_type_into_connection(cls, data: Any) -> Any:
        if isinstance(data, dict) and "datasource_type" in data:
            conn = data.get("connection")
            if isinstance(conn, dict) and "datasource_type" not in conn:
                data["connection"] = {**conn, "datasource_type": data["datasource_type"]}
        return data
```

Delete these classes from `_infrastructure.py`:

```text
SyncTriggerResponse
SyncJobStatusResponse
SyncSelectionResponse
SyncClearedResponse
SyncDeletedResponse
SyncSelectionPayload
SyncSelectionRequest
SourceObjectAuthorityLocator
SourceObjectResponse
ColumnPropertiesUpdateRequest
```

- [ ] **Step 2: Update model exports**

In `app/api/models/__init__.py`, remove imports and `__all__` entries for the deleted sync/object classes. Keep `DatasourcePolicyPayload`, `DatasourcePolicyResponse`, `DatasourceRegisterRequest`, `DatasourceResponse`, `DatasourceUpdateRequest`, and `DatasourceDeleteResponse`.

- [ ] **Step 3: Simplify policy normalization**

In `app/registry/datasource_registry.py`, replace `_normalize_policy` with:

```python
def _normalize_policy(datasource_type: str, policy: dict[str, Any] | None) -> dict[str, Any]:
    normalized = {
        "allow_live_browse": True,
        "allow_identity_reuse": False,
    }
    if policy:
        normalized.update(policy)
    if datasource_type == "duckdb":
        normalized.pop("allow_identity_reuse", None)
    return normalized
```

Delete `_normalize_sync`.

- [ ] **Step 4: Remove sync parameters from registry CRUD**

Update signatures:

```python
def register_datasource(
    self,
    datasource_type: str,
    display_name: str,
    connection: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

```python
def ensure_datasource(
    self,
    datasource_type: str,
    display_name: str,
    connection: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

```python
def update_datasource(
    self,
    datasource_id: str,
    display_name: str | None = None,
    connection: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

Remove SQL writes to `sync_mode`. In `_row_to_datasource`, remove:

```python
"sync_mode": str(row["sync_mode"]),
```

- [ ] **Step 5: Remove sync/cache registry methods**

Delete these methods from `DatasourceRegistry`:

```text
get_sync_mode
list_objects
get_object
patch_object_properties
add_sync_selection
remove_sync_selection
list_sync_selections
clear_sync_selections
```

In `delete_datasource`, remove typed binding dependency queries and deletes from removed tables. The method should end with:

```python
self.metadata.execute("DELETE FROM datasources WHERE datasource_id = ?", [datasource_id])
```

- [ ] **Step 6: Update datasource tests for removed fields**

In `tests/test_datasources.py`, remove assertions for `sync_mode` and `policy["allow_sync"]`. Replace register payload helpers so they no longer send `sync_mode` or `allow_sync`.

Example payload helper target:

```python
def _build_duckdb_datasource_payload(db_path: str, name: str) -> dict[str, object]:
    return {
        "datasource_type": "duckdb",
        "display_name": name,
        "connection": {"path": db_path},
        "policy": {"allow_live_browse": True},
    }
```

- [ ] **Step 7: Run datasource model tests**

Run:

```bash
.venv/bin/pytest tests/test_datasources.py tests/test_openapi_schema_quality.py::test_dataset_native_grounding_removed_routes_are_not_registered -q
```

Expected: failures remain only for routes not removed yet or columns browse not added. No failures should mention missing `sync_mode` columns in datasource CRUD.

- [ ] **Step 8: Commit datasource model and registry cleanup**

Run:

```bash
git add app/api/models/_infrastructure.py app/api/models/__init__.py app/registry/datasource_registry.py tests/test_datasources.py
git commit -m "refactor: remove datasource sync state" -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 4: Remove Sync/Object Routes And Add Live Columns Browse

**Files:**
- Modify: `app/api/datasources.py`
- Modify: `app/api/models/_infrastructure.py`
- Modify: `app/api/models/__init__.py`
- Modify: `app/registry/datasource_registry.py`
- Modify: `app/api/router.py`
- Delete: `app/registry/sync_runtime.py`
- Delete: `app/sync.py`
- Test: `tests/test_datasources.py`
- Test: `tests/test_openapi_schema_quality.py`

- [ ] **Step 1: Add live column browse response model**

In `app/api/models/_infrastructure.py`, add:

```python
class DatasourceColumnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    schema_name: str
    table_name: str
    data_type: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
```

Export `DatasourceColumnResponse` from `app/api/models/__init__.py`.

- [ ] **Step 2: Add live browse method**

In `app/registry/datasource_registry.py`, add this method next to `browse_catalog_tables`:

```python
def browse_catalog_columns(
    self, datasource_id: str, schema_name: str, table_name: str
) -> list[dict[str, Any]]:
    adapter = self.get_adapter(datasource_id)
    columns = adapter.list_columns(schema_name, table_name)
    return [
        {
            "name": column.native_name,
            "schema_name": schema_name,
            "table_name": table_name,
            "data_type": column.properties.get("data_type")
            or column.properties.get("type")
            or column.properties.get("native_type"),
            "properties": column.properties,
        }
        for column in columns
    ]
```

- [ ] **Step 3: Remove sync/object routes**

In `app/api/datasources.py`, delete route functions for:

```text
trigger_sync
list_sync_selections
add_sync_selections
clear_sync_selections
remove_sync_selection
get_sync_status
patch_column_properties
get_datasource_object
list_datasource_objects
```

Remove imports for deleted models and `DependencyError` if no longer used.

- [ ] **Step 4: Add columns browse route**

In `app/api/datasources.py`, add:

```python
@router.get("/datasources/{datasource_id}/browse/columns", response_model=list[DatasourceColumnResponse])
def browse_catalog_columns(
    datasource_id: str,
    request: Request,
    schema_name: str | None = Query(None),
    table_name: str | None = Query(None),
) -> list[DatasourceColumnResponse]:
    try:
        if schema_name is None:
            raise ValueError("schema_name query parameter is required")
        if table_name is None:
            raise ValueError("table_name query parameter is required")
        return [
            DatasourceColumnResponse.model_validate(item)
            for item in get_services(request).datasource_service.browse_catalog_columns(
                datasource_id, schema_name=schema_name, table_name=table_name
            )
        ]
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
```

Add `DatasourceColumnResponse` to the imports from `app.api.models`.

- [ ] **Step 5: Remove sync engine construction**

Search:

```bash
rg -n "sync_engine|RegistrySyncEngine|app\\.sync|registry\\.sync_runtime" app tests
```

Remove service construction and imports. Delete `app/registry/sync_runtime.py` and `app/sync.py` after there are no imports.

- [ ] **Step 6: Add live columns API test**

In `tests/test_datasources.py`, add an API test that creates a DuckDB datasource and calls live columns without sync:

```python
def test_browse_columns_live_without_sync(self) -> None:
    ds = self._create_datasource("Browse Columns DS")
    datasource_id = ds["datasource_id"]

    resp = self.client.get(
        f"/datasources/{datasource_id}/browse/columns",
        params={"schema_name": "analytics", "table_name": "orders"},
    )

    self.assertEqual(resp.status_code, 200)
    names = {item["name"] for item in resp.json()}
    self.assertIn("order_id", names)
```

This test assumes the existing datasource fixture creates `analytics.orders`. If that fixture no longer creates `analytics.orders`, first update the shared datasource fixture so `analytics.orders` exists for browse, preview, and columns tests.

- [ ] **Step 7: Run focused datasource route tests**

Run:

```bash
.venv/bin/pytest tests/test_datasources.py::DatasourceSyncModeTests tests/test_openapi_schema_quality.py::test_dataset_native_grounding_removed_routes_are_not_registered -q
```

Expected: sync mode tests fail because they still exist and must be removed in the next step.

- [ ] **Step 8: Delete obsolete datasource sync/object tests**

In `tests/test_datasources.py`, delete test classes and methods whose purpose is sync selections, sync execution, source object listing, object detail, object property patching, or delete blocking by typed binding. Keep browse schemas, browse tables, preview, CRUD, readiness, and live columns tests.

- [ ] **Step 9: Run datasource tests**

Run:

```bash
.venv/bin/pytest tests/test_datasources.py tests/test_openapi_schema_quality.py::test_dataset_native_grounding_removed_routes_are_not_registered -q
```

Expected: pass.

- [ ] **Step 10: Commit datasource route cleanup**

Run:

```bash
git add app/api/datasources.py app/api/models/_infrastructure.py app/api/models/__init__.py app/registry/datasource_registry.py app/api/router.py tests/test_datasources.py tests/test_openapi_schema_quality.py
git rm app/registry/sync_runtime.py app/sync.py
git commit -m "refactor: remove datasource sync and object routes" -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 5: Enforce Dataset-Native Semantic Validation And Readiness

**Files:**
- Modify: `app/semantic_service_v2/validation.py`
- Modify: `app/semantic_service_v2/service.py`
- Modify: `app/api/app_factory.py`
- Test: `tests/test_semantic_v2_api.py`
- Test: `tests/test_semantic_v2_service.py`

- [ ] **Step 1: Add validation tests for datasource-native grounding**

In `tests/test_semantic_v2_api.py`, add tests under `TestCreateSemanticModelAPI`:

```python
    def test_create_dataset_requires_marivo_datasource_id(self) -> None:
        client = _make_app()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["datasets"][0]["custom_extensions"] = []

        resp = client.post("/semantic-models", json=model_data)

        self.assertEqual(resp.status_code, 422)
        self.assertIn("datasource_id", resp.json()["detail"])

    def test_create_dataset_requires_non_empty_source_fqn(self) -> None:
        client = _make_app()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["datasets"][0]["source"] = ""

        resp = client.post("/semantic-models", json=model_data)

        self.assertEqual(resp.status_code, 422)
        self.assertIn("source", resp.json()["detail"])
```

- [ ] **Step 2: Add datasource existence wiring to test app**

In `tests/test_semantic_v2_api.py`, update `_make_app` so `app.state.datasource_service` exists:

```python
from app.datasources import DatasourceService
```

Inside `_make_app()` after `service = SemanticModelV2Service(store)`:

```python
datasource_service = DatasourceService(store)
```

Before returning the client:

```python
app.state.datasource_service = datasource_service
```

In `_make_model_dict`, keep `datasource_id: "ds_001"` for structural validation tests. Do not require the datasource row for the two tests in Step 1.

- [ ] **Step 3: Add structural validation**

In `app/semantic_service_v2/validation.py`, update dataset validation so every dataset must have:

```python
source = str(dataset.get("source") or "").strip()
if not source:
    raise SemanticValidationError("Dataset source is required and must be a non-empty relation FQN")

datasource_id = dataset.get("datasource_id")
if not isinstance(datasource_id, str) or not datasource_id.strip():
    raise SemanticValidationError("Dataset MARIVO extension datasource_id is required")
```

Place this inside the existing dataset loop after MARIVO extension enrichment has populated top-level `datasource_id`.

- [ ] **Step 4: Add readiness live validation response shape**

In `app/semantic_service_v2/service.py`, update `get_readiness` to return blockers for dataset live validation. The return structure should remain compatible with `SemanticModelReadinessResponse`:

```python
{
    "status": "ready" if not blockers else "not_ready",
    "semantic_version_id": None,
    "evaluated_semantic_version_id": None,
    "blockers": blockers,
}
```

Each blocker is a dict shaped like:

```python
{
    "code": "relation_not_found",
    "message": "Dataset orders source analytics.orders was not found in datasource ds_001",
    "dataset": "orders",
    "datasource_id": "ds_001",
    "source": "analytics.orders",
}
```

- [ ] **Step 5: Implement datasource live relation check**

In `app/semantic_service_v2/service.py`, add a private helper:

```python
def _check_dataset_live_readiness(self, dataset: dict[str, Any]) -> list[dict[str, Any]]:
    datasource_id = str(dataset.get("datasource_id") or "").strip()
    source = str(dataset.get("source") or "").strip()
    dataset_name = str(dataset.get("name") or "")
    if not datasource_id or not source:
        return []
    datasource_service = getattr(self, "datasource_service", None)
    if datasource_service is None:
        return []
    try:
        datasource_service.get_datasource(datasource_id)
    except KeyError:
        return [
            {
                "code": "datasource_not_found",
                "message": f"Dataset {dataset_name} references missing datasource {datasource_id}",
                "dataset": dataset_name,
                "datasource_id": datasource_id,
                "source": source,
            }
        ]
    parts = source.split(".")
    if len(parts) == 2:
        schema_name, table_name = parts
    elif len(parts) == 3:
        _, schema_name, table_name = parts
    else:
        return [
            {
                "code": "relation_not_found",
                "message": f"Dataset {dataset_name} source {source} is not a schema.table or catalog.schema.table FQN",
                "dataset": dataset_name,
                "datasource_id": datasource_id,
                "source": source,
            }
        ]
    try:
        datasource_service.browse_catalog_columns(datasource_id, schema_name, table_name)
    except KeyError:
        return [
            {
                "code": "relation_not_found",
                "message": f"Dataset {dataset_name} source {source} was not found in datasource {datasource_id}",
                "dataset": dataset_name,
                "datasource_id": datasource_id,
                "source": source,
            }
        ]
    except ValueError as error:
        return [
            {
                "code": "datasource_not_ready",
                "message": str(error),
                "dataset": dataset_name,
                "datasource_id": datasource_id,
                "source": source,
            }
        ]
    return []
```

Adjust `SemanticModelV2Service.__init__` to accept an optional datasource service:

```python
def __init__(self, store: SQLiteMetadataStore, datasource_service: Any = None) -> None:
    self.store = store
    self.datasource_service = datasource_service
```

In `app/api/app_factory.py`, construct `SemanticModelV2Service(metadata_store, datasource_service)`.

- [ ] **Step 6: Add readiness API tests**

In `tests/test_semantic_v2_api.py`, add:

```python
class TestSemanticModelReadinessDatasetNativeAPI(unittest.TestCase):
    def test_readiness_reports_missing_datasource(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(visibility="private", owner_user="alice"),
        )

        resp = client.get("/semantic-models/test_model/readiness")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["blockers"][0]["code"], "datasource_not_found")
```

- [ ] **Step 7: Run semantic v2 tests**

Run:

```bash
.venv/bin/pytest tests/test_semantic_v2_api.py tests/test_semantic_v2_service.py -q
```

Expected: pass.

- [ ] **Step 8: Commit semantic validation/readiness**

Run:

```bash
git add app/semantic_service_v2/validation.py app/semantic_service_v2/service.py app/api/app_factory.py tests/test_semantic_v2_api.py tests/test_semantic_v2_service.py
git commit -m "feat: validate dataset-native semantic grounding" -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 6: Remove Binding-Centric Runtime Dependencies

**Files:**
- Modify: `app/service.py`
- Modify: `app/time_axis_metadata.py`
- Modify: `app/analysis_core/validator.py`
- Modify: `app/analysis_core/compiler.py`
- Modify: `app/analysis_core/typed_resolution.py`
- Modify: `app/semantic_runtime/repository.py`
- Test: `tests/test_intent_api.py`
- Test: `tests/test_time_axis_metadata.py`
- Test: `tests/test_observe_artifact_lineage.py`

- [ ] **Step 1: Inventory binding runtime usage**

Run:

```bash
rg -n "typed_bindings|carrier_bindings|field_bindings|time_bindings|resolve_binding_ref|resolved_bindings|interface_contract\\.get\\(\" app/service.py app/time_axis_metadata.py app/analysis_core app/semantic_runtime tests/test_intent_api.py tests/test_time_axis_metadata.py tests/test_observe_artifact_lineage.py
```

Expected: every remaining hit is either a target for deletion or a private compiler metadata field that does not require persisted bindings.

- [ ] **Step 2: Remove persisted binding reads**

Delete methods that query `typed_bindings`, `carrier_bindings`, `field_bindings`, or `time_bindings`. Replace callers with dataset-native failures where runtime does not yet support the old path:

```python
raise ValueError(
    "binding_grounding_removed: v2 runtime uses dataset.datasource_id, dataset.source, and field.expression"
)
```

Use this explicit message only inside code paths that still receive legacy binding refs.

- [ ] **Step 3: Update compiler validation gate**

In `app/analysis_core/validator.py`, remove `_gate_binding_compatibility` checks that require `carrier_bindings` and `field_bindings`. Keep entity field resolution checks. If the validation pipeline expects a list from `_gate_binding_compatibility`, make it return an empty list:

```python
def _gate_binding_compatibility(
    step_type: str,
    resolved_inputs: ResolvedCompilerInputs,
) -> list[ValidationIssue]:
    _ = step_type
    _ = resolved_inputs
    return []
```

- [ ] **Step 4: Remove binding snapshots from compiler output**

In `app/analysis_core/compiler.py`, remove emitted `carrier_bindings` nodes and `resolved_binding_refs` metadata. Keep field and dataset references. If a helper only builds `CarrierBinding` from binding contracts, delete it and its TypedDict references from `app/analysis_core/ir.py`.

- [ ] **Step 5: Update legacy intent tests**

In `tests/test_intent_api.py`, delete test sections that create `/semantic/bindings` or insert into binding tables. For tests that still prove observe/intent behavior, rewrite fixtures to create v2 semantic models with dataset datasource extensions and field expressions.

Use this model fixture shape:

```python
{
    "name": "commerce",
    "datasets": [
        {
            "name": "orders",
            "source": "analytics.orders",
            "fields": [
                {
                    "name": "amount",
                    "expression": {
                        "dialects": [
                            {"dialect": "ANSI_SQL", "expression": "amount"}
                        ]
                    },
                    "custom_extensions": [
                        {
                            "vendor_name": "MARIVO",
                            "data": "{\"data_type\":\"number\"}"
                        }
                    ],
                }
            ],
            "custom_extensions": [
                {
                    "vendor_name": "MARIVO",
                    "data": "{\"datasource_id\":\"ds_test\"}"
                }
            ],
        }
    ],
}
```

- [ ] **Step 6: Run runtime-focused tests**

Run:

```bash
.venv/bin/pytest tests/test_intent_api.py tests/test_time_axis_metadata.py tests/test_observe_artifact_lineage.py -q
```

Expected: pass after deleting or rewriting binding-specific tests.

- [ ] **Step 7: Commit runtime binding cleanup**

Run:

```bash
git add app/service.py app/time_axis_metadata.py app/analysis_core/validator.py app/analysis_core/compiler.py app/analysis_core/typed_resolution.py app/analysis_core/ir.py app/semantic_runtime/repository.py tests/test_intent_api.py tests/test_time_axis_metadata.py tests/test_observe_artifact_lineage.py
git commit -m "refactor: remove persisted binding runtime dependencies" -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 7: Remove MCP Binding, Sync, And Datasource Object Surfaces

**Files:**
- Modify: `marivo-mcp/src/marivo_mcp/inventory.py`
- Modify: `marivo-mcp/src/marivo_mcp/tools/__init__.py`
- Modify: `marivo-mcp/src/marivo_mcp/resources/__init__.py`
- Modify: `marivo-mcp/README.md`
- Modify: `tests/test_marivo_mcp_inventory.py`
- Modify: `tests/test_marivo_mcp_transport.py`
- Modify: `tests/test_marivo_mcp_resources.py`

- [ ] **Step 1: Add MCP absence tests**

In `tests/test_marivo_mcp_inventory.py`, add:

```python
def test_dataset_native_grounding_removed_mcp_surfaces_absent() -> None:
    from marivo_mcp.inventory import MCP_SURFACES

    removed_fragments = {
        "/semantic/bindings",
        "/datasources/{datasource_id}/objects",
        "/datasources/{datasource_id}/sync",
    }
    surface_blob = "\n".join(
        path
        for surface in MCP_SURFACES
        for path in getattr(surface, "http_paths", ())
    )

    for fragment in removed_fragments:
        assert fragment not in surface_blob
```

- [ ] **Step 2: Remove inventory entries**

In `marivo-mcp/src/marivo_mcp/inventory.py`, delete all `McpSurfaceSpec` entries for:

```text
create_binding
list_bindings
get_binding
update_binding
validate_binding
activate_binding
deprecate_binding
publish_binding
sync_datasource
get_datasource_objects
get_datasource_object
get_sync_selections
marivo://datasources/{datasource_id}/objects
marivo://datasources/{datasource_id}/objects/{object_id}
```

Add a tool entry for `/datasources/{datasource_id}/browse/columns` if the MCP tool inventory mirrors live browse endpoints.

- [ ] **Step 3: Remove MCP tool models and functions**

In `marivo-mcp/src/marivo_mcp/tools/__init__.py`, delete Pydantic helper classes and functions dedicated only to binding creation/update and datasource object/sync reads. Keep live datasource browse and preview tools.

- [ ] **Step 4: Remove MCP resources**

In `marivo-mcp/src/marivo_mcp/resources/__init__.py`, remove the `"bindings"` resource entry and remove datasource object resources.

- [ ] **Step 5: Update MCP docs and tests**

Remove tests that call deleted tools/resources from:

```text
tests/test_marivo_mcp_transport.py
tests/test_marivo_mcp_resources.py
```

Update `marivo-mcp/README.md` to describe live browse columns and remove binding/object/sync sections.

- [ ] **Step 6: Run MCP tests**

Run:

```bash
.venv/bin/pytest tests/test_marivo_mcp_inventory.py tests/test_marivo_mcp_transport.py tests/test_marivo_mcp_resources.py -q
```

Expected: pass.

- [ ] **Step 7: Commit MCP cleanup**

Run:

```bash
git add marivo-mcp/src/marivo_mcp/inventory.py marivo-mcp/src/marivo_mcp/tools/__init__.py marivo-mcp/src/marivo_mcp/resources/__init__.py marivo-mcp/README.md tests/test_marivo_mcp_inventory.py tests/test_marivo_mcp_transport.py tests/test_marivo_mcp_resources.py
git commit -m "refactor: remove mcp sync and binding surfaces" -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 8: Remove Frontend Sync/Object/Binding UI

**Files:**
- Modify: `frontend/src/api/hooks.ts`
- Modify: `frontend/src/pages/OperationsPage.tsx`
- Modify: `frontend/src/pages/OperationsPage.test.tsx`
- Modify: `frontend/src/pages/SemanticLayerPage.tsx`
- Modify: `frontend/src/fixtures/mockApi.ts`
- Modify: `frontend/src/fixtures/mockData.ts`

- [ ] **Step 1: Remove source object hook and binding nav**

In `frontend/src/api/hooks.ts`, remove:

```typescript
{ key: "bindings", label: "Bindings", path: "/semantic/bindings" }
```

Delete `useSourceObjects`. Add this live columns hook:

```typescript
export function useDatasourceColumns(
  datasourceId?: string,
  schemaName?: string,
  tableName?: string,
  enabled = true,
) {
  return useQuery({
    queryKey: ["datasource-columns", datasourceId, schemaName, tableName],
    enabled: Boolean(datasourceId && schemaName && tableName && enabled),
    queryFn: async () =>
      unwrapList(
        await apiClient.get(`/datasources/${datasourceId}/browse/columns`, {
          params: { schema_name: schemaName, table_name: tableName },
        }),
      ),
  });
}
```

- [ ] **Step 2: Remove Operations sync fields**

In `frontend/src/pages/OperationsPage.tsx`, remove `sync_mode`, `allow_sync`, sync controls, synced objects drawer, and buttons labeled `Synced Objects`. Datasource forms should only expose:

```text
datasource_type
display_name
connection
policy.allow_live_browse
policy.allow_identity_reuse
```

- [ ] **Step 3: Remove SemanticLayer source object browser and binding form fields**

In `frontend/src/pages/SemanticLayerPage.tsx`, delete `SourceObjectBrowser` and remove the `source-objects` tab. Remove form controls named `time_bindings`, `field_bindings`, or `carrier_bindings`.

- [ ] **Step 4: Update mock API**

In `frontend/src/fixtures/mockApi.ts`, remove `/sources/{id}/objects` and `/semantic/bindings` handlers. Add a mock for:

```typescript
const columnsMatch = clean.match(/^\/datasources\/([^/]+)\/browse\/columns$/);
if (columnsMatch) {
  return [
    { name: "order_id", schema_name: "analytics", table_name: "orders", data_type: "string", properties: {} },
    { name: "amount", schema_name: "analytics", table_name: "orders", data_type: "number", properties: {} },
  ];
}
```

- [ ] **Step 5: Update frontend tests**

In `frontend/src/pages/OperationsPage.test.tsx`, delete tests named:

```text
shows source sync controls
opens the synced source objects drawer
opens mapping drawer with catalog row controls
```

Add a test that verifies no sync controls are present:

```typescript
it("does not show removed datasource sync controls", async () => {
  render(<OperationsPage />);

  expect(screen.queryByText(/Sync mode/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Synced Objects/i })).not.toBeInTheDocument();
});
```

- [ ] **Step 6: Run frontend checks**

Run:

```bash
cd frontend && npm run test -- OperationsPage.test.tsx --runInBand
cd frontend && npm run typecheck
cd frontend && npm run lint
```

Expected: pass.

- [ ] **Step 7: Commit frontend cleanup**

Run:

```bash
git add frontend/src/api/hooks.ts frontend/src/pages/OperationsPage.tsx frontend/src/pages/OperationsPage.test.tsx frontend/src/pages/SemanticLayerPage.tsx frontend/src/fixtures/mockApi.ts frontend/src/fixtures/mockData.ts
git commit -m "refactor: remove sync and binding frontend surfaces" -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 9: Update Documentation And Marivo Skill Guidance

**Files:**
- Modify: `docs/api/semantic.md`
- Modify: `docs/api/sources.md`
- Modify: `docs/api/quickstart.md`
- Modify: `docs/api/errors.md`
- Modify: `docs/ui/frontend-implementation.zh.md`
- Modify: `marivo-skill/marivo/references/semantic-layer.md`
- Modify: `marivo-skill/marivo/references/http-contracts.md`
- Modify: `marivo-skill/marivo/references/infrastructure.md`

- [ ] **Step 1: Replace semantic grounding guidance**

In `docs/api/semantic.md` and `marivo-skill/marivo/references/semantic-layer.md`, replace binding/synced source guidance with:

```markdown
## Dataset-Native Physical Grounding

In v2, Dataset and Field are the only persisted physical grounding contract.

- `dataset.custom_extensions[].data.datasource_id` selects the datasource.
- `dataset.source` is the datasource-local relation FQN, such as `schema.table` or `catalog.schema.table`.
- `field.expression` names a physical column or computed SQL expression.
- Metrics, dimensions, predicates, and relationships reference datasets and fields; they do not create binding-owned carrier or field surfaces.

Datasource catalog metadata is live. Use `/datasources/{id}/browse/schemas`, `/browse/tables`, `/browse/columns`, and `/catalog/preview` for discovery and validation. There is no sync step and no persisted source object cache.
```

- [ ] **Step 2: Remove binding examples**

Delete sections from docs and skill references that show:

```text
POST /semantic/bindings
carrier_bindings
field_bindings
time_bindings
source_objects
sync selections
```

- [ ] **Step 3: Update quickstart**

In `docs/api/quickstart.md`, replace binding creation commands with an OSI semantic model import or private create payload that includes:

```json
{
  "name": "orders",
  "source": "analytics.orders",
  "custom_extensions": [
    {
      "vendor_name": "MARIVO",
      "data": "{\"datasource_id\":\"ds_...\"}"
    }
  ],
  "fields": [
    {
      "name": "amount",
      "expression": {
        "dialects": [
          {
            "dialect": "ANSI_SQL",
            "expression": "amount"
          }
        ]
      }
    }
  ]
}
```

- [ ] **Step 4: Update error guidance**

In `docs/api/errors.md`, remove rows for binding grounding mistakes and add rows:

```markdown
| Symptom | Fix |
| --- | --- |
| dataset readiness reports `datasource_not_found` | create or select a valid datasource and put its id in the MARIVO dataset extension |
| dataset readiness reports `relation_not_found` | update `dataset.source` to a live `schema.table` or `catalog.schema.table` FQN from datasource browse |
| field readiness reports `field_expression_invalid` | update `field.expression.dialects[]` so the expression compiles against the dataset relation |
```

- [ ] **Step 5: Run documentation search**

Run:

```bash
rg -n "semantic/bindings|carrier_bindings|field_bindings|time_bindings|source_objects|sync selections|allow_sync|sync_mode" docs marivo-skill
```

Expected: no hits that describe active v2 behavior. Hits in historical design specs are acceptable only under `docs/superpowers/specs` or `docs/superpowers/plans`.

- [ ] **Step 6: Commit docs cleanup**

Run:

```bash
git add docs/api/semantic.md docs/api/sources.md docs/api/quickstart.md docs/api/errors.md docs/ui/frontend-implementation.zh.md marivo-skill/marivo/references/semantic-layer.md marivo-skill/marivo/references/http-contracts.md marivo-skill/marivo/references/infrastructure.md
git commit -m "docs: document dataset-native grounding contract" -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 10: Final Verification And Cleanup

**Files:**
- Verify only unless checks reveal a scoped fix.

- [ ] **Step 1: Verify removed backend symbols**

Run:

```bash
rg -n "source_objects|sync_jobs|sync_selections|typed_bindings|carrier_bindings|field_bindings|time_bindings|/semantic/bindings|/sync/selections|/datasources/\\{datasource_id\\}/objects|allow_sync|sync_mode" app tests marivo-mcp frontend docs marivo-skill
```

Expected: no active-code hits. Historical plan/spec hits under `docs/superpowers` are allowed.

- [ ] **Step 2: Run backend verification**

Run:

```bash
make test
make typecheck
make lint
```

Expected: all pass.

- [ ] **Step 3: Run frontend verification**

Run:

```bash
cd frontend && npm run test
cd frontend && npm run typecheck
cd frontend && npm run lint
cd frontend && npm run build
```

Expected: all pass.

- [ ] **Step 4: Run MCP package verification**

Run:

```bash
cd marivo-mcp && ../.venv/bin/pytest ../../tests/test_marivo_mcp_inventory.py ../../tests/test_marivo_mcp_transport.py ../../tests/test_marivo_mcp_resources.py -q
```

Expected: pass.

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git status --short --untracked-files=all
git diff --stat HEAD
```

Expected: clean status after commits, or only explicitly accepted local files unrelated to this cleanup.

- [ ] **Step 6: Handle final verification failures**

If Step 1 through Step 4 fails, do not create a broad final cleanup commit. Return to the task that owns the failing area, add a focused RED or regression test when the failure is not already covered, apply the smallest scoped fix there, rerun that task's verification command, and commit using that task's commit boundary.

If Step 1 through Step 4 passes, do not create an empty commit.

## Self-Review

Spec coverage:

- Removed sync and persisted catalog cache: Tasks 1, 2, 3, 4, 8, 9, and 10.
- Removed typed bindings: Tasks 1, 2, 6, 7, 8, 9, and 10.
- Dataset/field native grounding validation and readiness: Task 5.
- Live datasource browse/preview with columns browse: Tasks 4 and 5.
- Runtime/compiler transition away from persisted bindings: Task 6.
- MCP, frontend, docs, and tests: Tasks 7, 8, 9, and 10.

Placeholder scan:

- The plan uses concrete file paths, commands, and target code snippets.
- It does not rely on compatibility shims, migration of old binding rows, or hidden persisted cache.

Type consistency:

- Datasource policy removes `allow_sync` everywhere.
- Datasource registration/update remove `sync_mode` everywhere.
- Live columns use `DatasourceColumnResponse` and `browse_catalog_columns`.
- Semantic readiness blockers use the agreed codes: `datasource_not_found`, `datasource_not_ready`, `relation_not_found`, and `field_expression_invalid`.
