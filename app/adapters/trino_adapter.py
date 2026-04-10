"""Trino catalog adapter — reads schema/table/column metadata via information_schema."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.adapters.base import CatalogAdapter, CatalogCapabilities, PhysicalObject


class TrinoCatalogAdapter(CatalogAdapter):
    """Catalog adapter for Trino using information_schema queries."""

    def __init__(
        self,
        host: str,
        port: int = 8080,
        user: str = "factum",
        password: str | None = None,
        http_scheme: str = "http",
        catalog: str = "hive",
        schema: str = "default",
        client_tags: list[str] | None = None,
        source: str | None = None,
        http_headers: dict[str, str] | None = None,
        request_timeout: float = 30.0,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._http_scheme = http_scheme
        self._catalog = catalog
        self._schema = schema
        self._client_tags = client_tags
        self._source = source
        self._http_headers = http_headers
        self._request_timeout = request_timeout

    def _connect(self) -> Any:
        from trino.dbapi import connect

        connect_fn: Callable[..., Any] = connect
        # Filter out Trino reserved headers to avoid conflicts with
        # parameters (client_tags, source) that the client sets internally.
        _reserved_prefixes = ("x-trino-",)
        safe_headers: dict[str, str] | None = None
        if self._http_headers:
            safe_headers = {
                k: v
                for k, v in self._http_headers.items()
                if not k.lower().startswith(_reserved_prefixes)
            } or None

        kwargs: dict[str, Any] = {
            "host": self._host,
            "port": self._port,
            "user": self._user,
            "http_scheme": self._http_scheme,
            "catalog": self._catalog,
            "schema": self._schema,
            "request_timeout": self._request_timeout,
        }
        if self._password is not None:
            from trino.auth import BasicAuthentication

            kwargs["auth"] = BasicAuthentication(self._user, self._password)
        if self._client_tags is not None:
            kwargs["client_tags"] = self._client_tags
        if self._source is not None:
            kwargs["source"] = self._source
        if safe_headers is not None:
            kwargs["http_headers"] = safe_headers
        return connect_fn(**kwargs)

    def _query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            columns = [col[0] for col in cur.description]
            return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]
        finally:
            conn.close()

    def _quote_identifier(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def source_type(self) -> str:
        return "trino"

    def capabilities(self) -> CatalogCapabilities:
        return CatalogCapabilities(
            supports_schemas=True,
            supports_column_stats=True,
            supports_partitions=False,
            supports_lineage=False,
            supports_tags=False,
            supports_access_control=False,
            supports_column_comments=True,
            supports_table_properties=True,
        )

    def test_connection(self) -> bool:
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
            finally:
                conn.close()
            return True
        except Exception:
            return False

    def list_catalogs(self) -> list[PhysicalObject]:
        """Return all catalogs visible to this Trino connection."""
        rows = self._query("SHOW CATALOGS")
        return [
            PhysicalObject(
                native_name=next(iter(r.values())),
                native_id=None,
                object_type="catalog",
                parent_path=None,
            )
            for r in rows
        ]

    def list_schemas(self, catalog_name: str | None = None) -> list[PhysicalObject]:
        if catalog_name is None:
            # Aggregate schemas from all visible catalogs
            from contextlib import suppress

            schemas: list[PhysicalObject] = []
            for cat_obj in self.list_catalogs():
                with suppress(Exception):
                    # Some catalogs (e.g., jmx, system) may not support SHOW SCHEMAS
                    schemas.extend(self.list_schemas(cat_obj.native_name))
            return schemas
        # Use SHOW SCHEMAS FROM <catalog> instead of information_schema
        # because presto-gateway may not properly populate information_schema.schemata
        rows = self._query(f"SHOW SCHEMAS FROM {catalog_name}")
        return [
            PhysicalObject(
                native_name=r["Schema"],
                native_id=None,
                object_type="schema",
                parent_path=catalog_name,
            )
            for r in rows
            if r["Schema"] != "information_schema"
        ]

    def list_tables(self, schema_name: str) -> list[PhysicalObject]:
        table_rows = self._query(f"SHOW TABLES FROM {self._quote_identifier(schema_name)}")
        if not table_rows:
            return []

        column_rows = self._query(
            "SELECT table_name, COUNT(column_name) AS column_count "
            "FROM information_schema.columns "
            "WHERE table_catalog = ? AND table_schema = ? "
            "GROUP BY table_name",
            [self._catalog, schema_name],
        )
        column_counts = {
            str(row["table_name"]): int(row.get("column_count", 0) or 0) for row in column_rows
        }
        return [
            PhysicalObject(
                native_name=str(r["Table"]),
                native_id=None,
                object_type="table",
                parent_path=schema_name,
                properties={
                    "table_type": "BASE TABLE",
                    "column_count": column_counts.get(str(r["Table"]), 0),
                },
            )
            for r in table_rows
        ]

    def _get_column_comments(self, schema_name: str, table_name: str) -> dict[str, str]:
        """Retrieve column comments using SHOW COLUMNS.

        Returns a dict mapping column_name -> comment (empty string if no comment).
        """
        try:
            rows = self._query(f'SHOW COLUMNS FROM "{schema_name}"."{table_name}"')
            # SHOW COLUMNS returns: Column, Type, Extra, Comment
            return {r["Column"]: r.get("Comment", "") or "" for r in rows}
        except Exception:
            # If SHOW COLUMNS fails (e.g., permission issue), return empty
            return {}

    def _get_table_properties(self, schema_name: str, table_name: str) -> dict[str, Any]:
        """Retrieve table properties (e.g., Iceberg table$properties).

        Returns a dict of key-value pairs, or empty dict if unavailable.
        """
        try:
            rows = self._query(f'SELECT key, value FROM "{schema_name}"."{table_name}$properties"')
            return {r["key"]: r["value"] for r in rows}
        except Exception:
            # If query fails (non-Iceberg table, permission, etc.), return empty
            return {}

    def get_table_detail(self, schema_name: str, table_name: str) -> PhysicalObject:
        # Verify table exists
        table_rows = self._query(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_catalog = ? AND table_schema = ? AND table_name = ?",
            [self._catalog, schema_name, table_name],
        )
        if not table_rows:
            raise KeyError(f"Table not found: {schema_name}.{table_name}")

        col_rows = self._query(
            "SELECT column_name, data_type, ordinal_position, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_catalog = ? AND table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position",
            [self._catalog, schema_name, table_name],
        )

        # Fetch column comments and table properties
        comments = self._get_column_comments(schema_name, table_name)
        table_props = self._get_table_properties(schema_name, table_name)

        columns = [
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "position": r["ordinal_position"],
                "nullable": r["is_nullable"] == "YES",
                "comment": comments.get(r["column_name"], ""),
            }
            for r in col_rows
        ]

        properties: dict[str, Any] = {
            "columns": columns,
            "column_count": len(columns),
            "table_type": table_rows[0].get("table_type", ""),
        }

        # Add table properties if available
        if table_props:
            properties["table_properties"] = table_props
            # Extract commonly-used properties as top-level for convenience
            if "comment" in table_props:
                properties["comment"] = table_props["comment"]
            if "owner" in table_props:
                properties["owner"] = table_props["owner"]

        return PhysicalObject(
            native_name=table_name,
            native_id=None,
            object_type="table",
            parent_path=schema_name,
            properties=properties,
        )

    def list_columns(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        rows = self._query(
            "SELECT column_name, data_type, ordinal_position, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_catalog = ? AND table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position",
            [self._catalog, schema_name, table_name],
        )

        # Fetch column comments
        comments = self._get_column_comments(schema_name, table_name)

        return [
            PhysicalObject(
                native_name=r["column_name"],
                native_id=None,
                object_type="column",
                parent_path=f"{schema_name}.{table_name}",
                properties={
                    "data_type": r["data_type"],
                    "nullable": r["is_nullable"] == "YES",
                    "comment": comments.get(r["column_name"], ""),
                },
            )
            for r in rows
        ]

    def get_table_stats(self, schema_name: str, table_name: str) -> dict[str, Any]:
        """Return column-level stats via SHOW STATS."""
        rows = self._query(f'SHOW STATS FOR "{schema_name}"."{table_name}"')
        stats: dict[str, Any] = {"columns": {}}
        for r in rows:
            col_name = r.get("column_name")
            if col_name is None:
                # Summary row with row_count
                stats["row_count"] = r.get("row_count")
            else:
                stats["columns"][col_name] = {
                    "data_size": r.get("data_size"),
                    "distinct_count": r.get("distinct_values_count"),
                    "nulls_fraction": r.get("nulls_fraction"),
                    "row_count": r.get("row_count"),
                    "low_value": r.get("low_value"),
                    "high_value": r.get("high_value"),
                }
        return stats
