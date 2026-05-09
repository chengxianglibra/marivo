"""Registration functions for MCP datasource CRUD and browse/preview tools."""

from __future__ import annotations

import json
from typing import Any

from app.transports.mcp.tools._async_bridge import call_runtime


def register_datasource_tools(server: Any, runtime: Any) -> None:
    svc = runtime.get_service("datasource")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @server.tool()  # type: ignore
    async def list_datasources() -> dict[str, Any]:
        """List registered datasources via GET /datasources."""
        return await call_runtime(svc.list_datasources)

    @server.tool()  # type: ignore
    async def create_datasource(
        datasource_type: str,
        display_name: str,
        connection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one datasource via POST /datasources using the canonical datasource_type, display_name, and connection fields."""
        kwargs: dict[str, Any] = {
            "datasource_type": datasource_type,
            "display_name": display_name,
            "connection": connection or {},
        }
        return await call_runtime(svc.register_datasource, **kwargs)

    @server.tool()  # type: ignore
    async def get_datasource(datasource_id: str) -> dict[str, Any]:
        """Read one datasource via GET /datasources/{datasource_id}."""
        return await call_runtime(svc.get_datasource, datasource_id=datasource_id)

    @server.tool()  # type: ignore
    async def update_datasource(
        datasource_id: str,
        display_name: str | None = None,
        connection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update one datasource via PUT /datasources/{datasource_id}."""
        kwargs: dict[str, Any] = {"datasource_id": datasource_id}
        if display_name is not None:
            kwargs["display_name"] = display_name
        if connection is not None:
            kwargs["connection"] = connection
        return await call_runtime(svc.update_datasource, **kwargs)

    @server.tool()  # type: ignore
    async def delete_datasource(datasource_id: str) -> dict[str, Any]:
        """Delete one datasource via DELETE /datasources/{datasource_id}."""
        return await call_runtime(svc.delete_datasource, datasource_id=datasource_id)

    # ------------------------------------------------------------------
    # Browse / Preview
    # ------------------------------------------------------------------

    @server.tool()  # type: ignore
    async def browse_schemas(
        datasource_id: str,
        catalog: str | None = None,
    ) -> dict[str, Any]:
        """Browse schemas via GET /datasources/{datasource_id}/browse/schemas."""
        return await call_runtime(svc.browse_catalog_schemas, datasource_id=datasource_id)

    @server.tool()  # type: ignore
    async def browse_tables(
        datasource_id: str,
        catalog: str | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Browse tables via GET /datasources/{datasource_id}/browse/tables."""
        if schema_name is None:
            return {
                "data": None,
                "error": {
                    "code": "VALIDATION",
                    "message": "schema_name is required for browse_tables",
                },
            }
        return await call_runtime(
            svc.browse_catalog_tables,
            datasource_id=datasource_id,
            schema_name=schema_name,
        )

    @server.tool()  # type: ignore
    async def browse_columns(
        datasource_id: str,
        schema_name: str,
        table_name: str,
    ) -> dict[str, Any]:
        """Browse live table columns via GET /datasources/{datasource_id}/browse/columns."""
        return await call_runtime(
            svc.browse_catalog_columns,
            datasource_id=datasource_id,
            schema_name=schema_name,
            table_name=table_name,
        )

    @server.tool()  # type: ignore
    async def preview_table(
        datasource_id: str,
        schema: str,
        table: str,
        limit: int = 100,
        columns: str | None = None,
        filters: str | None = None,
    ) -> dict[str, Any]:
        """Preview sample rows from a table via GET /datasources/{datasource_id}/catalog/preview.

        Args:
            datasource_id: Registered datasource identifier
            schema: Schema name containing the table
            table: Table name to preview
            limit: Max rows (default 100, max 1000)
            columns: Comma-separated column names (optional)
            filters: JSON object or array of {column,value} equality filters (as string)
        """
        col_list: list[str] | None = None
        if columns is not None:
            col_list = [c.strip() for c in columns.split(",") if c.strip()] or None

        filters_dict: dict[str, Any] | None = None
        if filters is not None:
            try:
                parsed = json.loads(filters)
                if isinstance(parsed, dict):
                    filters_dict = parsed
            except (json.JSONDecodeError, TypeError):
                pass

        kwargs: dict[str, Any] = {
            "datasource_id": datasource_id,
            "schema_name": schema,
            "table_name": table,
            "limit": limit,
        }
        if col_list is not None:
            kwargs["columns"] = col_list
        if filters_dict is not None:
            kwargs["filters"] = filters_dict

        return await call_runtime(svc.preview_table, **kwargs)
