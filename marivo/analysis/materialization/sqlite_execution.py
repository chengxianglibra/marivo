"""SQLite read-only scalar method execution with physical storage-class validation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import sqlglot
from sqlglot import expressions as sge

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.scalar_sql_execution import Cursor, ScalarExecutionAdapter
from marivo.datasource.timezone import DatasourceEngineTimezone, probe_engine_timezone

if TYPE_CHECKING:
    from ibis.backends.sqlite import Backend


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class SQLiteExecutionAdapter(ScalarExecutionAdapter):
    engine = "sqlite"

    def __init__(self, backend: Backend, *, run_ref: str | None = None) -> None:
        super().__init__(backend, run_ref=run_ref)
        self._sqlite = backend

    def _lower(self, expression: ir.Expr) -> ir.Expr:
        def rewrite(
            node: ops.Node, results: dict[ops.Node, ops.Node], **kwargs: object
        ) -> ops.Node:
            value = node.copy(**kwargs)
            if isinstance(value, ops.IsNan):
                # Stored NaN is unavailable in this engine's admitted scalar inputs.
                return ibis.literal(False).op()
            if isinstance(value, ops.IsInf):
                return (value.arg.to_expr().abs() == float("inf")).op()
            return value

        return expression.op().map(rewrite)[expression.op()].to_expr()

    def cursor(self, *, stream: bool) -> Cursor:
        self._check()
        cursor: Cursor = self._sqlite.con.cursor()
        return cursor

    def initialize(self) -> None:
        super().initialize()
        cursor = self._sqlite.con.execute("PRAGMA query_only = ON")
        cursor.close()

    def get_schema(
        self,
        name: str,
        *,
        database: str | None = None,
        catalog: str | None = None,
        record: Callable[[str, str], None] | None = None,
    ) -> ibis.Schema:
        if catalog is not None or database not in (None, "main"):
            raise self.unsupported("SQLite source outside the declared main database")
        definition = self.read_scalar(
            self.statement(
                "SELECT sql FROM main.sqlite_schema WHERE type='table' AND name=?",
                parameters=(name,),
                role="source_schema",
            ),
            record=record,
        )
        if not isinstance(definition, str):
            raise self.unsupported("missing ordinary SQLite table definition")
        parsed_definition = sqlglot.parse_one(definition, read="sqlite")
        if not isinstance(parsed_definition, sge.Create) or parsed_definition.find(
            sge.VirtualProperty
        ):
            raise self.unsupported("non-ordinary SQLite table")
        for column_definition in parsed_definition.find_all(sge.ColumnDef):
            if (
                self._declared_columns is not None
                and column_definition.name not in self._declared_columns
            ):
                continue
            for collation in column_definition.find_all(sge.CollateColumnConstraint):
                if collation.this.name.upper() != "BINARY":
                    raise self.unsupported(
                        f"non-binary SQLite collation on {column_definition.name!r}"
                    )
        query = "SELECT name,type FROM pragma_table_info(?) ORDER BY cid"
        if record:
            record("source_schema", query)
        rows = self.submit(self.statement(query, parameters=(name,)))
        fields: dict[str, dt.DataType] = {}
        invalid: list[str] = []
        while (row := rows.fetchone()) is not None:
            column, declaration = row
            if not isinstance(column, str) or not isinstance(declaration, str):
                raise self.unsupported("malformed SQLite column metadata")
            if self._declared_columns is not None and column not in self._declared_columns:
                continue
            kind = declaration.upper()
            allowed = {
                "INTEGER": (dt.int64, "'integer'"),
                "INT": (dt.int64, "'integer'"),
                "BIGINT": (dt.int64, "'integer'"),
                "REAL": (dt.float64, "'real','integer'"),
                "DOUBLE": (dt.float64, "'real','integer'"),
                "TEXT": (dt.string, "'text'"),
                "DATE": (dt.date, "'text'"),
            }
            if kind not in allowed:
                raise self.unsupported(
                    f"SQLite column {column!r} has unsupported type {declaration!r}"
                )
            datatype, storage = allowed[kind]
            fields[column] = datatype
            quoted = _quote(column)
            predicate = f"typeof({quoted}) NOT IN ('null',{storage})"
            if kind == "DATE":
                predicate += (
                    f" OR ({quoted} IS NOT NULL AND (length({quoted}) != 10 "
                    f"OR substr({quoted}, 1, 4) < '0001' OR substr({quoted}, 1, 4) > '9999' OR date({quoted}, '+0 days') IS NULL OR date({quoted}, '+0 days') != {quoted}))"
                )
            invalid.append(f"({predicate})")
        if not fields:
            raise self.unsupported("missing SQLite relation")
        validation = f"SELECT count(*) FROM {_quote(name)} WHERE " + " OR ".join(invalid)
        violations = self.read_scalar(
            self.statement(validation, role="engine_check.sqlite_storage"), record=record
        )
        if violations != 0:
            raise self.error(
                "declared SQLite storage classes and canonical valid ISO dates",
                f"{violations} rows violate the physical source contract",
                "Correct mixed storage classes or invalid dates before executing again.",
                stage="output_validation",
            )
        return ibis.schema(fields)

    def timezone(self) -> DatasourceEngineTimezone:
        return probe_engine_timezone(self._sqlite)

    def interrupt(self) -> None:
        self._sqlite.con.interrupt()


def bind_sqlite(
    candidate: object, *, reserve: Callable[[str], None], run_ref: str
) -> SQLiteExecutionAdapter:
    from ibis.backends.sqlite import Backend

    if not isinstance(candidate, Backend):
        raise MaterializationError(
            expected="an Ibis SQLite backend",
            received=type(candidate).__name__,
            repair="Resolve the declared SQLite datasource.",
            stage="execution_boundary",
            run_ref=run_ref,
        )
    return SQLiteExecutionAdapter(candidate, run_ref=run_ref)


def admit_dataset(dataset: LogicalDataset) -> None:
    from marivo.analysis.operators.sqlite_support import unsupported_reason

    if (reason := unsupported_reason(dataset)) is not None:
        raise MaterializationError(
            expected="an exact qualified SQLite scalar method closure",
            received=reason,
            repair="Use qualified scalar methods with declared SQLite INTEGER, REAL, TEXT or DATE columns.",
            stage="implementation_registration",
        )
