"""SQLite read-only scalar method execution with physical storage-class validation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import TYPE_CHECKING

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import sqlglot
from sqlglot import expressions as sge

from marivo.analysis.compiler.source_dependencies import EntitySourceDependency
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization.errors import (
    MaterializationError,
    unsupported_source_type,
)
from marivo.analysis.materialization.scalar_sql_execution import Cursor, ScalarExecutionAdapter
from marivo.datasource.engines.sqlite import declared_scalar_type
from marivo.datasource.timezone import DatasourceEngineTimezone

if TYPE_CHECKING:
    from ibis.backends.sqlite import Backend


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _literal_moment(value: object) -> datetime:
    """Normalize one date or timestamp literal to its exact civil datetime."""
    if isinstance(value, datetime):
        return value
    assert isinstance(value, date)
    return datetime.combine(value, datetime.min.time())


class SQLiteExecutionAdapter(ScalarExecutionAdapter):
    engine = "sqlite"

    def __init__(self, backend: Backend, *, run_ref: str | None = None) -> None:
        super().__init__(backend, run_ref=run_ref)
        self._sqlite = backend
        from marivo.analysis.materialization.temporal_sql import _sqlite_shift

        self._marivo_shift = _sqlite_shift

    def _lower(self, expression: ir.Expr) -> ir.Expr:
        from marivo.analysis.materialization.temporal_sql import lower_temporal

        expression = lower_temporal(expression, self.engine)

        def rewrite(
            node: ops.Node, _results: dict[ops.Node, ops.Node] | None = None, **kwargs: object
        ) -> ops.Node:
            value = node.copy(**kwargs)
            if (
                isinstance(value, ops.Cast)
                and isinstance(value.to, dt.Timestamp)
                and isinstance(value.arg.dtype, (dt.Timestamp, dt.Date))
                and value.to.timezone == getattr(value.arg.dtype, "timezone", None)
                and (
                    value.to.scale is None
                    or value.to.scale == getattr(value.arg.dtype, "scale", None)
                )
            ) and not isinstance(value.arg.dtype, dt.Date):
                return value.arg
            if (
                isinstance(value, ops.Cast)
                and isinstance(value.to, dt.Timestamp)
                and isinstance(value.arg.dtype, dt.Date)
            ):
                # ibis renders a civil-date cast through
                # STRFTIME('%Y-%m-%d %H:%M:%f', ..), whose %f is three digits;
                # the transport contract needs the canonical six-digit text,
                # which only the registered deterministic shift scalar renders.
                return self._marivo_shift(value.arg.to_expr().cast("timestamp").op(), 0)
            if (
                isinstance(value, ops.Cast)
                and isinstance(value.to, dt.Timestamp)
                and isinstance(value.arg, ops.Literal)
                and isinstance(value.arg.value, (datetime, date))
            ):
                # Same three-digit strftime gap for a temporal literal bound.
                moment = _literal_moment(value.arg.value)
                return self._marivo_shift(
                    ibis.literal(moment.isoformat(sep=" ", timespec="microseconds"))
                    .cast("timestamp")
                    .op(),
                    0,
                )
            if isinstance(value, ops.Literal) and isinstance(value.dtype, dt.Timestamp):
                # A bare timestamp literal renders without the canonical
                # six-digit fraction, and SQLite MIN/MAX compare the texts
                # lexically, so the unshifted bound would leak into a cell.
                return self._marivo_shift(value.copy(**kwargs), 0)
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
        self.submit(self.statement("PRAGMA query_only = ON", role="source_setting"))
        from marivo.analysis.materialization.temporal_sql import (
            sqlite_localize,
            sqlite_render,
            sqlite_shift,
            sqlite_strptime,
            sqlite_truncate,
        )

        for name, function in (
            ("_marivo_localize", sqlite_localize),
            ("_marivo_render", sqlite_render),
            ("_marivo_truncate", sqlite_truncate),
            ("_marivo_shift", sqlite_shift),
            ("_marivo_strptime", sqlite_strptime),
        ):
            self._sqlite.con.create_function(name, 2, function, deterministic=True)

    def get_schema(
        self,
        name: str,
        *,
        database: str | None = None,
        catalog: str | None = None,
        dependency: EntitySourceDependency | None = None,
    ) -> ibis.Schema:
        if dependency is not None:
            dependency.validate_request(name, database, catalog)
        columns = None if dependency is None else dependency.physical_columns
        if catalog is not None or database not in (None, "main"):
            raise self.unsupported("SQLite source outside the declared main database")
        definition = self.read_scalar(
            self.statement(
                "SELECT sql FROM main.sqlite_schema WHERE type IN ('table','view') AND name=?",
                parameters=(name,),
                role="source_schema",
            ),
        )
        if not isinstance(definition, str):
            raise self.unsupported("missing ordinary SQLite table or view definition")
        parsed_definition = sqlglot.parse_one(definition, read="sqlite")
        if not isinstance(parsed_definition, sge.Create) or parsed_definition.find(
            sge.VirtualProperty
        ):
            raise self.unsupported("non-ordinary SQLite table")
        for column_definition in parsed_definition.find_all(sge.ColumnDef):
            if columns is not None and column_definition.name not in columns:
                continue
            for collation in column_definition.find_all(sge.CollateColumnConstraint):
                if collation.this.name.upper() != "BINARY":
                    raise self.unsupported(
                        f"non-binary SQLite collation on {column_definition.name!r}"
                    )
        query = "SELECT name,type FROM pragma_table_info(?) ORDER BY cid"
        rows = self.submit(self.statement(query, parameters=(name,), role="source_schema"))
        fields: dict[str, dt.DataType] = {}
        invalid: list[str] = []
        while (row := rows.fetchone()) is not None:
            column, declaration = row
            if not isinstance(column, str) or not isinstance(declaration, str):
                raise self.unsupported("malformed SQLite column metadata")
            if columns is not None and column not in columns:
                continue
            datatype = declared_scalar_type(declaration)
            if datatype is None:
                raise unsupported_source_type(dependency, column, declaration)
            storage = (
                "'integer'"
                if datatype.is_integer() or datatype.is_boolean()
                else "'real','integer'"
                if datatype.is_floating()
                else "'text'"
            )
            fields[column] = datatype
            quoted = _quote(column)
            predicate = f"typeof({quoted}) NOT IN ('null',{storage})"
            if datatype.is_date():
                predicate += (
                    f" OR ({quoted} IS NOT NULL AND (length({quoted}) != 10 "
                    f"OR substr({quoted}, 1, 4) < '0001' OR substr({quoted}, 1, 4) > '9999' OR date({quoted}, '+0 days') IS NULL OR date({quoted}, '+0 days') != {quoted}))"
                )
            if datatype.is_boolean():
                predicate += f" OR ({quoted} IS NOT NULL AND {quoted} NOT IN (0,1))"
            if datatype.is_timestamp():
                digits = "[0-9]"
                pattern = f"{digits * 4}-{digits * 2}-{digits * 2} {digits * 2}:{digits * 2}:{digits * 2}.{digits * 6}"
                day = f"substr({quoted},1,10)"
                predicate += (
                    f" OR ({quoted} IS NOT NULL AND ({quoted} NOT GLOB '{pattern}'"
                    f" OR substr({quoted},1,4) < '0001'"
                    f" OR date({day},'+0 days') IS NULL OR date({day},'+0 days') != {day}"
                    f" OR substr({quoted},12,2) > '23' OR substr({quoted},15,2) > '59'"
                    f" OR substr({quoted},18,2) > '59'))"
                )
            invalid.append(f"({predicate})")
        if not fields:
            raise self.unsupported("missing SQLite relation")
        validation = f"SELECT count(*) FROM {_quote(name)} WHERE " + " OR ".join(invalid)
        violations = self.read_scalar(
            self.statement(validation, role="engine_check.sqlite_storage")
        )
        if violations != 0:
            raise self.error(
                "declared SQLite storage classes, Boolean 0/1 and canonical valid civil dates/timestamps",
                f"{violations} rows violate the physical source contract",
                "Correct mixed storage, Boolean values or dates/timestamps before executing again.",
                stage="output_validation",
            )
        return ibis.schema(fields)

    def timezone(self) -> DatasourceEngineTimezone:
        from marivo.datasource.engines import require_profile_for_backend_type
        from marivo.datasource.timezone import resolve_engine_timezone

        return resolve_engine_timezone(
            require_profile_for_backend_type("sqlite").timezone_probe_sql,
            lambda query: self.read_scalar(self.statement(query, role="source_timezone")),
        )

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
            repair="Use qualified scalar methods with qualified SQLite scalar declarations and validated storage.",
            stage="implementation_registration",
        )
