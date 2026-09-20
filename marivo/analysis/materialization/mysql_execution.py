"""MySQL read-only InnoDB scalar method execution using unbuffered driver cursors."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.compiler.source_dependencies import EntitySourceDependency
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization.errors import (
    MaterializationError,
    source_type_errors,
    unsupported_source_type,
)
from marivo.analysis.materialization.execution import Parameter
from marivo.analysis.materialization.scalar_sql_execution import (
    Cursor,
    ScalarExecutionAdapter,
    ScalarStatement,
)
from marivo.datasource.timezone import DatasourceEngineTimezone

if TYPE_CHECKING:
    from ibis.backends.mysql import Backend


class MySQLExecutionAdapter(ScalarExecutionAdapter):
    engine = "mysql"

    def __init__(self, backend: Backend, *, run_ref: str | None = None) -> None:
        super().__init__(backend, run_ref=run_ref)
        self._mysql = backend
        self._div_precision_increment: int | None = None

    def _div_precision(self) -> int:
        """Read the server's decimal division scale increment once per adapter.

        The exact composed decimal scale contract (mean ``s+4``, division
        ``s1+4``) holds only when the increment equals 4; a different server
        value makes the declared publication scales wrong.
        """
        if self._div_precision_increment is None:
            received = self.read_scalar(
                self.statement(
                    "SELECT @@div_precision_increment",
                    role="engine_check.mysql_div_precision",
                )
            )
            if not isinstance(received, int):
                raise self.error(
                    "an integer MySQL div_precision_increment",
                    f"non-integer server value {received!r}",
                    "Verify the MySQL server version; the scalar decimal contract assumes MySQL 8.",
                    stage="output_validation",
                )
            self._div_precision_increment = received
        return self._div_precision_increment

    def require_div_precision_increment(self) -> None:
        """Refuse decimal mean/div results when the server increment is not 4."""
        value = self._div_precision()
        if value == 4:
            return
        raise self.error(
            "MySQL div_precision_increment=4 for exact decimal mean and division scales",
            f"the server reports div_precision_increment={value}",
            (
                "Set the server (or session) variable to 4, for example "
                "SET SESSION div_precision_increment=4, or use a backend whose decimal "
                "scale contract is public without this setting."
            ),
            stage="output_validation",
        )

    def _prepare(
        self,
        expression: ir.Expr,
        *,
        role: str,
        params: Mapping[str, Parameter] | None = None,
        execute: bool = False,
    ) -> ScalarStatement:
        if any(
            isinstance(node, ops.Divide)
            and (node.left.dtype.is_decimal() or node.right.dtype.is_decimal())
            for node in expression.op().find(ops.Divide)
        ):
            self.require_div_precision_increment()
        return super()._prepare(expression, role=role, params=params, execute=execute)

    def _lower(self, expression: ir.Expr) -> ir.Expr:
        from marivo.analysis.materialization.temporal_sql import lower_temporal

        expression = lower_temporal(expression, self.engine)

        def rewrite(
            node: ops.Node, results: dict[ops.Node, ops.Node], **kwargs: object
        ) -> ops.Node:
            value = node.copy(**kwargs)
            if (
                isinstance(value, ops.Cast)
                and value.to.is_unsigned_integer()
                and value.to.copy(nullable=True) == value.arg.dtype.copy(nullable=True)
            ):
                # MySQL CAST only accepts UNSIGNED (64-bit), not narrower unsigned names.
                return value.arg
            if (
                isinstance(value, ops.Cast)
                and isinstance(value.to, dt.Timestamp)
                and isinstance(value.arg.dtype, dt.Timestamp)
                and value.to.timezone == value.arg.dtype.timezone
                and (value.to.scale is None or value.to.scale == value.arg.dtype.scale)
            ):
                return value.arg
            if isinstance(value, ops.TimestampTruncate):
                return ops.Cast(value, dt.Timestamp(scale=6))
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
        dependency: EntitySourceDependency | None = None,
    ) -> ibis.Schema:
        if dependency is not None:
            dependency.validate_request(name, database, catalog)
        columns = None if dependency is None else dependency.physical_columns
        if catalog is not None:
            raise self.unsupported("MySQL catalog qualification")
        namespace = database or str(
            self.read_scalar(self.statement("SELECT DATABASE()", role="source_schema"))
        )
        engine = self.read_scalar(
            self.statement(
                "SELECT ENGINE FROM information_schema.tables WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                parameters=(namespace, name),
                role="source_schema",
            ),
        )
        if engine != "InnoDB":
            raise self.unsupported(
                f"MySQL table engine {engine!r}; scalar execution requires InnoDB"
            )
        query = (
            "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLLATION_NAME FROM information_schema.columns "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION"
        )
        rows = self.submit(self.statement(query, parameters=(namespace, name)))
        fields: dict[str, dt.DataType] = {}
        date_checks: list[str] = []
        while (row := rows.fetchone()) is not None:
            column, kind, nullable, collation = row
            if not isinstance(column, str) or not isinstance(kind, str):
                raise self.unsupported("malformed MySQL column metadata")
            if columns is not None and column not in columns:
                continue
            base_kind = kind.lower().split("(", 1)[0].split()[0]
            if base_kind in {"char", "enum", "set", "bit"}:
                raise unsupported_source_type(dependency, column, kind)
            if collation is not None and collation != "utf8mb4_0900_bin":
                raise self.unsupported(f"MySQL column {column!r} requires utf8mb4_0900_bin text")
            with source_type_errors(dependency, column, kind):
                datatype = self._mysql.compiler.type_mapper.from_string(
                    kind, nullable=nullable == "YES"
                )
            declared_boolean = dependency is not None and any(
                item.physical == column and item.declared_type == "boolean"
                for item in dependency.columns
            )
            quoted = "`" + column.replace("`", "``") + "`"
            if declared_boolean and re.fullmatch(r"tinyint\(1\)", kind.lower()):
                datatype = dt.boolean.copy(nullable=nullable == "YES")
                date_checks.append(f"({quoted} IS NOT NULL AND {quoted} NOT IN (0,1))")
            if isinstance(datatype, dt.Timestamp):
                if datatype.scale is None:
                    datatype = datatype.copy(scale=0)
                if datatype.scale is not None and not 0 <= datatype.scale <= 6:
                    raise unsupported_source_type(dependency, column, kind)
                if base_kind == "timestamp":
                    zone = self.read_scalar(
                        self.statement("SELECT @@session.time_zone", role="source_schema"),
                    )
                    if zone not in {"UTC", "+00:00"}:
                        raise self.unsupported("MySQL TIMESTAMP requires a verified UTC session")
                    declared = (
                        next(
                            (
                                item.declared_type
                                for item in dependency.columns
                                if item.physical == column
                            ),
                            None,
                        )
                        if dependency is not None
                        else None
                    )
                    aware = (
                        declared is not None
                        and isinstance(dt.dtype(declared), dt.Timestamp)
                        and dt.dtype(declared).timezone is not None
                    )
                    datatype = datatype.copy(timezone="UTC" if aware else None)
                date_checks.append(
                    f"({quoted} IS NOT NULL AND (YEAR({quoted}) < 1 OR MONTH({quoted}) < 1 "
                    f"OR DAY({quoted}) < 1 OR LAST_DAY({quoted}) IS NULL "
                    f"OR DAY({quoted}) > DAY(LAST_DAY({quoted}))))"
                )
            if not (
                datatype.is_integer()
                or datatype.is_boolean()
                or datatype.is_timestamp()
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
                raise unsupported_source_type(dependency, column, kind)
            fields[column] = (
                dt.string.copy(nullable=nullable == "YES") if datatype.is_string() else datatype
            )
            if datatype.is_date():
                quoted = "`" + column.replace("`", "``") + "`"
                date_checks.append(
                    f"({quoted} IS NOT NULL AND (YEAR({quoted}) < 1 OR MONTH({quoted}) < 1 "
                    f"OR DAY({quoted}) < 1 OR LAST_DAY({quoted}) IS NULL "
                    f"OR DAY({quoted}) > DAY(LAST_DAY({quoted}))))"
                )
        if not fields and dependency is None:
            raise self.unsupported("missing MySQL relation")
        if date_checks:
            qualified = ".".join("`" + part.replace("`", "``") + "`" for part in (namespace, name))
            validation = f"SELECT count(*) FROM {qualified} WHERE " + " OR ".join(date_checks)
            violations = self.read_scalar(
                self.statement(validation, role="engine_check.mysql_dates")
            )
            if violations != 0:
                raise self.error(
                    "valid Gregorian dates/timestamps, Boolean 0/1, or SQL NULL",
                    f"{violations} rows violate the MySQL scalar storage contract",
                    "Correct invalid dates/timestamps or Boolean storage before executing again.",
                    stage="output_validation",
                )
        return ibis.schema(fields)

    def timezone(self) -> DatasourceEngineTimezone:
        from marivo.datasource.engines import require_profile_for_backend_type
        from marivo.datasource.timezone import resolve_engine_timezone

        return resolve_engine_timezone(
            require_profile_for_backend_type("mysql").timezone_probe_sql,
            lambda query: self.read_scalar(self.statement(query, role="source_timezone")),
        )

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
