from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.adapters.base import MAX_PREVIEW_ROWS, CatalogAdapter, PreviewFilters
from app.registry.common import now_iso
from app.registry.factories import build_catalog_adapter, validate_datasource_type
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore

logger = logging.getLogger("marivo.datasource_auth")


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


def _loads_stored_json(raw: Any) -> Any:
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return None


def _normalize_policy(datasource_type: str, policy: dict[str, Any] | None) -> dict[str, Any]:
    normalized = {
        "allow_live_browse": True,
        "allow_sync": True,
        "allow_identity_reuse": False,
    }
    if policy:
        normalized.update(policy)
    # duckdb silently removes allow_identity_reuse (not applicable)
    if datasource_type == "duckdb":
        normalized.pop("allow_identity_reuse", None)
    return normalized


def _normalize_sync(sync: dict[str, Any] | None) -> dict[str, Any]:
    mode = str((sync or {}).get("mode", "selected"))
    if mode == "by_select":
        mode = "selected"
    if mode not in {"selected", "all", "none"}:
        raise ValueError("sync.mode must be 'selected', 'all', or 'none'")
    return {"mode": mode}


class ExecutionAuthLoggingEngine(AnalyticsEngine):
    """Emit execution-auth success audit only when the engine is actually used."""

    def __init__(self, inner: AnalyticsEngine, auth_audit_payload: dict[str, Any]) -> None:
        self._inner = inner
        self._auth_audit_payload = dict(auth_audit_payload)
        self._logged = False

    def _log_once(self) -> None:
        if self._logged:
            return
        logger.info("execution_auth_resolved", extra=self._auth_audit_payload)
        self._logged = True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def initialize(self) -> None:
        self._log_once()
        self._inner.initialize()

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        self._log_once()
        return self._inner.query_rows(sql, params)

    def table_exists(self, table_name: str) -> bool:
        self._log_once()
        return self._inner.table_exists(table_name)

    def table_row_count(self, table_name: str) -> int:
        self._log_once()
        return self._inner.table_row_count(table_name)


class DatasourceRegistry:
    """Unified datasource registry combining catalog, execution, and sync access."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    # =========================================================================
    # CRUD
    # =========================================================================

    def register_datasource(
        self,
        datasource_type: str,
        display_name: str,
        connection: dict[str, Any],
        sync_mode: str = "selected",
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_datasource_type(datasource_type)
        normalized_policy = _normalize_policy(datasource_type, policy)

        datasource_id = f"ds_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO datasources (
                datasource_id,
                datasource_type,
                display_name,
                connection_json,
                sync_mode,
                policy_json,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [
                datasource_id,
                datasource_type,
                display_name,
                json.dumps(connection),
                sync_mode,
                json.dumps(normalized_policy),
                now,
                now,
            ],
        )
        return self.get_datasource(datasource_id)

    def get_datasource(self, datasource_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM datasources WHERE datasource_id = ?", [datasource_id]
        )
        if row is None:
            raise KeyError(f"Unknown datasource: {datasource_id}")
        return self._row_to_datasource(row)

    def list_datasources(self) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows("SELECT * FROM datasources ORDER BY created_at")
        return [self._row_to_datasource(row) for row in rows]

    def ensure_datasource(
        self,
        datasource_type: str,
        display_name: str,
        connection: dict[str, Any],
        sync_mode: str = "selected",
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_datasource_type(datasource_type)
        existing = self.metadata.query_one(
            "SELECT * FROM datasources WHERE display_name = ?",
            [display_name],
        )
        if existing is None:
            return self.register_datasource(
                datasource_type,
                display_name,
                connection,
                sync_mode=sync_mode,
                policy=policy,
            )

        now = now_iso()
        normalized_policy = _normalize_policy(datasource_type, policy)
        self.metadata.execute(
            """
            UPDATE datasources
            SET datasource_type = ?, connection_json = ?, sync_mode = ?,
                policy_json = ?, updated_at = ?
            WHERE datasource_id = ?
            """,
            [
                datasource_type,
                json.dumps(connection),
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
            params.append(json.dumps(connection))
        if sync_mode is not None:
            updates.append("sync_mode = ?")
            params.append(sync_mode)
        if policy is not None:
            normalized_policy = _normalize_policy(existing["datasource_type"], policy)
            updates.append("policy_json = ?")
            params.append(json.dumps(normalized_policy))

        if not updates:
            return existing

        params.extend([now_iso(), datasource_id])
        self.metadata.execute(
            f"UPDATE datasources SET {', '.join(updates)}, updated_at = ? WHERE datasource_id = ?",
            params,
        )
        return self.get_datasource(datasource_id)

    def delete_datasource(self, datasource_id: str) -> None:
        self.get_datasource(datasource_id)

        bindings_using_source_objects = self.metadata.query_rows(
            """
            SELECT DISTINCT b.binding_ref
            FROM typed_bindings b
            JOIN carrier_bindings cb ON cb.binding_id = b.binding_id
            JOIN source_objects o ON cb.source_object_ref = o.object_id
            WHERE o.datasource_id = ?
            """,
            [datasource_id],
        )
        if bindings_using_source_objects:
            refs = [str(row["binding_ref"]) for row in bindings_using_source_objects]
            raise DependencyError(
                f"Cannot delete datasource: {len(bindings_using_source_objects)} typed binding(s) depend on it",
                dependencies=refs,
            )

        self.metadata.execute("DELETE FROM sync_selections WHERE datasource_id = ?", [datasource_id])
        self.metadata.execute("DELETE FROM sync_jobs WHERE datasource_id = ?", [datasource_id])
        self.metadata.execute("DELETE FROM source_objects WHERE datasource_id = ?", [datasource_id])
        self.metadata.execute("DELETE FROM datasources WHERE datasource_id = ?", [datasource_id])

    # =========================================================================
    # Validation
    # =========================================================================

    def validate_datasource(self, datasource_id: str) -> dict[str, Any]:
        datasource = self.get_datasource(datasource_id)
        return self.evaluate_datasource(datasource).to_dict(datasource_id=datasource_id)

    def get_datasource_readiness(self, datasource_id: str) -> dict[str, Any]:
        validation = self.validate_datasource(datasource_id)
        return {
            "datasource_id": datasource_id,
            "readiness_status": validation["readiness_status"],
            "failure_code": validation["failure_code"],
        }

    def evaluate_datasource(self, datasource: dict[str, Any]) -> DatasourceValidationResult:
        if datasource["status"] != "active":
            return DatasourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="datasource_inactive",
            )

        datasource_type = datasource["datasource_type"]
        try:
            validate_datasource_type(datasource_type)
        except ValueError:
            return DatasourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="datasource_invalid_type",
            )

        connection = datasource.get("connection")
        if not isinstance(connection, dict):
            return DatasourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="datasource_invalid_connection",
            )

        try:
            build_catalog_adapter(datasource_type, connection)
        except (KeyError, TypeError, ValueError):
            return DatasourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="datasource_invalid_connection",
            )

        return DatasourceValidationResult(
            is_valid=True,
            readiness_status="ready",
        )

    # =========================================================================
    # Catalog
    # =========================================================================

    def get_adapter(self, datasource_id: str) -> CatalogAdapter:
        datasource = self.get_datasource(datasource_id)
        connection = datasource["connection"]
        return build_catalog_adapter(datasource["datasource_type"], connection)

    def get_sync_mode(self, datasource_id: str) -> str:
        row = self.metadata.query_one(
            "SELECT sync_mode FROM datasources WHERE datasource_id = ?",
            [datasource_id],
        )
        if row is None:
            raise KeyError(f"Unknown datasource: {datasource_id}")
        return str(row["sync_mode"])

    def list_objects(
        self,
        datasource_id: str,
        object_type: str | None = None,
        schema_name: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM source_objects WHERE datasource_id = ?"
        params: list[Any] = [datasource_id]
        if object_type:
            sql += " AND object_type = ?"
            params.append(object_type)
        if schema_name:
            sql += " AND json_extract(authority_locator_json, '$.schema') = ?"
            params.append(schema_name)
        sql += " ORDER BY fqn"
        rows = self.metadata.query_rows(sql, params)
        return [self._row_to_object(row) for row in rows]

    def get_object(self, datasource_id: str, object_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM source_objects WHERE object_id = ? AND datasource_id = ?",
            [object_id, datasource_id],
        )
        if row is None:
            raise KeyError(f"Object {object_id!r} not found in datasource {datasource_id!r}")
        return self._row_to_object(row)

    def patch_object_properties(
        self, datasource_id: str, object_id: str, user_props: dict[str, Any]
    ) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM source_objects WHERE object_id = ? AND datasource_id = ?",
            [object_id, datasource_id],
        )
        if row is None:
            raise KeyError(f"Object {object_id!r} not found in datasource {datasource_id!r}")
        if row["object_type"] != "column":
            raise ValueError(f"Object {object_id!r} is not a column (type={row['object_type']!r})")

        merged = {**json.loads(str(row["properties_json"])), **user_props}
        self.metadata.execute(
            "UPDATE source_objects SET properties_json = ?, updated_at = ? WHERE object_id = ?",
            [json.dumps(merged), now_iso(), object_id],
        )
        updated = self.metadata.query_one(
            "SELECT * FROM source_objects WHERE object_id = ?",
            [object_id],
        )
        if updated is None:
            raise KeyError(f"Object {object_id!r} not found in datasource {datasource_id!r}")
        return self._row_to_object(updated)

    def browse_catalog_schemas(self, datasource_id: str) -> list[dict[str, Any]]:
        datasource = self.get_datasource(datasource_id)
        connection = datasource["connection"]
        adapter = build_catalog_adapter(datasource["datasource_type"], connection)
        catalog_name = None
        if datasource["datasource_type"] == "trino":
            raw_catalog = connection.get("catalog")
            if isinstance(raw_catalog, str) and raw_catalog:
                catalog_name = raw_catalog
        elif datasource["datasource_type"] == "duckdb":
            catalog_name = "main"
        schemas = adapter.list_schemas(catalog_name)
        return [{"name": schema.native_name, "properties": schema.properties} for schema in schemas]

    def browse_catalog_tables(self, datasource_id: str, schema_name: str) -> list[dict[str, Any]]:
        adapter = self.get_adapter(datasource_id)
        tables = adapter.list_tables(schema_name)
        return [
            {"name": table.native_name, "schema": schema_name, "properties": table.properties}
            for table in tables
        ]

    def preview_table(
        self,
        datasource_id: str,
        schema_name: str,
        table_name: str,
        limit: int = 100,
        columns: list[str] | None = None,
        filters: PreviewFilters | None = None,
    ) -> dict[str, Any]:
        adapter = self.get_adapter(datasource_id)
        result = adapter.preview_table(
            schema_name=schema_name,
            table_name=table_name,
            limit=limit,
            columns=columns,
            filters=filters,
        )
        return {
            "datasource_id": datasource_id,
            "schema_name": schema_name,
            "table_name": table_name,
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "limit_requested": limit,
            "limit_applied": min(limit, MAX_PREVIEW_ROWS),
            "filters_applied": filters or {},
        }

    # =========================================================================
    # Analytics
    # =========================================================================

    def build_analytics_engine(
        self,
        datasource_id: str,
        *,
        session_id: str | None = None,
    ) -> AnalyticsEngine:
        from app.registry.factories import build_analytics_engine as _build_analytics_engine

        datasource = self.get_datasource(datasource_id)
        resolution = self._resolve_runtime_connection(datasource, session_id=session_id)
        runtime_engine = _build_analytics_engine(
            datasource["datasource_type"], resolution.connection
        )
        if resolution.auth_audit_payload is None:
            return runtime_engine
        return ExecutionAuthLoggingEngine(runtime_engine, resolution.auth_audit_payload)

    def _resolve_runtime_connection(
        self,
        datasource: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> _RuntimeConnectionResolution:
        connection = dict(datasource.get("connection") or {})
        datasource_type = str(datasource.get("datasource_type") or "")
        policy = dict(datasource.get("policy") or {})

        if datasource_type != "trino":
            return _RuntimeConnectionResolution(connection=connection)

        allow_identity_reuse = policy.get("allow_identity_reuse", False)

        # If session_user is provided in the connection, use it directly
        if connection.get("user"):
            return _RuntimeConnectionResolution(connection=connection)

        if allow_identity_reuse:
            # Keep connection.user as-is (the registered service user)
            return _RuntimeConnectionResolution(connection=connection)

        # No session_user and identity reuse not allowed — this is a config error
        raise ValueError(
            "session_user_missing: trino datasource without allow_identity_reuse "
            "requires session_user in connection"
        )

    # =========================================================================
    # Sync selections
    # =========================================================================

    def add_sync_selection(
        self, datasource_id: str, schema_name: str, table_name: str
    ) -> dict[str, Any]:
        self.get_datasource(datasource_id)
        existing = self.metadata.query_one(
            "SELECT * FROM sync_selections WHERE datasource_id = ? AND schema_name = ? AND table_name = ?",
            [datasource_id, schema_name, table_name],
        )
        if existing is not None:
            return dict(existing)

        selection_id = f"sel_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO sync_selections (selection_id, datasource_id, schema_name, table_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [selection_id, datasource_id, schema_name, table_name, now],
        )
        return {
            "selection_id": selection_id,
            "datasource_id": datasource_id,
            "schema_name": schema_name,
            "table_name": table_name,
            "created_at": now,
        }

    def remove_sync_selection(self, selection_id: str) -> None:
        existing = self.metadata.query_one(
            "SELECT selection_id FROM sync_selections WHERE selection_id = ?",
            [selection_id],
        )
        if existing is None:
            raise KeyError(f"Unknown selection: {selection_id}")
        self.metadata.execute("DELETE FROM sync_selections WHERE selection_id = ?", [selection_id])

    def list_sync_selections(self, datasource_id: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            "SELECT * FROM sync_selections WHERE datasource_id = ? ORDER BY schema_name, table_name",
            [datasource_id],
        )
        return [dict(row) for row in rows]

    def clear_sync_selections(self, datasource_id: str) -> None:
        self.get_datasource(datasource_id)
        self.metadata.execute("DELETE FROM sync_selections WHERE datasource_id = ?", [datasource_id])

    # =========================================================================
    # Row conversion
    # =========================================================================

    def _row_to_datasource(self, row: dict[str, Any]) -> dict[str, Any]:
        datasource_type = str(row["datasource_type"])
        raw_connection = _loads_stored_json(row["connection_json"])
        connection = raw_connection if isinstance(raw_connection, dict) else {}

        raw_policy = _loads_stored_json(row["policy_json"])
        policy_invalid = not isinstance(raw_policy, dict)
        policy = _normalize_policy(datasource_type, raw_policy if not policy_invalid else None)

        datasource: dict[str, Any] = {
            "datasource_id": row["datasource_id"],
            "datasource_type": datasource_type,
            "display_name": row["display_name"],
            "connection": connection,
            "sync_mode": str(row["sync_mode"]),
            "policy": policy,
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if policy_invalid:
            validation = DatasourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="datasource_invalid_policy",
            )
        else:
            validation = self.evaluate_datasource(datasource)
        datasource["readiness_status"] = validation.readiness_status
        datasource["failure_code"] = validation.failure_code
        return datasource

    def _row_to_object(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "object_id": row["object_id"],
            "datasource_id": row["datasource_id"],
            "object_type": row["object_type"],
            "parent_id": row["parent_id"],
            "native_name": row["native_name"],
            "native_id": row["native_id"],
            "fqn": row["fqn"],
            "authority_locator": json.loads(str(row["authority_locator_json"])),
            "properties": json.loads(str(row["properties_json"])),
            "sync_version": row["sync_version"],
            "synced_at": row["synced_at"],
        }


@dataclass(slots=True)
class _RuntimeConnectionResolution:
    connection: dict[str, Any]
    auth_audit_payload: dict[str, Any] | None = None
