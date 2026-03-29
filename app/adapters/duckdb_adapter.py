from __future__ import annotations

from pathlib import Path

import duckdb

from app.adapters.base import CatalogAdapter, CatalogCapabilities, PhysicalObject


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
        )

    def test_connection(self) -> bool:
        try:
            con = duckdb.connect(str(self._path), read_only=True)
            con.close()
            return True
        except Exception:
            return False

    def list_schemas(self, catalog_name: str | None = None) -> list[PhysicalObject]:
        con = duckdb.connect(str(self._path), read_only=True)
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
        con = duckdb.connect(str(self._path), read_only=True)
        try:
            rows = con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = ? ORDER BY table_name",
                [schema_name],
            ).fetchall()
            results = []
            for row in rows:
                col_count = con.execute(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = ? AND table_name = ?",
                    [schema_name, row[0]],
                ).fetchone()[0]
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
        con = duckdb.connect(str(self._path), read_only=True)
        try:
            exists = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = ? AND table_name = ?",
                [schema_name, table_name],
            ).fetchone()[0]
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
        con = duckdb.connect(str(self._path), read_only=True)
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
