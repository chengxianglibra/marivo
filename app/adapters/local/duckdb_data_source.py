from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from app.contracts.errors import ErrorCode, NotFoundError, ValidationError
from app.contracts.values import ColumnInfo, LogicalQuery, QueryResult, SourceRef, SourceSchema


class DuckDBDataSource:
    """DuckDB-backed DataSource for local embedded mode.

    Phase 4 bridge: execute() accepts CompiledQuery objects and raw SQL
    strings directly (not formal LogicalQuery only). The DataSource Protocol
    stays typed as LogicalQuery; DuckDBDataSource satisfies it structurally
    via duck typing.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = path
        self._con: duckdb.DuckDBPyConnection | None = None

    @property
    def _connection(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            self._con = duckdb.connect(str(self._path) if self._path else ":memory:")
        return self._con

    def execute(self, query: Any) -> QueryResult:
        """Execute a query against DuckDB.

        Phase 4 bridge: accepts LogicalQuery, CompiledQuery, or raw SQL string.
        """
        try:
            if isinstance(query, str):
                sql = query
                params: list[Any] = []
            elif isinstance(query, LogicalQuery):
                sql = query.sql
                params = list(query.params.values()) if query.params else []
            else:
                # CompiledQuery bridge: extract sql + params from dataclass
                sql = query.sql
                params = list(query.params) if query.params else []

            result = (
                self._connection.execute(sql, params) if params else self._connection.execute(sql)
            )
        except duckdb.ParserException as e:
            raise ValidationError(
                code=ErrorCode.VALIDATION,
                message=f"Query could not be parsed: {e}",
            ) from e
        except duckdb.CatalogException as e:
            raise NotFoundError(
                code=ErrorCode.NOT_FOUND,
                message=f"Catalog object not found: {e}",
            ) from e

        columns = [d[0] for d in result.description] if result.description else []
        rows = [
            dict(zip(columns, row, strict=True)) if columns else {} for row in result.fetchall()
        ]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            query_sql=sql if isinstance(query, str) else None,
        )

    def schema(self, source_ref: SourceRef) -> SourceSchema:
        """Query DuckDB catalog for table schema."""
        con = self._connection
        try:
            cols_result = con.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? "
                "ORDER BY ordinal_position",
                [source_ref.schema_name, source_ref.table_name],
            )
            rows = cols_result.fetchall()
            if not rows:
                raise NotFoundError(
                    code=ErrorCode.NOT_FOUND,
                    message=f"Table '{source_ref.schema_name}.{source_ref.table_name}' not found in data source",
                )
            columns = [
                ColumnInfo(
                    name=row[0],
                    dtype=row[1],
                    nullable=row[2] == "YES",
                )
                for row in rows
            ]
        except NotFoundError:
            raise
        except duckdb.CatalogException as e:
            raise NotFoundError(
                code=ErrorCode.NOT_FOUND,
                message=f"Table '{source_ref.schema_name}.{source_ref.table_name}' not found in data source",
            ) from e

        return SourceSchema(columns=columns)

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None
