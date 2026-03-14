"""Trino catalog adapter — reads schema/table/column metadata via information_schema."""

from __future__ import annotations

from typing import Any

from app.adapters.base import CatalogAdapter, CatalogCapabilities, PhysicalObject


class TrinoCatalogAdapter(CatalogAdapter):
    """Catalog adapter for Trino using information_schema queries."""

    def __init__(
        self,
        host: str,
        port: int = 8080,
        user: str = "omnidb",
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

        # Filter out Trino reserved headers to avoid conflicts with
        # parameters (client_tags, source) that the client sets internally.
        _RESERVED_PREFIXES = ("x-trino-",)
        safe_headers: dict[str, str] | None = None
        if self._http_headers:
            safe_headers = {
                k: v for k, v in self._http_headers.items()
                if not k.lower().startswith(_RESERVED_PREFIXES)
            } or None

        kwargs: dict[str, Any] = dict(
            host=self._host,
            port=self._port,
            user=self._user,
            http_scheme=self._http_scheme,
            catalog=self._catalog,
            schema=self._schema,
            request_timeout=self._request_timeout,
        )
        if self._password is not None:
            from trino.auth import BasicAuthentication
            kwargs["auth"] = BasicAuthentication(self._user, self._password)
        if self._client_tags is not None:
            kwargs["client_tags"] = self._client_tags
        if self._source is not None:
            kwargs["source"] = self._source
        if safe_headers is not None:
            kwargs["http_headers"] = safe_headers
        return connect(**kwargs)

    def _query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            columns = [col[0] for col in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        finally:
            conn.close()

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
                native_name=list(r.values())[0],
                native_id=None,
                object_type="catalog",
                parent_path=None,
            )
            for r in rows
        ]

    def list_schemas(self, catalog_name: str | None = None) -> list[PhysicalObject]:
        if catalog_name is None:
            # Aggregate schemas from all visible catalogs
            schemas: list[PhysicalObject] = []
            for cat_obj in self.list_catalogs():
                schemas.extend(self.list_schemas(cat_obj.native_name))
            return schemas
        cat = catalog_name
        rows = self._query(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE catalog_name = ? ORDER BY schema_name",
            [cat],
        )
        return [
            PhysicalObject(
                native_name=r["schema_name"],
                native_id=None,
                object_type="schema",
                parent_path=cat,
            )
            for r in rows
            if r["schema_name"] != "information_schema"
        ]

    def list_tables(self, schema_name: str) -> list[PhysicalObject]:
        rows = self._query(
            "SELECT t.table_name, t.table_type, COUNT(c.column_name) AS column_count "
            "FROM information_schema.tables t "
            "LEFT JOIN information_schema.columns c "
            "  ON c.table_catalog = t.table_catalog "
            " AND c.table_schema  = t.table_schema "
            " AND c.table_name    = t.table_name "
            "WHERE t.table_catalog = ? AND t.table_schema = ? "
            "GROUP BY t.table_name, t.table_type "
            "ORDER BY t.table_name",
            [self._catalog, schema_name],
        )
        return [
            PhysicalObject(
                native_name=r["table_name"],
                native_id=None,
                object_type="table",
                parent_path=schema_name,
                properties={
                    "table_type": r.get("table_type", ""),
                    "column_count": r.get("column_count", 0),
                },
            )
            for r in rows
        ]

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
        columns = [
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "position": r["ordinal_position"],
                "nullable": r["is_nullable"] == "YES",
            }
            for r in col_rows
        ]
        return PhysicalObject(
            native_name=table_name,
            native_id=None,
            object_type="table",
            parent_path=schema_name,
            properties={
                "columns": columns,
                "column_count": len(columns),
                "table_type": table_rows[0].get("table_type", ""),
            },
        )

    def list_columns(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        rows = self._query(
            "SELECT column_name, data_type, ordinal_position, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_catalog = ? AND table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position",
            [self._catalog, schema_name, table_name],
        )
        return [
            PhysicalObject(
                native_name=r["column_name"],
                native_id=None,
                object_type="column",
                parent_path=f"{schema_name}.{table_name}",
                properties={
                    "data_type": r["data_type"],
                    "nullable": r["is_nullable"] == "YES",
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
