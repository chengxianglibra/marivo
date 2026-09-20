"""Read-only Trino execution with caller-owned Trino cursor lifetimes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import ExitStack
from datetime import datetime
from math import isfinite
from typing import TYPE_CHECKING, Protocol

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
from marivo.analysis.materialization.scalar_sql_execution import ScalarExecutionAdapter
from marivo.analysis.operators.trino_support import supported_type
from marivo.datasource.engines.trino import _trino_namespace
from marivo.datasource.timezone import DatasourceEngineTimezone

if TYPE_CHECKING:
    from ibis.backends.trino import Backend


class _NativeCursor(Protocol):
    def execute(self, operation: str, params: tuple[Parameter, ...] = ()) -> object: ...
    def fetchmany(self, size: int) -> Sequence[Sequence[object]]: ...
    def close(self) -> None: ...


class TrinoCursor:
    """Register before submit, including while the driver waits for its first page."""

    def __init__(self, adapter: TrinoExecutionAdapter, native: _NativeCursor) -> None:
        self._adapter = adapter
        self._native = native
        self._closed = False

    def _check(self) -> None:
        self._adapter._check()
        if self._closed:
            raise self._adapter.error(
                "an open owned Trino cursor",
                "cursor is closed",
                "Create a new cursor in an open execution context.",
            )

    def execute(self, query: str, parameters: tuple[Parameter, ...] = ()) -> object:
        self._check()
        result = (
            self._native.execute(query, parameters) if parameters else self._native.execute(query)
        )
        self._check()
        return result

    def fetchmany(self, size: int) -> Sequence[tuple[object, ...]]:
        self._check()
        rows = [tuple(row) for row in self._native.fetchmany(size)]
        self._check()
        if any(isinstance(value, float) and not isfinite(value) for row in rows for value in row):
            raise self._adapter.error(
                "finite Trino scalar results",
                "non-finite floating result",
                "Correct non-finite values or overflowing aggregates before retrying.",
                stage="output_validation",
            )
        return rows

    def close(self) -> None:
        if self._closed:
            return
        # Keep failed closes owned so final cleanup can retry cancellation.
        self._native.close()
        self._closed = True
        self._adapter._cursors.discard(self)


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class TrinoExecutionAdapter(ScalarExecutionAdapter):
    engine = "trino"

    def __init__(self, backend: Backend, *, run_ref: str | None = None) -> None:
        super().__init__(backend, run_ref=run_ref)
        self._trino = backend
        self._cursors: set[TrinoCursor] = set()

    def cursor(self, *, stream: bool) -> TrinoCursor:
        self._check()
        result = TrinoCursor(self, self._trino.con.cursor())
        self._cursors.add(result)
        return result

    def _lower(self, expression: ir.Expr) -> ir.Expr:
        from marivo.analysis.materialization.temporal_sql import lower_temporal

        expression = lower_temporal(expression, self.engine)

        def qualify(
            node: ops.Node, results: dict[ops.Node, ops.Node], **kwargs: object
        ) -> ops.Node:
            if isinstance(node, ops.UnboundTable):
                default_catalog: str = node.namespace.catalog or self._trino.con.catalog
                catalog, database = _trino_namespace(
                    node.namespace.database,
                    catalog=default_catalog,
                    default_schema=self._trino.con.schema,
                )
                kwargs["namespace"] = ops.Namespace(catalog=catalog, database=database)
            value = node.copy(**kwargs)
            if isinstance(value, ops.Literal) and isinstance(value.dtype, dt.Timestamp):
                # Ibis' FROM_ISO8601_TIMESTAMP path truncates to milliseconds.
                # A typed string cast preserves the exact native precision.
                assert isinstance(value.value, datetime)
                return ops.Cast(ibis.literal(value.value.isoformat(sep=" ")).op(), value.dtype)
            if (
                isinstance(value, ops.Cast)
                and isinstance(value.to, dt.Timestamp)
                and isinstance(value.arg.dtype, dt.Timestamp)
                and value.to.timezone == value.arg.dtype.timezone
                and (value.to.scale is None or value.to.scale == value.arg.dtype.scale)
            ):
                return value.arg
            return value

        return expression.op().map(qualify)[expression.op()].to_expr()

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
        default_catalog: str | None = catalog or self._trino.con.catalog
        default_schema: str | None = self._trino.con.schema
        if not isinstance(default_catalog, str):
            raise self.unsupported("Trino requires an explicit catalog and schema")
        catalog, database = _trino_namespace(
            database, catalog=default_catalog, default_schema=default_schema
        )
        if database is None:
            raise self.unsupported("Trino requires an explicit catalog and schema")
        # The connector and relation form are observation receipts; admission
        # depends only on the `$` guard and the relation's column metadata.
        self.read_scalar(
            self.statement(
                "SELECT connector_name FROM system.metadata.catalogs WHERE catalog_name = ?",
                parameters=(catalog,),
                role="source_schema",
            ),
        )
        if "$" in name:
            raise self.unsupported(
                "an ordinary Trino relation; $-suffixed internal tables like "
                f"{name!r} (for example $partitions, $files, $snapshots or $properties) "
                "carry metadata shapes, not column sets; "
                "request the underlying ordinary relation or view instead."
            )
        qualified = ".".join(map(_identifier, (catalog, database, name)))
        self.read_scalar(
            self.statement(
                f"SELECT table_type FROM {_identifier(catalog)}.information_schema.tables "
                "WHERE table_schema = ? AND table_name = ?",
                parameters=(database, name),
                role="source_schema",
            ),
        )
        query = f"SHOW COLUMNS FROM {qualified}"
        rows = self.submit(self.statement(query, role="source_schema"))
        fields: dict[str, dt.DataType] = {}
        finite_checks: list[str] = []
        while (row := rows.fetchone()) is not None:
            column, kind = row[:2]
            if not isinstance(column, str) or not isinstance(kind, str):
                raise self.unsupported("malformed Trino column metadata")
            if columns is not None and column not in columns:
                continue
            with source_type_errors(dependency, column, kind):
                datatype = self._trino.compiler.type_mapper.from_string(kind)
            if datatype.is_string():
                datatype = dt.string
            if kind.lower().startswith("char(") or not supported_type(str(datatype)):
                raise unsupported_source_type(dependency, column, kind)
            fields[column] = datatype
            if datatype.is_floating():
                finite_checks.append(f"NOT is_finite({_identifier(column)})")
        if finite_checks:
            violations = self.read_scalar(
                self.statement(
                    f"SELECT count(*) FROM {qualified} WHERE " + " OR ".join(finite_checks),
                    role="engine_check.trino_finite",
                ),
            )
            if violations != 0:
                raise self.error(
                    "finite declared Trino floating values or SQL NULL",
                    f"{violations} rows contain non-finite values",
                    "Correct NaN or infinity values before retrying.",
                    stage="output_validation",
                )
        return ibis.schema(fields)

    def timezone(self) -> DatasourceEngineTimezone:
        from marivo.datasource.engines import require_profile_for_backend_type
        from marivo.datasource.timezone import resolve_engine_timezone

        return resolve_engine_timezone(
            require_profile_for_backend_type("trino").timezone_probe_sql,
            lambda query: self.read_scalar(self.statement(query, role="source_timezone")),
        )

    def disconnect(self) -> None:
        if self._closed:
            return
        try:
            with ExitStack() as stack:
                stack.callback(self._trino.disconnect)
                for cursor in tuple(self._cursors):
                    stack.callback(cursor.close)
                for stream in tuple(self._streams):
                    stack.callback(stream.close)
        finally:
            self._closed = True

    def interrupt(self) -> None:
        self.disconnect()


def bind_trino(
    candidate: object, *, reserve: Callable[[str], None], run_ref: str
) -> TrinoExecutionAdapter:
    from ibis.backends.trino import Backend

    if not isinstance(candidate, Backend):
        raise MaterializationError(
            expected="an Ibis Trino backend",
            received=type(candidate).__name__,
            repair="Resolve the declared Trino datasource.",
            stage="execution_boundary",
            run_ref=run_ref,
        )
    return TrinoExecutionAdapter(candidate, run_ref=run_ref)


def admit_dataset(dataset: LogicalDataset) -> None:
    from marivo.analysis.operators.trino_support import unsupported_reason

    reason = unsupported_reason(dataset)
    if reason is not None:
        raise MaterializationError(
            expected="a qualified Trino scalar closure",
            received=reason,
            repair="Use qualified scalar methods over declared Trino relations and native civil dates.",
            stage="implementation_registration",
        )
