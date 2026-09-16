"""MySQL read-only InnoDB scalar method execution using unbuffered driver cursors."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.scalar_sql_execution import Cursor, ScalarExecutionAdapter
from marivo.datasource.timezone import DatasourceEngineTimezone, probe_engine_timezone

if TYPE_CHECKING:
    from ibis.backends.mysql import Backend


class MySQLExecutionAdapter(ScalarExecutionAdapter):
    engine = "mysql"

    def __init__(self, backend: Backend, *, run_ref: str | None = None) -> None:
        super().__init__(backend, run_ref=run_ref)
        self._mysql = backend

    def _lower(self, expression: ir.Expr) -> ir.Expr:
        def rewrite(
            node: ops.Node, results: dict[ops.Node, ops.Node], **kwargs: object
        ) -> ops.Node:
            value = node.copy(**kwargs)
            if isinstance(value, ops.DateTruncate) and value.unit.name == "WEEK":
                date = value.arg.to_expr()
                return (date - date.day_of_week.index().as_interval("D")).op()
            if isinstance(value, ops.Sum) and value.dtype.is_floating():
                # Native MySQL SUM may serialize overflow as zero or saturate a cast.
                # Adding floating zero preserves finite sums and forces native overflow.
                return ops.Add(value, ibis.literal(0.0).op())
            if isinstance(value, ops.IsNan):
                # Stored NaN is unavailable in this engine's admitted scalar inputs.
                return ibis.literal(False).op()
            if isinstance(value, ops.IsInf):
                return (value.arg.to_expr().abs() > 1.7976931348623157e308).op()
            return value

        return expression.op().map(rewrite)[expression.op()].to_expr()

    def cursor(self, *, stream: bool) -> Cursor:
        from importlib import import_module

        self._check()
        cursor: Cursor = self._mysql.con.cursor(import_module("MySQLdb.cursors").SSCursor)
        return cursor

    def validate_result(self) -> None:
        warnings = self._mysql.con.warning_count()
        if warnings:
            raise self.error(
                "a complete MySQL result without conversion warnings",
                f"MySQL reported {warnings} statement warnings",
                "Inspect source values and numeric precision; correct conversion or overflow before retrying.",
                stage="output_validation",
            )

    def get_schema(
        self,
        name: str,
        *,
        database: str | None = None,
        catalog: str | None = None,
        record: Callable[[str, str], None] | None = None,
    ) -> ibis.Schema:
        if catalog is not None:
            raise self.unsupported("MySQL catalog qualification")
        namespace = database or str(
            self.read_scalar(
                self.statement("SELECT DATABASE()", role="source_schema"), record=record
            )
        )
        engine = self.read_scalar(
            self.statement(
                "SELECT ENGINE FROM information_schema.tables WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                parameters=(namespace, name),
                role="source_schema",
            ),
            record=record,
        )
        if engine != "InnoDB":
            raise self.unsupported(
                f"MySQL table engine {engine!r}; scalar execution requires InnoDB"
            )
        query = (
            "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLLATION_NAME FROM information_schema.columns "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION"
        )
        if record:
            record("source_schema", query)
        rows = self.submit(self.statement(query, parameters=(namespace, name)))
        fields: dict[str, dt.DataType] = {}
        date_checks: list[str] = []
        while (row := rows.fetchone()) is not None:
            column, kind, nullable, collation = row
            if not isinstance(column, str) or not isinstance(kind, str):
                raise self.unsupported("malformed MySQL column metadata")
            if self._declared_columns is not None and column not in self._declared_columns:
                continue
            if "unsigned" in kind.lower() or (
                collation is not None and collation != "utf8mb4_0900_bin"
            ):
                raise self.unsupported(
                    f"MySQL column {column!r} requires signed types and utf8mb4_0900_bin text"
                )
            datatype = self._mysql.compiler.type_mapper.from_string(
                kind, nullable=nullable == "YES"
            )
            if not (
                datatype.is_signed_integer()
                or datatype.is_floating()
                or datatype.is_string()
                or datatype.is_date()
                or (
                    isinstance(datatype, dt.Decimal)
                    and datatype.precision is not None
                    and datatype.scale is not None
                    and 0 <= datatype.scale <= datatype.precision <= 38
                )
            ):
                raise self.unsupported(f"MySQL column {column!r} has unsupported type {kind!r}")
            fields[column] = datatype
            if datatype.is_date():
                quoted = "`" + column.replace("`", "``") + "`"
                date_checks.append(
                    f"({quoted} IS NOT NULL AND (YEAR({quoted}) < 1 OR MONTH({quoted}) < 1 "
                    f"OR DAY({quoted}) < 1 OR LAST_DAY({quoted}) IS NULL "
                    f"OR DAY({quoted}) > DAY(LAST_DAY({quoted}))))"
                )
        if not fields:
            raise self.unsupported("missing MySQL relation")
        if date_checks:
            qualified = ".".join("`" + part.replace("`", "``") + "`" for part in (namespace, name))
            validation = f"SELECT count(*) FROM {qualified} WHERE " + " OR ".join(date_checks)
            violations = self.read_scalar(
                self.statement(validation, role="engine_check.mysql_dates"), record=record
            )
            if violations != 0:
                raise self.error(
                    "valid nonzero Gregorian DATE values or SQL NULL",
                    f"{violations} rows contain invalid MySQL dates",
                    "Correct zero or invalid dates before executing again.",
                    stage="output_validation",
                )
        return ibis.schema(fields)

    def timezone(self) -> DatasourceEngineTimezone:
        return probe_engine_timezone(self._mysql)

    def interrupt(self) -> None:
        # Closing this exact connection is best-effort cancellation, not server termination proof.
        from contextlib import ExitStack

        try:
            with ExitStack() as stack:
                for stream in tuple(self._streams):
                    stack.callback(stream.close)
                self._mysql.disconnect()
        finally:
            self._closed = True


def bind_mysql(
    candidate: object, *, reserve: Callable[[str], None], run_ref: str
) -> MySQLExecutionAdapter:
    from ibis.backends.mysql import Backend

    if not isinstance(candidate, Backend):
        raise MaterializationError(
            expected="an Ibis MySQL backend",
            received=type(candidate).__name__,
            repair="Resolve the declared MySQL datasource.",
            stage="execution_boundary",
            run_ref=run_ref,
        )
    return MySQLExecutionAdapter(candidate, run_ref=run_ref)


def admit_dataset(dataset: LogicalDataset) -> None:
    from marivo.analysis.operators.mysql_support import unsupported_reason

    reason = unsupported_reason(dataset)
    if reason is not None:
        raise MaterializationError(
            expected="an individually qualified MySQL scalar method closure",
            received=reason,
            repair="Use qualified scalar methods, native civil-date axes and declared InnoDB sources.",
            stage="implementation_registration",
        )
