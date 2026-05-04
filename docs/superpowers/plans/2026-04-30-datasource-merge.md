---
status: completed
created: 2026-04-30
---

# Datasource Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge source/engine/mapping into a single datasource object, eliminating the mapping projection layer and simplifying the auth contract to a boolean `allow_identity_reuse` field.

**Architecture:** Replace the three-object model (SourceRegistry + EngineRegistry + MappingRegistry) with a single DatasourceRegistry. The routing layer no longer resolves through mappings — it goes directly from source_object → datasource → execution. The engine auth contract (mode/username_source/fallback_username) is replaced by a single boolean `policy.allow_identity_reuse`.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, DuckDB (analytics), SQLite/MySQL (metadata), pytest

---

## Task 1: Update Storage Schema — DDL

**Files:**
- Modify: `app/storage/schema.py`
- Modify: `app/storage/sqlite_metadata.py`

Replace `sources`, `engines`, and `source_execution_mappings` tables with a single `datasources` table. Update `source_objects` FK reference from `sources(source_id)` to `datasources(datasource_id)`. Update `sync_jobs` and `sync_selections` FK references similarly.

- [ ] **Step 1: Replace table definitions in `app/storage/schema.py`**

Find the DDL list (starts around line 73). Replace the `sources` table DDL with the `datasources` table DDL. Delete the `engines` and `source_execution_mappings` DDL blocks. Update `source_objects`, `sync_jobs`, and `sync_selections` FK references.

Replace the `sources` table DDL (lines 73-84) with:

```python
"""
CREATE TABLE IF NOT EXISTS datasources (
    datasource_id   TEXT PRIMARY KEY,
    datasource_type TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    connection_json TEXT NOT NULL DEFAULT '{}',
    sync_mode       TEXT NOT NULL DEFAULT 'selected',
    policy_json     TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
""",
```

Update `source_objects` FK (around line 89) — rename column from `source_id` to `datasource_id`:
```python
datasource_id   TEXT NOT NULL REFERENCES datasources(datasource_id),
```

Delete the `engines` table DDL (lines 794-809) and `source_execution_mappings` table DDL (lines 811-822) entirely.

Update `sync_jobs` FK (around line 782) — rename column from `source_id` to `datasource_id`:
```python
datasource_id   TEXT NOT NULL REFERENCES datasources(datasource_id),
```

Update `sync_selections` FK (around line 838) — rename column from `source_id` to `datasource_id`:
```python
datasource_id     TEXT NOT NULL REFERENCES datasources(datasource_id),
```

- [ ] **Step 2: Update `app/storage/sqlite_metadata.py`**

This file contains legacy migration DDL. Update all references to `sources` table to `datasources`, remove `engines` and `source_execution_mappings` table DDL blocks, and update FK references. Rename `source_id` columns to `datasource_id` in `source_objects`, `sync_jobs`, and `sync_selections` tables. Search for every `CREATE TABLE IF NOT EXISTS sources`, `CREATE TABLE IF NOT EXISTS engines`, `CREATE TABLE IF NOT EXISTS source_execution_mappings`, and every `REFERENCES sources` or `REFERENCES engines` occurrence.

- [ ] **Step 3: Commit**

```bash
git add app/storage/schema.py app/storage/sqlite_metadata.py
git commit -m "refactor: replace sources/engines/mappings DDL with datasources table"
```

---

## Task 2: Create Datasource API Models

**Files:**
- Modify: `app/api/models/_infrastructure.py`

Replace all source/engine/mapping Pydantic models with datasource models. Delete `SourceAuthorityPayload`, `SourceSyncPayload`, `SourcePolicyPayload`, `SourceRegisterRequest`, `SourceUpdateRequest`, `SourceResponse`, `EngineAuthPayload`, `EngineDefaultNamespacePayload`, `EngineDeploymentCapabilitiesPayload`, `EnginePolicyPayload`, `EngineRegisterRequest`, `EngineUpdateRequest`, `EngineResponse`, `MappingCatalogEntryPayload`, `MappingCreateRequest`, `MappingUpdateRequest`, `MappingResponse`, `MappingDeleteResponse`, `EngineDeleteResponse`, `SourceMappingSummaryResponse`, `EngineMappingSummaryResponse`, `MappingDeleteResponse`, and all related sub-models.

Add new datasource models.

- [ ] **Step 1: Add new datasource models**

Add these models at the top of the source/engine model sections (replacing them):

```python
# =============================================================================
# Datasource models
# =============================================================================


class DatasourcePolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_live_browse: bool = True
    allow_sync: bool = True
    allow_identity_reuse: bool = False


class DatasourceRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasource_type: Literal["duckdb", "trino"]
    display_name: str
    connection: dict[str, Any] = Field(default_factory=dict)
    sync_mode: Literal["selected", "all", "none"] = "selected"
    policy: DatasourcePolicyPayload = Field(default_factory=DatasourcePolicyPayload)


class DatasourceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    connection: dict[str, Any] | None = None
    sync_mode: Literal["selected", "all", "none"] | None = None
    policy: DatasourcePolicyPayload | None = None


class DatasourcePolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_live_browse: bool = True
    allow_sync: bool = True
    allow_identity_reuse: bool = False


class DatasourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasource_id: str
    datasource_type: Literal["duckdb", "trino"]
    display_name: str
    connection: dict[str, Any] = Field(default_factory=dict)
    sync_mode: str = "selected"
    policy: DatasourcePolicyResponse
    status: Literal["active", "inactive", "deprecated"] = "active"
    readiness_status: Literal["not_ready", "ready"] = "not_ready"
    failure_code: str | None = None
    created_at: str = ""
    updated_at: str = ""


class DatasourceDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasource_id: str
    deleted: bool = True
```

- [ ] **Step 2: Delete all old source/engine/mapping models**

Remove every class from `SourceAuthorityPayload` through `EngineDeleteResponse` and all mapping models. Remove `SourceMappingSummaryResponse`, `EngineMappingSummaryResponse`. Keep any non-source/engine/mapping models (session, routing response, etc.) intact.

Also update `RouteEngineResponse` — it currently has `engine_id` and `engine_type`. Change to:

```python
class RouteEngineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasource_id: str
    datasource_type: Literal["duckdb", "trino"]
    display_name: str
```

- [ ] **Step 3: Update `app/api/models/__init__.py` exports**

Update the `__init__.py` to export the new datasource models instead of source/engine/mapping models. Remove `SourceRegisterRequest`, `SourceResponse`, `SourceUpdateRequest`, `EngineRegisterRequest`, `EngineResponse`, `EngineUpdateRequest`, `MappingCreateRequest`, `MappingResponse`, `MappingUpdateRequest`, `MappingCatalogEntryPayload`, `MappingDeleteResponse`, `EngineDeleteResponse`. Add `DatasourceRegisterRequest`, `DatasourceResponse`, `DatasourceUpdateRequest`, `DatasourceDeleteResponse`, `DatasourcePolicyPayload`, `DatasourcePolicyResponse`.

- [ ] **Step 4: Commit**

```bash
git add app/api/models/_infrastructure.py app/api/models/__init__.py
git commit -m "refactor: replace source/engine/mapping API models with datasource models"
```

---

## Task 3: Update Factories

**Files:**
- Modify: `app/registry/factories.py`

Unify `SUPPORTED_SOURCE_TYPES` and `SUPPORTED_ENGINE_TYPES` into `SUPPORTED_DATASOURCE_TYPES`. Unify `validate_source_type` and `validate_engine_type` into `validate_datasource_type`. Unify `build_catalog_adapter` and `build_analytics_engine` — both take `datasource_type` + `connection`.

- [ ] **Step 1: Rewrite `app/registry/factories.py`**

```python
from __future__ import annotations

from typing import Any

from app.adapters.base import CatalogAdapter
from app.storage.analytics import AnalyticsEngine

SUPPORTED_DATASOURCE_TYPES: tuple[str, ...] = ("duckdb", "trino")


def _duckdb_path(connection: dict[str, Any]) -> str:
    for key in ("path", "database", "db_path"):
        value = connection.get(key)
        if isinstance(value, str) and value:
            return value
    raise KeyError("DuckDB connection requires one of: path, database, db_path")


def _trino_connect_kwargs(connection: dict[str, Any]) -> dict[str, Any]:
    """Extract Trino connection kwargs shared by catalog adapter and analytics engine."""
    raw_tags = connection.get("client_tags") or connection.get("client-tags")
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    raw_headers = connection.get("http_headers") or connection.get("http-headers")
    if isinstance(raw_headers, str):
        import json

        raw_headers = json.loads(raw_headers)
    kwargs: dict[str, Any] = {
        "host": connection["host"],
        "port": connection.get("port", 8080),
        "user": connection.get("user", "marivo"),
        "password": connection.get("password"),
        "http_scheme": connection.get("http_scheme") or connection.get("http-scheme", "http"),
        "catalog": connection.get("catalog", "hive"),
        "schema": connection.get("schema", "default"),
        "client_tags": raw_tags,
        "source": connection.get("source"),
        "http_headers": raw_headers,
    }
    if "request_timeout" in connection:
        kwargs["request_timeout"] = float(connection["request_timeout"])
    legacy_ps = connection.get("legacy_prepared_statements")
    if legacy_ps is not None:
        kwargs["legacy_prepared_statements"] = bool(legacy_ps)
    return kwargs


def validate_datasource_type(datasource_type: str) -> None:
    if datasource_type not in SUPPORTED_DATASOURCE_TYPES:
        supported = ", ".join(SUPPORTED_DATASOURCE_TYPES)
        raise ValueError(
            f"Unsupported datasource type: {datasource_type}. "
            f"Supported types: {supported}"
        )


def build_catalog_adapter(datasource_type: str, connection: dict[str, Any]) -> CatalogAdapter:
    validate_datasource_type(datasource_type)
    if datasource_type == "duckdb":
        from app.adapters.duckdb_adapter import DuckDBCatalogAdapter

        return DuckDBCatalogAdapter(_duckdb_path(connection))
    if datasource_type == "trino":
        from app.adapters.trino_adapter import TrinoCatalogAdapter

        return TrinoCatalogAdapter(**_trino_connect_kwargs(connection))
    raise ValueError(f"Unsupported datasource type: {datasource_type}")


def build_analytics_engine(datasource_type: str, connection: dict[str, Any]) -> AnalyticsEngine:
    validate_datasource_type(datasource_type)
    if datasource_type == "duckdb":
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine

        return DuckDBAnalyticsEngine(_duckdb_path(connection))
    if datasource_type == "trino":
        from app.storage.trino_analytics import TrinoAnalyticsEngine

        return TrinoAnalyticsEngine(**_trino_connect_kwargs(connection))
    raise ValueError(f"Unsupported datasource type: {datasource_type}")
```

- [ ] **Step 2: Commit**

```bash
git add app/registry/factories.py
git commit -m "refactor: unify source/engine factory functions into datasource factory"
```

---

## Task 4: Create DatasourceRegistry

**Files:**
- Create: `app/registry/datasource_registry.py`
- Delete: `app/registry/source_registry.py`
- Delete: `app/registry/engine_registry.py`
- Delete: `app/registry/mapping_registry.py`

Merge SourceRegistry, EngineRegistry, and MappingRegistry into DatasourceRegistry. Move `DependencyError` here. Implement the new `allow_identity_reuse` auth logic. The registry operates on the `datasources` table.

- [ ] **Step 1: Create `app/registry/datasource_registry.py`**

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.adapters.base import MAX_PREVIEW_ROWS, CatalogAdapter, PreviewFilters
from app.registry.common import now_iso
from app.registry.factories import build_analytics_engine, build_catalog_adapter, validate_datasource_type
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore


class DependencyError(Exception):
    """Raised when a delete is blocked by existing dependencies."""

    def __init__(self, message: str, dependencies: list[str] | None = None) -> None:
        super().__init__(message)
        self.dependencies = dependencies or []


@dataclass(slots=True)
class DatasourceValidationResult:
    is_valid: bool
    readiness_status: str
    failure_code: str | None = None

    def to_dict(self, *, datasource_id: str) -> dict[str, Any]:
        return {
            "datasource_id": datasource_id,
            "is_valid": self.is_valid,
            "readiness_status": self.readiness_status,
            "failure_code": self.failure_code,
        }


def _normalize_policy(
    datasource_type: str,
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = {
        "allow_live_browse": True,
        "allow_sync": True,
        "allow_identity_reuse": False,
    }
    if policy:
        normalized.update(policy)
    # duckdb: silently ignore allow_identity_reuse
    if datasource_type == "duckdb":
        normalized.pop("allow_identity_reuse", None)
    return normalized


def _loads_stored_json(raw: Any) -> Any:
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return None


class DatasourceRegistry:
    """Datasource registry: unified metadata authority and execution boundary."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    # -- CRUD --

    def register_datasource(
        self,
        datasource_type: str,
        display_name: str,
        connection: dict[str, Any] | None = None,
        sync_mode: str = "selected",
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_datasource_type(datasource_type)
        if sync_mode not in {"selected", "all", "none"}:
            raise ValueError("sync_mode must be 'selected', 'all', or 'none'")
        normalized_policy = _normalize_policy(datasource_type, policy)
        normalized_connection = dict(connection) if connection else {}

        datasource_id = f"ds_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO datasources (
                datasource_id, datasource_type, display_name,
                connection_json, sync_mode, policy_json,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [
                datasource_id,
                datasource_type,
                display_name,
                json.dumps(normalized_connection),
                sync_mode,
                json.dumps(normalized_policy),
                now,
                now,
            ],
        )
        return self.get_datasource(datasource_id)

    def get_datasource(self, datasource_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM datasources WHERE datasource_id = ?",
            [datasource_id],
        )
        if row is None:
            raise KeyError(f"Unknown datasource: {datasource_id}")
        return self._row_to_datasource(row)

    def list_datasources(self) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            "SELECT * FROM datasources ORDER BY created_at"
        )
        return [self._row_to_datasource(row) for row in rows]

    def ensure_datasource(
        self,
        datasource_type: str,
        display_name: str,
        connection: dict[str, Any] | None = None,
        sync_mode: str = "selected",
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_datasource_type(datasource_type)
        existing = self.metadata.query_one(
            "SELECT datasource_id FROM datasources WHERE display_name = ?",
            [display_name],
        )
        if existing is None:
            return self.register_datasource(
                datasource_type, display_name,
                connection=connection, sync_mode=sync_mode, policy=policy,
            )
        now = now_iso()
        normalized_policy = _normalize_policy(datasource_type, policy)
        normalized_connection = dict(connection) if connection else {}
        self.metadata.execute(
            """
            UPDATE datasources
            SET datasource_type = ?, connection_json = ?, sync_mode = ?,
                policy_json = ?, updated_at = ?
            WHERE datasource_id = ?
            """,
            [
                datasource_type,
                json.dumps(normalized_connection),
                sync_mode,
                json.dumps(normalized_policy),
                now,
                existing["datasource_id"],
            ],
        )
        return self.get_datasource(str(existing["datasource_id"]))

    def update_datasource(
        self,
        datasource_id: str,
        display_name: str | None = None,
        connection: dict[str, Any] | None = None,
        sync_mode: str | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.get_datasource(datasource_id)
        updates: list[str] = []
        params: list[Any] = []

        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name)
        if connection is not None:
            updates.append("connection_json = ?")
            params.append(json.dumps(dict(connection)))
        if sync_mode is not None:
            if sync_mode not in {"selected", "all", "none"}:
                raise ValueError("sync_mode must be 'selected', 'all', or 'none'")
            updates.append("sync_mode = ?")
            params.append(sync_mode)
        if policy is not None:
            normalized = _normalize_policy(existing["datasource_type"], policy)
            updates.append("policy_json = ?")
            params.append(json.dumps(normalized))

        if not updates:
            return existing

        updates.append("updated_at = ?")
        params.append(now_iso())
        params.append(datasource_id)
        self.metadata.execute(
            f"UPDATE datasources SET {', '.join(updates)} WHERE datasource_id = ?",
            params,
        )
        return self.get_datasource(datasource_id)

    def delete_datasource(self, datasource_id: str) -> dict[str, Any]:
        existing = self.get_datasource(datasource_id)
        # Check typed binding dependencies via source_objects
        binding_refs = self.metadata.query_rows(
            """
            SELECT 'typed_binding' AS ref_type, binding_id AS ref_id
            FROM typed_bindings tb
            WHERE EXISTS (
                SELECT 1 FROM source_objects so
                WHERE so.source_id = ? AND so.object_id = tb.carrier_object_id
            )
            LIMIT 10
            """,
            [datasource_id],
        )
        if binding_refs:
            refs = [f"{r['ref_type']}:{r['ref_id']}" for r in binding_refs]
            raise DependencyError(
                f"Cannot delete datasource: {len(binding_refs)} binding(s) depend on it",
                dependencies=refs,
            )
        self.metadata.execute(
            "DELETE FROM datasources WHERE datasource_id = ?",
            [datasource_id],
        )
        return {"datasource_id": datasource_id, "deleted": True}

    # -- Validation / Readiness --

    def validate_datasource(self, datasource_id: str) -> DatasourceValidationResult:
        ds = self.get_datasource(datasource_id)
        return self.evaluate_datasource(ds)

    @staticmethod
    def evaluate_datasource(ds: dict[str, Any]) -> DatasourceValidationResult:
        if ds.get("status") not in {"active", None}:
            return DatasourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="datasource_inactive",
            )
        datasource_type = ds.get("datasource_type")
        if datasource_type not in {"duckdb", "trino"}:
            return DatasourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="datasource_invalid_type",
            )
        return DatasourceValidationResult(
            is_valid=True,
            readiness_status="ready",
        )

    def get_datasource_readiness(self, datasource_id: str) -> dict[str, Any]:
        ds = self.get_datasource(datasource_id)
        result = self.evaluate_datasource(ds)
        return result.to_dict(datasource_id=datasource_id)

    # -- Catalog adapter / sync mode / browse --

    def get_adapter(self, datasource_id: str) -> CatalogAdapter:
        ds = self.get_datasource(datasource_id)
        return build_catalog_adapter(ds["datasource_type"], ds["connection"])

    def get_sync_mode(self, datasource_id: str) -> str:
        ds = self.get_datasource(datasource_id)
        return ds.get("sync_mode", "selected")

    def list_objects(
        self, datasource_id: str, *, object_type: str | None = None
    ) -> list[dict[str, Any]]:
        params: list[Any] = [datasource_id]
        type_clause = " AND object_type = ?" if object_type else ""
        if object_type:
            params.append(object_type)
        rows = self.metadata.query_rows(
            f"""
            SELECT * FROM source_objects
            WHERE source_id = ?{type_clause}
            ORDER BY object_type, native_name
            """,
            params,
        )
        return [self._row_to_source_object(row) for row in rows]

    def get_object(self, datasource_id: str, object_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM source_objects WHERE source_id = ? AND object_id = ?",
            [datasource_id, object_id],
        )
        if row is None:
            raise KeyError(f"Unknown object: {object_id}")
        return self._row_to_source_object(row)

    def browse_catalog_schemas(
        self, datasource_id: str
    ) -> list[dict[str, Any]]:
        adapter = self.get_adapter(datasource_id)
        return adapter.list_schemas()

    def browse_catalog_tables(
        self, datasource_id: str, *, schema_name: str | None = None
    ) -> list[dict[str, Any]]:
        adapter = self.get_adapter(datasource_id)
        return adapter.list_tables(schema_name=schema_name)

    def preview_table(
        self,
        datasource_id: str,
        *,
        table_name: str,
        schema_name: str | None = None,
        catalog_name: str | None = None,
        filters: PreviewFilters = None,
        limit: int = MAX_PREVIEW_ROWS,
    ) -> list[dict[str, Any]]:
        adapter = self.get_adapter(datasource_id)
        return adapter.preview_table(
            table_name=table_name,
            schema_name=schema_name,
            catalog_name=catalog_name,
            filters=filters,
            limit=limit,
        )

    # -- Analytics engine building --

    def build_analytics_engine(
        self,
        datasource_id: str,
        *,
        session_id: str | None = None,
        execution_identity: dict[str, Any] | None = None,
    ) -> AnalyticsEngine:
        ds = self.get_datasource(datasource_id)
        runtime_connection = self._resolve_runtime_connection(
            ds, session_id=session_id, execution_identity=execution_identity,
        )
        return build_analytics_engine(ds["datasource_type"], runtime_connection)

    def resolve_runtime_connection(
        self,
        ds: dict[str, Any],
        *,
        session_id: str | None = None,
        execution_identity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve the runtime connection, applying allow_identity_reuse logic for Trino."""
        connection = dict(ds.get("connection") or {})
        datasource_type = ds.get("datasource_type", "")
        policy = dict(ds.get("policy") or {})

        if datasource_type != "trino":
            return connection

        allow_identity_reuse = policy.get("allow_identity_reuse", False)

        # Try to get execution identity
        identity = execution_identity or {}
        if session_id is not None and not identity:
            from app.sessions import SessionManager
            sm = SessionManager(self.metadata)
            identity = sm.get_execution_identity(session_id)

        session_user = identity.get("session_user")
        if isinstance(session_user, str) and session_user.strip():
            connection["user"] = session_user.strip()
        elif allow_identity_reuse:
            # Keep existing connection.user as fallback
            pass
        else:
            raise ValueError(
                "session_user_missing: this datasource requires session_user "
                "for Trino authentication (allow_identity_reuse is false)"
            )

        return connection

    # -- Sync selections --

    def add_sync_selection(
        self, datasource_id: str, schema_name: str, table_name: str
    ) -> dict[str, Any]:
        from uuid import uuid4 as _uuid4
        selection_id = f"sel_{_uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT OR IGNORE INTO sync_selections
                (selection_id, source_id, schema_name, table_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [selection_id, datasource_id, schema_name, table_name, now],
        )
        return {"selection_id": selection_id, "source_id": datasource_id,
                "schema_name": schema_name, "table_name": table_name}

    def remove_sync_selection(
        self, datasource_id: str, schema_name: str, table_name: str
    ) -> dict[str, Any]:
        self.metadata.execute(
            "DELETE FROM sync_selections WHERE source_id = ? AND schema_name = ? AND table_name = ?",
            [datasource_id, schema_name, table_name],
        )
        return {"removed": True}

    def list_sync_selections(self, datasource_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.metadata.query_rows(
                "SELECT * FROM sync_selections WHERE source_id = ? ORDER BY schema_name, table_name",
                [datasource_id],
            )
        ]

    def clear_sync_selections(self, datasource_id: str) -> dict[str, Any]:
        self.metadata.execute(
            "DELETE FROM sync_selections WHERE source_id = ?",
            [datasource_id],
        )
        return {"cleared": True}

    # -- Internal helpers --

    @staticmethod
    def _row_to_datasource(row: Any) -> dict[str, Any]:
        policy = _loads_stored_json(row.get("policy_json")) or {}
        connection = _loads_stored_json(row.get("connection_json")) or {}
        ds = dict(row)
        ds["policy"] = policy
        ds["connection"] = connection
        ds.pop("policy_json", None)
        ds.pop("connection_json", None)

        # Compute readiness
        validation = DatasourceRegistry.evaluate_datasource(ds)
        ds["readiness_status"] = validation.readiness_status
        ds["failure_code"] = validation.failure_code
        return ds

    @staticmethod
    def _row_to_source_object(row: Any) -> dict[str, Any]:
        obj = dict(row)
        obj["authority_locator"] = _loads_stored_json(row.get("authority_locator_json"))
        obj["properties"] = _loads_stored_json(row.get("properties_json"))
        obj.pop("authority_locator_json", None)
        obj.pop("properties_json", None)
        return obj
```

- [ ] **Step 2: Commit**

```bash
git add app/registry/datasource_registry.py
git commit -m "feat: add DatasourceRegistry merging source/engine/mapping registries"
```

---

## Task 5: Update Registry Package Exports

**Files:**
- Modify: `app/registry/__init__.py`
- Delete: `app/registry/source_registry.py`
- Delete: `app/registry/engine_registry.py`
- Delete: `app/registry/mapping_registry.py`

- [ ] **Step 1: Rewrite `app/registry/__init__.py`**

```python
from app.registry.datasource_registry import DatasourceRegistry
from app.registry.factories import build_analytics_engine, build_catalog_adapter
from app.registry.sync_runtime import RegistrySyncEngine

__all__ = [
    "DatasourceRegistry",
    "RegistrySyncEngine",
    "build_analytics_engine",
    "build_catalog_adapter",
]
```

- [ ] **Step 2: Delete old registry files**

```bash
rm app/registry/source_registry.py app/registry/engine_registry.py app/registry/mapping_registry.py
```

- [ ] **Step 3: Commit**

```bash
git add app/registry/__init__.py
git rm app/registry/source_registry.py app/registry/engine_registry.py app/registry/mapping_registry.py
git commit -m "refactor: update registry exports, remove source/engine/mapping registries"
```

---

## Task 6: Replace Service Facades

**Files:**
- Delete: `app/sources.py`
- Delete: `app/engines.py`
- Delete: `app/mappings.py`
- Create: `app/datasources.py`

- [ ] **Step 1: Create `app/datasources.py`**

```python
from app.registry.datasource_registry import DatasourceRegistry
from app.registry.factories import build_catalog_adapter


class DatasourceService(DatasourceRegistry):
    """Thin compatibility facade over DatasourceRegistry."""
```

- [ ] **Step 2: Delete old facade files**

```bash
rm app/sources.py app/engines.py app/mappings.py
```

- [ ] **Step 3: Commit**

```bash
git add app/datasources.py
git rm app/sources.py app/engines.py app/mappings.py
git commit -m "refactor: replace source/engine/mapping facades with DatasourceService"
```

---

## Task 7: Simplify QueryRouter

**Files:**
- Modify: `app/routing.py`

The router currently resolves: source_object → mapping → engine. With the merge, it resolves: source_object → datasource. Remove all mapping logic. Remove dependency on `EngineService` and `MappingService`. Replace with `DatasourceService`.

- [ ] **Step 1: Rewrite `QueryRouter.__init__` and imports**

Replace the imports and constructor:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.datasources import DatasourceService
from app.source_object_locator import (
    normalize_source_object_authority_locator,
    qualify_execution_locator,
)
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore


class QueryRouter:
    """Resolve table names to an execution engine via datasource lookup."""

    def __init__(self, metadata: MetadataStore, datasource_service: DatasourceService) -> None:
        self.metadata = metadata
        self.datasource_service = datasource_service
```

- [ ] **Step 2: Simplify `resolve_route` method**

Replace the current mapping-based resolution. The core logic becomes:

1. Resolve table names to source_objects
2. Group by datasource_id (currently source_id)
3. Look up the datasource for each source_id
4. Validate datasource is ready
5. Build analytics engine from the datasource
6. For execution locators, use authority_locator directly (no mapping projection)

Key changes to `resolve_route`:
- Replace `_collect_source_candidates` with direct datasource lookup
- Remove `mapping_details`, `engine_priorities`, `selected_mapping_ids`
- After resolving table to source_object, get the datasource_id (=source_id), look up datasource, check readiness
- `ResolvedRoute.engine_id` → `ResolvedRoute.datasource_id`

- [ ] **Step 3: Simplify `resolve_execution_locator`**

The current method matches authority_catalog against mapping's catalog_mappings. With the merge, authority_locator IS the execution locator (no projection needed):

```python
def resolve_execution_locator(
    self,
    table_source_object: dict[str, Any],
) -> dict[str, Any]:
    authority_locator = dict(table_source_object.get("authority_locator") or {})
    return {
        "catalog": authority_locator.get("catalog"),
        "schema": authority_locator.get("schema"),
        "table": authority_locator.get("table") or table_source_object.get("native_name"),
        "datasource_id": table_source_object["source_id"],
        "readiness_blockers": [],
        "authority_locator": authority_locator,
    }
```

- [ ] **Step 4: Update `ResolvedRoute` dataclass**

```python
@dataclass
class ResolvedRoute:
    datasource_id: str
    engine: AnalyticsEngine | None = None
    qualified_names: dict[str, str] = field(default_factory=dict)
    selection_reason: str | None = None
    routing_detail: dict[str, Any] = field(default_factory=dict)
```

Remove `capability_profile`, `capability_score` fields (capabilities are now derived from datasource_type, no scoring needed).

- [ ] **Step 5: Remove mapping/engine helper methods**

Delete `_ready_mappings_for_source`, `_collect_source_candidates`, `_build_candidate_scores`, `_source_authority_catalogs`, `_mapping_projection_blocker`, `_failure_code_from_message`. Replace with simpler datasource readiness check.

- [ ] **Step 6: Update `qualify_table_name_for_engine`**

```python
def qualify_table_name_for_engine(
    self,
    datasource_id: str,
    execution_locator: dict[str, Any],
) -> str:
    ds = self.datasource_service.get_datasource(datasource_id)
    return qualify_execution_locator(
        execution_locator,
        engine_type=str(ds.get("datasource_type") or ""),
    )
```

- [ ] **Step 7: Commit**

```bash
git add app/routing.py
git commit -m "refactor: simplify QueryRouter to resolve via datasource without mapping"
```

---

## Task 8: Update Routing Runtime

**Files:**
- Modify: `app/execution/routing_runtime.py`

- [ ] **Step 1: Update `RoutingResolutionResult` and `RoutingRuntime`**

Replace `engine` / `engine_type` fields with `datasource` / `datasource_type` where applicable. Update imports to use `DatasourceService` instead of `EngineService`.

- [ ] **Step 2: Commit**

```bash
git add app/execution/routing_runtime.py
git commit -m "refactor: update routing runtime to use datasource"
```

---

## Task 9: Create Datasource API Endpoints

**Files:**
- Create: `app/api/datasources.py`
- Delete: `app/api/sources.py`
- Delete: `app/api/engines.py`
- Delete: `app/api/mappings.py`

- [ ] **Step 1: Create `app/api/datasources.py`**

```python
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services
from app.api.models import (
    DatasourceRegisterRequest,
    DatasourceResponse,
    DatasourceUpdateRequest,
    SyncSelectionRequest,
)
from app.registry.datasource_registry import DependencyError

router = APIRouter()


@router.post("/datasources", response_model=DatasourceResponse)
def register_datasource(payload: DatasourceRegisterRequest, request: Request) -> DatasourceResponse:
    services = get_services(request)
    try:
        return DatasourceResponse.model_validate(
            services.datasource_service.register_datasource(
                datasource_type=payload.datasource_type,
                display_name=payload.display_name,
                connection=payload.connection,
                sync_mode=payload.sync_mode,
                policy=payload.policy.model_dump(),
            )
        )
    except (ValueError, KeyError) as error:
        raise _http_error(error)


@router.get("/datasources", response_model=list[DatasourceResponse])
def list_datasources(request: Request) -> list[DatasourceResponse]:
    services = get_services(request)
    return [DatasourceResponse.model_validate(ds) for ds in services.datasource_service.list_datasources()]


@router.get("/datasources/{datasource_id}", response_model=DatasourceResponse)
def get_datasource(datasource_id: str, request: Request) -> DatasourceResponse:
    services = get_services(request)
    try:
        return DatasourceResponse.model_validate(
            services.datasource_service.get_datasource(datasource_id)
        )
    except KeyError as error:
        raise _http_error(error)


@router.put("/datasources/{datasource_id}", response_model=DatasourceResponse)
def update_datasource(
    datasource_id: str, payload: DatasourceUpdateRequest, request: Request
) -> DatasourceResponse:
    services = get_services(request)
    try:
        return DatasourceResponse.model_validate(
            services.datasource_service.update_datasource(
                datasource_id=datasource_id,
                display_name=payload.display_name,
                connection=payload.connection,
                sync_mode=payload.sync_mode,
                policy=payload.policy.model_dump() if payload.policy else None,
            )
        )
    except (ValueError, KeyError) as error:
        raise _http_error(error)


@router.delete("/datasources/{datasource_id}")
def delete_datasource(datasource_id: str, request: Request) -> dict[str, Any]:
    services = get_services(request)
    try:
        return services.datasource_service.delete_datasource(datasource_id)
    except KeyError as error:
        raise _http_error(error)
    except DependencyError as error:
        raise HTTPException(status_code=409, detail=str(error))


@router.post("/datasources/{datasource_id}/sync")
def sync_datasource(datasource_id: str, request: Request) -> dict[str, Any]:
    services = get_services(request)
    try:
        job = services.sync_engine.trigger_sync(datasource_id)
        return dict(job)
    except KeyError as error:
        raise _http_error(error)


@router.get("/datasources/{datasource_id}/browse/schemas")
def browse_schemas(datasource_id: str, request: Request) -> list[dict[str, Any]]:
    services = get_services(request)
    try:
        return services.datasource_service.browse_catalog_schemas(datasource_id)
    except KeyError as error:
        raise _http_error(error)


@router.get("/datasources/{datasource_id}/browse/tables")
def browse_tables(
    datasource_id: str,
    schema_name: str | None = Query(None),
    request: Request = None,
) -> list[dict[str, Any]]:
    services = get_services(request)
    try:
        return services.datasource_service.browse_catalog_tables(
            datasource_id, schema_name=schema_name
        )
    except KeyError as error:
        raise _http_error(error)


@router.post("/datasources/{datasource_id}/preview")
def preview_table(
    datasource_id: str,
    payload: dict[str, Any],
    request: Request,
) -> list[dict[str, Any]]:
    services = get_services(request)
    try:
        return services.datasource_service.preview_table(
            datasource_id,
            table_name=payload["table_name"],
            schema_name=payload.get("schema_name"),
            catalog_name=payload.get("catalog_name"),
            filters=payload.get("filters"),
            limit=payload.get("limit", 100),
        )
    except KeyError as error:
        raise _http_error(error)


def _http_error(error: KeyError | ValueError) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))
```

- [ ] **Step 2: Delete old API endpoint files**

```bash
rm app/api/sources.py app/api/engines.py app/api/mappings.py
```

- [ ] **Step 3: Commit**

```bash
git add app/api/datasources.py
git rm app/api/sources.py app/api/engines.py app/api/mappings.py
git commit -m "refactor: replace source/engine/mapping API endpoints with /datasources"
```

---

## Task 10: Update API Router Registration

**Files:**
- Modify: `app/api/router.py`

- [ ] **Step 1: Update imports and router list**

Replace `sources`, `engines`, `mappings` with `datasources`:

```python
from app.api import (
    approvals,
    calendar,
    catalog,
    datasources,
    governance,
    health,
    jobs,
    metrics,
    openapi_fragments,
    routing,
    semantic,
    sessions,
)


def include_api_routers(app: FastAPI) -> None:
    for router in (
        health.router,
        openapi_fragments.router,
        sessions.router,
        datasources.router,
        routing.router,
        semantic.router,
        catalog.router,
        governance.router,
        jobs.router,
        approvals.router,
        metrics.router,
        calendar.router,
    ):
        app.include_router(router)
```

- [ ] **Step 2: Commit**

```bash
git add app/api/router.py
git commit -m "refactor: update API router to use datasources instead of sources/engines/mappings"
```

---

## Task 11: Update Service Assembly

**Files:**
- Modify: `app/api/deps.py`
- Modify: `app/api/app_factory.py`

- [ ] **Step 1: Update `app/api/deps.py`**

Replace `SourceService`, `EngineService`, `MappingService` with `DatasourceService`:

```python
from app.datasources import DatasourceService
from app.routing import QueryRouter

@dataclass(slots=True)
class AppServices:
    resolved_path: Path | str
    config: MarivoConfig
    service: SemanticLayerService
    datasource_service: DatasourceService
    sync_engine: SyncEngine
    query_router: QueryRouter
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    governance_service: GovernanceService | None
    approval_service: ApprovalService
    metrics: MetricsCollector | None
    job_service: JobService
    job_repository: JobRepository
    semantic_service: SemanticService
    catalog_runtime: CatalogRuntimeService
```

Remove `source_service`, `engine_service`, `mapping_service` fields. Add `datasource_service`.

- [ ] **Step 2: Update `app/api/app_factory.py`**

In `_build_services`, replace:

```python
source_service = SourceService(metadata_store)
sync_engine = SyncEngine(metadata_store)
engine_service = EngineService(metadata_store)
mapping_service = MappingService(metadata_store)
query_router = QueryRouter(metadata_store, engine_service)
```

With:

```python
datasource_service = DatasourceService(metadata_store)
sync_engine = SyncEngine(metadata_store)
query_router = QueryRouter(metadata_store, datasource_service)
```

Update the `AppServices` construction and `_attach_state` accordingly.

- [ ] **Step 3: Commit**

```bash
git add app/api/deps.py app/api/app_factory.py
git commit -m "refactor: update service assembly to use DatasourceService"
```

---

## Task 12: Update Sync Runtime

**Files:**
- Modify: `app/registry/sync_runtime.py`

The sync runtime currently queries `sources` table and uses `source_id` FK. Update to use `datasources` table and `datasource_id`.

- [ ] **Step 1: Update all SQL queries in sync_runtime.py**

- Replace `SELECT * FROM sources` with `SELECT * FROM datasources`
- Replace column `authority_json` with `connection_json` (the connection is now top-level)
- Rename `source_id` column references to `datasource_id` (the column was renamed in the DDL)
- Remove `_get_authority_catalog` method that reads `authority_json` — catalog information is now derived from the connection parameters

- [ ] **Step 2: Commit**

```bash
git add app/registry/sync_runtime.py
git commit -m "refactor: update sync runtime to use datasources table"
```

---

## Task 13: Update Downstream Consumers

**Files:**
- Modify: `app/service.py` — update `QueryRouter` import
- Modify: `app/observability.py` — rename `correlation_engine_id` to `correlation_datasource_id`
- Modify: `app/execution/federation.py` — replace `engine_id` with `datasource_id`
- Modify: `app/semantic_service/common.py` — replace `engine_id` with `datasource_id`
- Modify: `app/analysis_core/ir.py` — remove `mapping_id` field from analysis IR row
- Modify: `app/semantic_runtime/catalog.py` — update `source_id` queries (column name unchanged)
- Modify: `app/api/models/catalog.py` — update any `source_id` field references
- Modify: `app/api/routing.py` — update route resolution to use datasource

This is a sweep task. For each file, search for `source_service`, `engine_service`, `mapping_service`, `engine_id`, `mapping_id`, `EngineService`, `MappingService`, `SourceService`, and update to use `datasource_service` / `DatasourceService` / `datasource_id`.

- [ ] **Step 1: Update each file listed above**

For `app/service.py`: Update `TYPE_CHECKING` import of `QueryRouter` — no functional change needed if the class name is the same.

For `app/observability.py`: Rename `correlation_engine_id` ContextVar to `correlation_datasource_id`.

For `app/execution/federation.py`: Replace `engine_id` field references with `datasource_id`.

For `app/semantic_service/common.py`: Replace `engine_id` field with `datasource_id`.

For `app/analysis_core/ir.py`: Remove `mapping_id` from analysis IR row if present.

For `app/api/routing.py`: Update route resolution response to use `datasource_id` instead of `engine_id`.

- [ ] **Step 2: Commit**

```bash
git add app/service.py app/observability.py app/execution/federation.py \
  app/semantic_service/common.py app/analysis_core/ir.py \
  app/semantic_runtime/catalog.py app/api/models/catalog.py app/api/routing.py
git commit -m "refactor: update downstream consumers to use datasource references"
```

---

## Task 14: Update Test Helpers

**Files:**
- Modify: `tests/semantic_test_helpers.py`
- Modify: `tests/shared_fixtures.py`

- [ ] **Step 1: Update `tests/semantic_test_helpers.py`**

- `seed_duckdb_source_object`: Change INSERT into `sources` table to INSERT into `datasources` table. Remove `authority_json` column, use `connection_json` instead. Remove `synthetic_catalog` from the JSON. Remove `intrinsic_capabilities_json`.
- `ensure_active_duckdb_mapping`: Remove entirely. No more engine/mapping seeding needed. The datasource IS the engine.
- `build_semantic_layer_service`: Replace `EngineService` with `DatasourceService` in QueryRouter construction.
- Update all direct SQL references from `sources` → `datasources`, remove `engines` and `source_execution_mappings` table INSERTs.

- [ ] **Step 2: Update `tests/shared_fixtures.py`**

Replace any `source_id`/`engine_id` fixture setup with `datasource_id` setup.

- [ ] **Step 3: Commit**

```bash
git add tests/semantic_test_helpers.py tests/shared_fixtures.py
git commit -m "refactor: update test helpers to use datasource"
```

---

## Task 15: Rewrite Core Datasource Tests

**Files:**
- Delete: `tests/test_sources.py`
- Delete: `tests/test_engines.py`
- Delete: `tests/test_mappings.py`
- Delete: `tests/test_registry_boundaries.py`
- Create: `tests/test_datasources.py`

- [ ] **Step 1: Write `tests/test_datasources.py`**

Cover these test cases (TDD — write tests first, then verify they pass with the new registry):

1. Register a DuckDB datasource — verify returned shape
2. Register a Trino datasource — verify returned shape
3. Get datasource by ID
4. List datasources
5. Update datasource display_name
6. Update datasource policy (including allow_identity_reuse)
7. Delete datasource
8. Delete datasource with binding dependency → DependencyError
9. Readiness: active datasource → ready
10. Readiness: inactive datasource → not_ready
11. DuckDB: allow_identity_reuse silently ignored
12. Trino: allow_identity_reuse=false, no session_user → session_user_missing
13. Trino: allow_identity_reuse=true, no session_user → uses connection.user
14. Trino: session_user provided → uses session_user
15. Sync mode validation
16. Datasource type validation
17. Browse schemas/tables (DuckDB)
18. Preview table (DuckDB)
19. Sync trigger

- [ ] **Step 2: Delete old test files**

```bash
rm tests/test_sources.py tests/test_engines.py tests/test_mappings.py tests/test_registry_boundaries.py
```

- [ ] **Step 3: Run tests**

```bash
make test
```

- [ ] **Step 4: Fix any failures**

- [ ] **Step 5: Commit**

```bash
git add tests/test_datasources.py
git rm tests/test_sources.py tests/test_engines.py tests/test_mappings.py tests/test_registry_boundaries.py
git commit -m "test: rewrite datasource tests replacing source/engine/mapping tests"
```

---

## Task 16: Update All Transitive Test Files

**Files:**
- Modify: All test files that import `SourceService`, `EngineService`, `MappingService`, `QueryRouter`, or reference `source_id`, `engine_id`, `mapping_id` as setup parameters

This is a sweep task. For each affected test file:

- Replace `SourceService` → `DatasourceService`
- Replace `EngineService` → `DatasourceService`
- Replace `MappingService` → remove (no longer needed)
- Replace `source_service` → `datasource_service`
- Replace `engine_service` → `datasource_service`
- Replace `mapping_service` → remove
- Replace `engine_id` in test setup/assertions with `datasource_id`
- Replace `mapping_id` references — remove or replace with `datasource_id`
- Replace `QueryRouter(metadata, engine_service)` → `QueryRouter(metadata, datasource_service)`
- Update any `seed_source_engine_mapping()` calls — replace with `seed_duckdb_source_object()` + direct datasource creation
- Update any direct SQL INSERTs into `sources`/`engines`/`source_execution_mappings` tables → INSERT into `datasources`

Affected files (from the exploration report):

- `tests/test_catalog_query.py`
- `tests/test_config.py`
- `tests/test_execution_substrate.py`
- `tests/test_intent_api.py`
- `tests/test_intent_attribute.py`
- `tests/test_intent_detect.py`
- `tests/test_intent_diagnose.py`
- `tests/test_intent_test.py`
- `tests/test_intent_validate.py`
- `tests/test_marivo_mcp_config.py`
- `tests/test_marivo_mcp_inventory.py`
- `tests/test_marivo_mcp_resources.py`
- `tests/test_marivo_mcp_smoke.py`
- `tests/test_marivo_mcp_target_resolution.py`
- `tests/test_marivo_mcp_transport.py`
- `tests/test_metric_dimension_resolution.py`
- `tests/test_mysql_metadata_ddl.py`
- `tests/test_mysql_metadata_integration.py`
- `tests/test_observe_artifact_lineage.py`
- `tests/test_observe_compare_lineage_reuse.py`
- `tests/test_openapi_fragments.py`
- `tests/test_regression_8_5.py`
- `tests/test_semantic_readiness.py`
- `tests/test_semantic_revision_dependency_plan.py`
- `tests/test_semantic_runtime.py`
- `tests/test_semantic_typed_api.py`
- `tests/test_semantic_typed_end_to_end.py`
- `tests/test_step_metadata.py`
- `tests/test_storage.py`
- `tests/test_time_axis_metadata.py`
- `tests/test_time_scope_resolution.py`

- [ ] **Step 1: Run a global search-and-replace sweep**

Use grep to find all files that need updating, then modify each one.

- [ ] **Step 2: Run full test suite**

```bash
make test
```

- [ ] **Step 3: Fix failures iteratively**

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "refactor: update all transitive test files to use datasource"
```

---

## Task 17: Update MCP Adapter

**Files:**
- Modify: `marivo-mcp/src/marivo_mcp/config.py`
- Modify: `marivo-mcp/src/marivo_mcp/inventory.py`
- Modify: `marivo-mcp/src/marivo_mcp/tools/__init__.py`
- Modify: `marivo-mcp/src/marivo_mcp/resources/__init__.py`

- [ ] **Step 1: Update `config.py`**

Replace `default_source_id` with `default_datasource_id`. Update `MARIVO_DEFAULT_SOURCE_ID` env var to `MARIVO_DEFAULT_DATASOURCE_ID`.

- [ ] **Step 2: Update `inventory.py`**

Replace `list_sources`, `register_source`, `preview_source_table`, `get_source`, `get_source_objects`, `get_source_object` surface specs with `list_datasources`, `register_datasource`, `preview_datasource_table`, `get_datasource`, `get_datasource_objects`, `get_datasource_object`.

- [ ] **Step 3: Update `tools/__init__.py`**

Replace `sync_source(source_id)` with `sync_datasource(datasource_id)`, `preview_source_table(source_id)` with `preview_datasource_table(datasource_id)`, etc.

- [ ] **Step 4: Update `resources/__init__.py`**

Replace `marivo://sources/{source_id}/objects` with `marivo://datasources/{datasource_id}/objects`, etc.

- [ ] **Step 5: Commit**

```bash
git add marivo-mcp/
git commit -m "refactor: update MCP adapter to use datasource endpoints"
```

---

## Task 18: Delete Legacy Spec Docs

**Files:**
- Delete: `specs/service/data-plane/source-engine-mapping-contract.md`
- Delete: `specs/service/data-plane/execution-auth-contract.md`

- [ ] **Step 1: Delete the files**

```bash
rm specs/service/data-plane/source-engine-mapping-contract.md specs/service/data-plane/execution-auth-contract.md
```

- [ ] **Step 2: Commit**

```bash
git rm specs/service/data-plane/source-engine-mapping-contract.md specs/service/data-plane/execution-auth-contract.md
git commit -m "docs: delete legacy source-engine-mapping and execution-auth specs"
```

---

## Task 19: Run Full Verification

- [ ] **Step 1: Run type check**

```bash
make typecheck
```

- [ ] **Step 2: Run linter**

```bash
make lint
```

- [ ] **Step 3: Run full test suite**

```bash
make test
```

- [ ] **Step 4: Fix any remaining issues**

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve remaining type/lint/test issues from datasource merge"
```

---

## Task 20: Final Cleanup

- [ ] **Step 1: Search for any remaining references**

```bash
grep -r "SourceService\|EngineService\|MappingService\|source_registry\|engine_registry\|mapping_registry\|source_engine_mapping\|synthetic_catalog\|engine_id\|mapping_id" app/ tests/ marivo-mcp/ --include="*.py" | grep -v "__pycache__" | grep -v ".pyc"
```

- [ ] **Step 2: Fix any remaining references found**

- [ ] **Step 3: Update `agent-guide.md` if needed**

Check if agent-guide.md references source/engine/mapping concepts and update.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: final cleanup after datasource merge refactor"
```
