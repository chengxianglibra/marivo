from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from app.adapters.base import MAX_PREVIEW_ROWS, CatalogAdapter, PreviewFilters
from app.contracts.ids import UserId
from app.identity import resolve_user
from app.registry.common import now_iso
from app.registry.factories import build_catalog_adapter, validate_datasource_type
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore

logger = logging.getLogger("marivo.datasource_auth")


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


def _json_ready_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


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
    ) -> dict[str, Any]:
        validate_datasource_type(datasource_type)

        owner_user = UserId(resolve_user() or "local")

        datasource_id = f"ds_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO datasources (
                datasource_id,
                datasource_type,
                display_name,
                connection_json,
                owner_user,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [
                datasource_id,
                datasource_type,
                display_name,
                json.dumps(connection),
                owner_user,
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
        self._check_ownership(row)
        return self._row_to_datasource(row)

    def list_datasources(self) -> list[dict[str, Any]]:
        user = resolve_user()
        if user is not None:
            rows = self.metadata.query_rows(
                "SELECT * FROM datasources WHERE owner_user = ? ORDER BY created_at", [user]
            )
        else:
            rows = self.metadata.query_rows("SELECT * FROM datasources ORDER BY created_at")
        return [self._row_to_datasource(row) for row in rows]

    def ensure_datasource(
        self,
        datasource_type: str,
        display_name: str,
        connection: dict[str, Any],
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
            )

        now = now_iso()
        self.metadata.execute(
            """
            UPDATE datasources
            SET datasource_type = ?, connection_json = ?, updated_at = ?
            WHERE datasource_id = ?
            """,
            [
                datasource_type,
                json.dumps(connection),
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
        self.metadata.execute("DELETE FROM datasources WHERE datasource_id = ?", [datasource_id])

    def _check_ownership(self, row: dict[str, Any]) -> None:
        user = resolve_user()
        if user is None:
            return
        row_owner = row.get("owner_user")
        if row_owner is not None and user != row_owner:
            raise KeyError(f"Datasource not found: {row['datasource_id']}")

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
            "rows": [
                {key: _json_ready_scalar(value) for key, value in row.items()}
                for row in result.rows
            ],
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

        if datasource_type != "trino":
            return _RuntimeConnectionResolution(connection=connection)

        if connection.get("user"):
            return _RuntimeConnectionResolution(connection=connection)

        raise ValueError("session_user_missing: trino datasource requires user in connection")

    # =========================================================================
    # Row conversion
    # =========================================================================

    def _row_to_datasource(self, row: dict[str, Any]) -> dict[str, Any]:
        datasource_type = str(row["datasource_type"])
        raw_connection = _loads_stored_json(row["connection_json"])
        connection = raw_connection if isinstance(raw_connection, dict) else {}

        datasource: dict[str, Any] = {
            "datasource_id": row["datasource_id"],
            "datasource_type": datasource_type,
            "display_name": row["display_name"],
            "connection": connection,
            "owner_user": row.get("owner_user"),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        validation = self.evaluate_datasource(datasource)
        datasource["readiness_status"] = validation.readiness_status
        datasource["failure_code"] = validation.failure_code
        return datasource


@dataclass(slots=True)
class _RuntimeConnectionResolution:
    connection: dict[str, Any]
    auth_audit_payload: dict[str, Any] | None = None
