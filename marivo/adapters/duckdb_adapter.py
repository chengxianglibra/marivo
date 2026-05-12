from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from marivo.adapters.base import (
    MAX_PREVIEW_ROWS,
    CatalogAdapter,
    CatalogCapabilities,
    PhysicalObject,
    PreviewFilters,
    PreviewResult,
)


class DuckDBCatalogAdapter(CatalogAdapter):
    """Catalog adapter that reads schema/table/column metadata from a DuckDB file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def source_type(self) -> str:
        return "duckdb"

    def capabilities(self) -> CatalogCapabilities:
        return CatalogCapabilities(
            supports_schemas=True,
            supports_column_stats=False,
            supports_partitions=False,
            supports_lineage=False,
            supports_tags=False,
            supports_access_control=False,
            supports_table_preview=True,
        )

    def test_connection(self) -> bool:
        try:
            con = self._connect()
            con.close()
            return True
        except Exception:
            return False

    def _connect(self) -> Any:
        """Open a read-only DuckDB connection. Defers duckdb import to method level."""
        return import_module("duckdb").connect(str(self._path), read_only=True)

    def _quote_identifier(self, identifier: str) -> str:
        """Quote and escape a DuckDB identifier.

        Double quotes inside identifiers are escaped by doubling them.
        """
        return '"' + identifier.replace('"', '""') + '"'

    def list_schemas(self, catalog_name: str | None = None) -> list[PhysicalObject]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT DISTINCT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('information_schema', 'pg_catalog') "
                "ORDER BY schema_name"
            ).fetchall()
            return [
                PhysicalObject(
                    native_name=row[0],
                    native_id=None,
                    object_type="schema",
                    parent_path=catalog_name or "local",
                )
                for row in rows
            ]
        finally:
            con.close()

    def list_tables(self, schema_name: str) -> list[PhysicalObject]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = ? ORDER BY table_name",
                [schema_name],
            ).fetchall()
            results = []
            for row in rows:
                _cr = con.execute(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = ? AND table_name = ?",
                    [schema_name, row[0]],
                ).fetchone()
                col_count = _cr[0] if _cr else 0
                results.append(
                    PhysicalObject(
                        native_name=row[0],
                        native_id=None,
                        object_type="table",
                        parent_path=schema_name,
                        properties={"column_count": col_count},
                    )
                )
            return results
        finally:
            con.close()

    def get_table_detail(self, schema_name: str, table_name: str) -> PhysicalObject:
        con = self._connect()
        try:
            _er = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = ? AND table_name = ?",
                [schema_name, table_name],
            ).fetchone()
            exists = _er[0] if _er else 0
            if not exists:
                raise KeyError(f"Table {schema_name}.{table_name} not found")
            cols = con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
                [schema_name, table_name],
            ).fetchall()
            columns = [{"name": c[0], "type": c[1]} for c in cols]
            return PhysicalObject(
                native_name=table_name,
                native_id=None,
                object_type="table",
                parent_path=schema_name,
                properties={
                    "columns": columns,
                    "column_count": len(columns),
                },
            )
        finally:
            con.close()

    def list_columns(self, schema_name: str, table_name: str) -> list[PhysicalObject]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
                [schema_name, table_name],
            ).fetchall()
            return [
                PhysicalObject(
                    native_name=row[0],
                    native_id=None,
                    object_type="column",
                    parent_path=f"{schema_name}.{table_name}",
                    properties={"data_type": row[1]},
                )
                for row in rows
            ]
        finally:
            con.close()

    def preview_table(
        self,
        schema_name: str,
        table_name: str,
        limit: int = 100,
        columns: list[str] | None = None,
        filters: PreviewFilters | None = None,
    ) -> PreviewResult:
        """Preview sample rows from a DuckDB table."""
        effective_limit = min(max(1, limit), MAX_PREVIEW_ROWS)

        con = self._connect()
        try:
            # 1. Verify table exists
            exists_row = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = ? AND table_name = ?",
                [schema_name, table_name],
            ).fetchone()
            if not exists_row or exists_row[0] == 0:
                raise KeyError(f"Table {schema_name}.{table_name} not found")

            # 2. Get column metadata for type info
            col_rows = con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
                [schema_name, table_name],
            ).fetchall()
            all_columns = {row[0]: row[1] for row in col_rows}

            # 3. Validate column selection if provided
            if columns is not None:
                invalid = [c for c in columns if c not in all_columns]
                if invalid:
                    raise ValueError(f"Unknown columns: {invalid}")
                selected_columns = columns
            else:
                selected_columns = list(all_columns.keys())

            filters = filters or {}
            invalid_filters = [name for name in filters if name not in all_columns]
            if invalid_filters:
                raise ValueError(f"Unknown filter columns: {invalid_filters}")

            # 4. Build safe SELECT query with properly escaped identifiers
            quoted_cols = ", ".join(self._quote_identifier(c) for c in selected_columns)
            quoted_schema = self._quote_identifier(schema_name)
            quoted_table = self._quote_identifier(table_name)
            where_clause = ""
            params: list[object] = []
            if filters:
                predicates = []
                for column, value in filters.items():
                    predicates.append(f"{self._quote_identifier(column)} IS NOT DISTINCT FROM ?")
                    params.append(value)
                where_clause = " WHERE " + " AND ".join(predicates)
            # Fetch limit+1 rows to accurately detect truncation
            sql = (
                f"SELECT {quoted_cols} FROM {quoted_schema}.{quoted_table}"
                f"{where_clause} LIMIT {effective_limit + 1}"
            )

            # 5. Execute query
            rows = con.execute(sql, params).fetchall()

            # 6. Determine truncation and trim to actual limit
            truncated = len(rows) > effective_limit
            if truncated:
                rows = rows[:effective_limit]

            # 7. Build result
            return PreviewResult(
                columns=[{"name": c, "type": all_columns[c]} for c in selected_columns],
                rows=[dict(zip(selected_columns, row, strict=True)) for row in rows],
                row_count=len(rows),
                truncated=truncated,
            )
        finally:
            con.close()
