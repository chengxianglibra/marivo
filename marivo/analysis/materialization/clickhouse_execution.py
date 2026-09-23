"""Read-only ClickHouse execution with owned Native streams."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from datetime import timedelta
from itertools import islice
from math import isfinite
from typing import TYPE_CHECKING, Protocol

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler.nodes import CompiledDataset
from marivo.analysis.compiler.source_dependencies import EntitySourceDependency
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.domains.completeness import (
    EventCoverageProvider,
    EventCoverageResolution,
    resolve_event_coverage,
)
from marivo.analysis.domains.contracts import EventDefinition
from marivo.analysis.domains.lifecycle import LifecycleSemantics
from marivo.analysis.materialization.errors import (
    MaterializationError,
    source_type_errors,
    unsupported_source_type,
)
from marivo.analysis.materialization.event_bundle import EventBundleStream
from marivo.analysis.materialization.execution import BatchStream, Parameter, Statement
from marivo.analysis.materialization.lifecycle_bundle import LifecycleBundle
from marivo.analysis.materialization.scalar_sql_execution import (
    ScalarExecutionAdapter,
    ScalarStatement,
)
from marivo.analysis.operators.clickhouse_support import supported_type
from marivo.datasource.timezone import DatasourceEngineTimezone

if TYPE_CHECKING:
    from ibis.backends.clickhouse import Backend


class NativeStream(Protocol):
    def __enter__(self) -> Iterator[Sequence[object]]: ...
    def __exit__(self, *args: object) -> object: ...


class ClickHouseCursor:
    """Own the stream, including a response returned after interruption."""

    def __init__(self, adapter: ClickHouseExecutionAdapter) -> None:
        self._adapter = adapter
        self._native: NativeStream | None = None
        self._rows: Iterator[Sequence[object]] = iter(())
        self._closed = False

    def _check(self) -> None:
        self._adapter._check()
        if self._closed:
            raise self._adapter.error(
                "an open ClickHouse cursor",
                "cursor is closed",
                "Create a cursor in an open execution context.",
            )

    def execute(self, query: str, parameters: tuple[Parameter, ...] = ()) -> object:
        self._check()
        native: NativeStream = self._adapter._clickhouse.con.query_rows_stream(
            query, parameters=parameters or None
        )
        self._native = native
        # Submission may have been interrupted before returning its response.
        if self._closed or self._adapter._closed:
            native.__exit__(None, None, None)
            self._native = None
            self._adapter._cursors.discard(self)
            self._check()
        self._rows = native.__enter__()
        return None

    def fetchmany(self, size: int) -> Sequence[tuple[object, ...]]:
        self._check()
        rows = [tuple(row) for row in islice(self._rows, size)]
        self._check()
        if any(isinstance(value, float) and not isfinite(value) for row in rows for value in row):
            raise self._adapter.error(
                "finite ClickHouse scalar results",
                "non-finite floating result",
                "Correct non-finite values or overflowing aggregates before retrying.",
                stage="output_validation",
            )
        return rows

    def close(self) -> None:
        if self._closed and self._native is None:
            return
        if self._native is not None:
            self._native.__exit__(None, None, None)
            self._native = None
        self._closed = True
        self._adapter._cursors.discard(self)


def _identifier(value: str) -> str:
    return "`" + value.replace("\\", "\\\\").replace("`", "\\`") + "`"


class ClickHouseExecutionAdapter(ScalarExecutionAdapter):
    engine = "clickhouse"

    def __init__(self, backend: Backend, *, run_ref: str | None = None) -> None:
        super().__init__(backend, run_ref=run_ref)
        self._clickhouse = backend
        self._cursors: set[ClickHouseCursor] = set()
        self._event_bundle: EventBundleStream | None = None
        self._event_primary: ops.Node | None = None
        self._source_engines: set[str] = set()
        self.lifecycle_bundle: LifecycleBundle | None = None
        self._lifecycle_compiling = False

    def open_event_bundle(
        self, recipe: CompiledDataset, *, step_keys: tuple[str, ...]
    ) -> tuple[tuple[str, int], ...]:
        from marivo.analysis.materialization.clickhouse_event_sql import compile_event_bundle

        self._check()
        if self._event_bundle is not None:
            raise self.unsupported("Event bundle already submitted")
        if self._source_engines != {"MergeTree"}:
            raise self.unsupported("Event snapshots require qualified MergeTree tables")
        shared = self.read_scalar(
            self.statement(
                "SELECT getSetting('enable_shared_storage_snapshot_in_query')",
                role="engine_check.event_snapshot",
            )
        )
        if shared != 1:
            raise self.unsupported(
                "Event assertions require enable_shared_storage_snapshot_in_query=1"
            )
        materialized = self.read_scalar(
            self.statement(
                "SELECT getSetting('enable_materialized_cte') SETTINGS enable_materialized_cte=1",
                role="engine_check.event_cte",
            )
        )
        if materialized != 1:
            raise self.error(
                "ClickHouse Event reader with enable_materialized_cte=1",
                "materialized CTE execution is disabled",
                "Ask the datasource administrator to enable materialized CTEs in the read-only reader profile.",
                stage="implementation_registration",
            )
        bundle = compile_event_bundle(recipe, step_keys=step_keys)
        stream = EventBundleStream(self, bundle)
        self._streams.add(stream)
        self._event_bundle = stream
        self._event_primary = recipe.expression.op()
        return stream.validations

    def open_lifecycle_bundle(
        self, recipe: CompiledDataset, semantics: LifecycleSemantics
    ) -> tuple[tuple[str, int], ...]:
        self._check()
        if self.lifecycle_bundle is not None or self._source_engines != {"MergeTree"}:
            raise self.unsupported("Lifecycle requires an unopened qualified MergeTree source")
        if (
            self.read_scalar(
                self.statement(
                    "SELECT getSetting('enable_shared_storage_snapshot_in_query')",
                    role="engine_check.lifecycle_snapshot",
                )
            )
            != 1
        ):
            raise self.unsupported("Lifecycle requires shared storage snapshots within a query")
        self._lifecycle_compiling = True
        self.lifecycle_bundle = LifecycleBundle(self, recipe, semantics)
        return self.lifecycle_bundle.validations

    def _prepare(
        self,
        expression: ir.Expr,
        *,
        role: str,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        execute: bool = False,
    ) -> ScalarStatement:
        if not self._lifecycle_compiling:
            return super()._prepare(expression, role=role, params=params, execute=execute)
        from marivo.analysis.compiler.event_time import _localize_utc

        self._check()
        localize = type(_localize_utc("UTC", ibis.timestamp("2000-01-01")).op())
        if any(
            not isinstance(node, localize)
            for node in expression.op().find((ops.InMemoryTable, ops.ScalarUDF, ops.AggUDF))
        ):
            raise self.unsupported("Lifecycle uploads or ungoverned UDFs")
        return ScalarStatement(
            self._compile_sql(expression, params=params),
            (),
            expression.as_table().schema().to_pyarrow(),
            role,
            self._context,
            native_structs=True,
        )

    def _compile_sql(
        self, expression: ir.Expr, *, params: Mapping[ir.Scalar, Parameter] | None = None
    ) -> str:
        if self._lifecycle_compiling:
            from marivo.analysis.materialization.clickhouse_event_sql import (
                compile_event_expression,
            )

            if params is not None:
                raise self.unsupported("parameterized Lifecycle relation")
            return compile_event_expression(expression)
        return super()._compile_sql(expression, params=params)

    def event_bundle_proof(self) -> pa.Table:
        self._check()
        if self._event_bundle is None:
            raise self.unsupported("Event bundle was not submitted")
        return self._event_bundle.proof

    def batches(
        self,
        value: Statement | ir.Expr,
        *,
        chunk_size: int,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
    ) -> BatchStream:
        if (
            isinstance(value, ir.Table)
            and self.lifecycle_bundle is not None
            and self.lifecycle_bundle.certifies(value)
        ):
            return self.lifecycle_bundle.stream(value)
        if (
            isinstance(value, ir.Expr)
            and self._event_bundle is not None
            and value.op() is self._event_primary
            and role == "primary"
            and params is None
        ):
            return self._event_bundle
        return super().batches(value, chunk_size=chunk_size, params=params, role=role)

    def resolve_coverage(
        self,
        definition: EventDefinition,
        *,
        provider: EventCoverageProvider | None,
        source_binding_fingerprint: str,
        execution_domain_id: str,
        require_source_origin: bool,
    ) -> EventCoverageResolution:
        if provider is not None:
            raise self.unsupported("ClickHouse Event coverage provider")
        return resolve_event_coverage(
            definition,
            source_binding_fingerprint=source_binding_fingerprint,
            execution_domain_id=execution_domain_id,
            require_source_origin=require_source_origin,
        )

    def cursor(self, *, stream: bool) -> ClickHouseCursor:
        self._check()
        result = ClickHouseCursor(self)
        self._cursors.add(result)
        return result

    def _lower(self, expression: ir.Expr) -> ir.Expr:
        if self._lifecycle_compiling:
            return expression
        from marivo.analysis.materialization.temporal_sql import lower_temporal

        expression = lower_temporal(expression, self.engine)

        def widen(
            node: ops.Node, _results: dict[ops.Node, ops.Node] | None = None, **kwargs: object
        ) -> ops.Node:
            if (
                isinstance(node, ops.Cast)
                and isinstance(node.to, dt.Timestamp)
                and node.to.scale is None
            ):
                # A generic timestamp cast must not narrow DateTime64 to DateTime.
                value = kwargs["arg"]
                assert isinstance(value, ops.Value)
                return ops.Cast(value, node.to.copy(scale=6))
            if isinstance(node, (ops.Count, ops.CountStar)):
                return ops.Cast(ops.Cast(node.copy(**kwargs), dt.Decimal(76, 0)), node.dtype)
            if isinstance(node, ops.Sum) and (
                node.arg.dtype.is_integer() or node.arg.dtype.is_decimal()
            ):
                arg = kwargs["arg"]
                assert isinstance(arg, ops.Value)
                scale = node.arg.dtype.scale if isinstance(node.arg.dtype, dt.Decimal) else 0
                wide = node.copy(arg=ops.Cast(arg, dt.Decimal(76, scale)), where=kwargs["where"])
                return ops.Cast(wide, node.dtype)
            if isinstance(node, ops.WindowFunction):
                function = kwargs["func"]
                assert isinstance(function, ops.Value)
                casts: list[dt.DataType] = []
                while isinstance(function, ops.Cast):
                    casts.append(function.to)
                    function = function.arg
                if casts:
                    assert isinstance(function, (ops.Analytic, ops.Reduction))
                    window: ops.Value = node.copy(
                        func=function, **{k: v for k, v in kwargs.items() if k != "func"}
                    )
                    for dtype in reversed(casts):
                        window = ops.Cast(window, dtype)
                    return window
            return node.copy(**kwargs)

        return expression.op().map(widen)[expression.op()].to_expr()

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
        database = database or self._clickhouse.con.database
        if not isinstance(database, str) or catalog is not None:
            raise self.unsupported("ClickHouse requires a database and table without a catalog")
        settings = self.read_scalar(
            self.statement(
                "SELECT value FROM system.settings WHERE name = 'join_use_nulls'",
                role="engine_check.clickhouse_settings",
            ),
        )
        if settings != "1":
            raise self.error(
                "effective join_use_nulls=1",
                f"join_use_nulls={settings!r}",
                "Configure the read-only ClickHouse account with join_use_nulls=1.",
                stage="source_schema",
            )
        kind = self.read_scalar(
            self.statement(
                "SELECT engine FROM system.tables WHERE database = %s AND name = %s",
                parameters=(database, name),
                role="source_schema",
            ),
        )
        if not isinstance(kind, str) or not kind:
            raise self.unsupported(
                f"an existing ClickHouse relation {database}.{name}; "
                f"no system.tables engine value {kind!r} was returned; "
                "verify the database and table names on this ClickHouse server."
            )
        self._source_engines.add(kind)
        query = f"DESCRIBE TABLE {_identifier(database)}.{_identifier(name)}"
        rows = self.submit(self.statement(query, role="source_schema"))
        fields: dict[str, dt.DataType] = {}
        finite: list[str] = []
        temporal_transport_checked = False
        while (row := rows.fetchone()) is not None:
            column, kind = row[:2]
            if not isinstance(column, str) or not isinstance(kind, str):
                raise self.unsupported("malformed ClickHouse column metadata")
            if columns is not None and column not in columns:
                continue
            physical = kind
            if physical.startswith("LowCardinality(") and physical.endswith(")"):
                physical = physical[len("LowCardinality(") : -1]
                if physical not in {"String", "Nullable(String)"}:
                    raise unsupported_source_type(dependency, column, kind)
            if physical.startswith("Nullable(") and physical.endswith(")"):
                physical = physical[len("Nullable(") : -1]
            if (
                re.fullmatch(
                    r"(?:U?Int(?:8|16|32|64)|Bool|Float(?:32|64)|String|Date|DateTime(?:\('[A-Za-z0-9_./:+-]+'\))?|DateTime64\([0-6](?:,\s*'[A-Za-z0-9_./:+-]+')?\)|Decimal\([0-9]+,\s*[0-9]+\))",
                    physical,
                )
                is None
            ):
                raise unsupported_source_type(dependency, column, kind)
            with source_type_errors(dependency, column, kind):
                datatype = self._clickhouse.compiler.type_mapper.from_string(kind).copy(
                    nullable=kind.startswith(("Nullable(", "LowCardinality(Nullable("))
                )
            if isinstance(datatype, dt.Timestamp):
                if not temporal_transport_checked:
                    from marivo.datasource.timezone import resolve_engine_timezone

                    transport = resolve_engine_timezone(
                        "SELECT timezone()",
                        lambda query: self.read_scalar(
                            self.statement(query, role="engine_check.clickhouse_timestamp_timezone")
                        ),
                    )
                    if transport.engine_timezone_tz.utcoffset(None) != timedelta(0):
                        raise self.error(
                            "a verified UTC ClickHouse timestamp execution timezone",
                            "the connection uses a non-UTC execution timezone",
                            "Configure the ClickHouse reader profile with timezone UTC; retain the actual timezone in aware source-column bindings.",
                            stage="source_schema",
                        )
                    temporal_transport_checked = True
                if datatype.timezone is None:
                    datatype = datatype.copy(timezone=self.timezone().engine_timezone_name)
                # C2's UTC-labelled civil binding remains exact; new aware bindings
                # retain the physical instant instead of relabelling its wall clock.
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
                if (
                    declared is not None
                    and isinstance(dt.dtype(declared), dt.Timestamp)
                    and dt.dtype(declared).timezone is None
                ):
                    if datatype.timezone != "UTC":
                        raise self.unsupported(
                            "a civil ClickHouse binding requires a verified UTC physical timezone"
                        )
                    datatype = datatype.copy(timezone=None)
            if not supported_type(str(datatype.copy(nullable=True))):
                raise unsupported_source_type(dependency, column, kind)
            fields[column] = datatype
            if datatype.is_floating():
                finite.append(f"NOT isFinite({_identifier(column)})")
        if finite:
            violations = self.read_scalar(
                self.statement(
                    f"SELECT count(*) FROM {_identifier(database)}.{_identifier(name)} WHERE "
                    + " OR ".join(finite),
                    role="engine_check.clickhouse_finite",
                ),
            )
            if violations != 0:
                raise self.error(
                    "finite declared ClickHouse floating values or SQL NULL",
                    f"{violations} rows contain non-finite values",
                    "Correct NaN or infinity values before retrying.",
                    stage="output_validation",
                )
        return ibis.schema(fields)

    def timezone(self) -> DatasourceEngineTimezone:
        from marivo.datasource.engines import require_profile_for_backend_type
        from marivo.datasource.timezone import resolve_engine_timezone

        return resolve_engine_timezone(
            require_profile_for_backend_type("clickhouse").timezone_probe_sql,
            lambda query: self.read_scalar(self.statement(query, role="source_timezone")),
        )

    def disconnect(self) -> None:
        if self._closed:
            return
        try:
            with ExitStack() as stack:
                stack.callback(self._clickhouse.disconnect)
                if self.lifecycle_bundle is not None:
                    stack.callback(self.lifecycle_bundle.close)
                for cursor in tuple(self._cursors):
                    stack.callback(cursor.close)
                for stream in tuple(self._streams):
                    stack.callback(stream.close)
        finally:
            self._closed = True

    def interrupt(self) -> None:
        self.disconnect()


def bind_clickhouse(
    candidate: object,
    *,
    reserve: Callable[[str], None],
    run_ref: str,
) -> ClickHouseExecutionAdapter:
    from ibis.backends.clickhouse import Backend

    if not isinstance(candidate, Backend):
        raise MaterializationError(
            expected="an Ibis ClickHouse backend",
            received=type(candidate).__name__,
            repair="Resolve the declared ClickHouse datasource.",
            stage="execution_boundary",
            run_ref=run_ref,
        )
    return ClickHouseExecutionAdapter(candidate, run_ref=run_ref)


def admit_dataset(dataset: LogicalDataset) -> None:
    from marivo.analysis.operators.registry import implementation, source_unsupported_reason

    if implementation(dataset).for_backend("clickhouse") is None:
        raise MaterializationError(
            expected="an individually qualified ClickHouse method closure",
            received=source_unsupported_reason(dataset, "clickhouse") or "unregistered method",
            repair="Use an exact admitted method shape and declared ClickHouse sources.",
            stage="implementation_registration",
        )
