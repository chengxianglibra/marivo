"""Read-only Trino execution with caller-owned Trino cursor lifetimes."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from datetime import datetime
from math import isfinite
from typing import TYPE_CHECKING, Protocol

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler.nodes import (
    CompiledDataset,
    CompiledRelationFence,
    CompiledValidation,
)
from marivo.analysis.compiler.source_dependencies import EntitySourceDependency
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.domains.completeness import (
    EventCoverageProvider,
    EventCoverageResolution,
    resolve_event_coverage,
)
from marivo.analysis.domains.contracts import EventDefinition
from marivo.analysis.materialization.errors import (
    MaterializationError,
    source_type_errors,
    unsupported_source_type,
)
from marivo.analysis.materialization.execution import Parameter
from marivo.analysis.materialization.scalar_sql_execution import (
    ScalarExecutionAdapter,
    ScalarStatement,
)
from marivo.analysis.operators.trino_support import supported_type
from marivo.datasource.engines.trino import _trino_namespace
from marivo.datasource.timezone import DatasourceEngineTimezone

if TYPE_CHECKING:
    from ibis.backends.trino import Backend
    from trino.client import TrinoRequest


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
        self._event_prefix: str | None = None
        self._event_request: TrinoRequest | None = None
        self._event_counts: dict[ops.Node, ops.Node] = {}
        self._source_connectors: set[str] = set()
        self._source_kinds: set[str] = set()

    def open_event_relations(self, recipe: CompiledDataset) -> tuple[tuple[str, int], ...]:
        from trino import constants

        from marivo.analysis.materialization.trino_event_sql import compile_event_expression

        self._check()
        if self._event_prefix is not None:
            raise self.unsupported("Event source already opened")
        if self._source_connectors != {"iceberg"} or self._source_kinds != {"BASE TABLE"}:
            raise self.unsupported("Event snapshots require qualified Iceberg base tables")
        self.submit(
            self.statement(
                "SET SESSION distinct_aggregations_strategy = 'single_step'",
                role="event_source.planning",
            )
        )
        request: TrinoRequest = self._trino.con._create_request()
        self._event_request = request
        control = "START TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        with self.submission("event_source.snapshot", control):
            response = request.post(control)
            status = request.process(response)
            transaction = response.headers.get(constants.HEADER_STARTED_TRANSACTION)
            if transaction:
                request.transaction_id = transaction
            while status.next_uri:
                response = request.get(status.next_uri)
                transaction = (
                    response.headers.get(constants.HEADER_STARTED_TRANSACTION) or transaction
                )
                if transaction:
                    request.transaction_id = transaction
                status = request.process(response)
            if not transaction:
                raise self.unsupported("Trino did not establish a read-only transaction")
            request.transaction_id = transaction
        preparations = recipe.preparations or recipe.validations
        ctes = [
            f"{_identifier(item.relation_name)} AS ({compile_event_expression(item.expression)})"
            for item in preparations
            if isinstance(item, CompiledRelationFence)
        ]
        self._event_prefix = "WITH " + ", ".join(ctes) + " "
        accepted: list[tuple[str, int]] = []
        for check in preparations:
            if not isinstance(check, CompiledValidation):
                continue
            self._prepare_event_counts(check.expression)
            checked = self.read_table(self.prepare(check.expression, role=check.name))
            violations = checked["violations"][0].as_py() if checked.num_rows == 1 else None
            if type(violations) is not int or violations != 0:
                raise self.error(
                    check.expected or "zero Event source violations",
                    f"Event validation failed: {check.name}",
                    check.repair or "Repair the governed Event source rows.",
                    stage="output_validation",
                )
            accepted.append((check.name, 0))
        return tuple(accepted)

    def _prepare_event_counts(self, expression: ir.Expr) -> None:
        # Trino inlines CTEs. Submit each independent scalar assertion in the
        # same snapshot instead of duplicating the complete match across a
        # large UNION query. Only violation counts leave the source here.
        for node in expression.op().find(ops.Aggregate):
            if (
                not isinstance(node.parent, ops.Union)
                or node.parent.schema.names != ("violations",)
                or node.groups
                or len(node.metrics) != 1
                or not isinstance(next(iter(node.metrics.values())), ops.Sum)
                or any(
                    union.distinct
                    for union in node.parent.find(ops.Union)
                    if union.schema.names == ("violations",)
                )
                or node in self._event_counts
            ):
                continue

            def leaves(relation: ops.Relation) -> Iterator[ops.Relation]:
                if isinstance(relation, ops.Union):
                    yield from leaves(relation.left)
                    yield from leaves(relation.right)
                else:
                    yield relation

            total = 0
            for index, part in enumerate(leaves(node.parent)):
                value = self.read_table(
                    self.prepare(part.to_expr(), role=f"event.output.check.{index}")
                )
                count = value["violations"][0].as_py() if value.num_rows == 1 else None
                if type(count) is not int or count < 0:
                    raise self.unsupported("invalid Event assertion count")
                total += count
            self._event_counts[node] = (
                ibis.literal(total, type="int64").name(node.schema.names[0]).as_table().op()
            )

    def _close_event_snapshot(self) -> None:
        if self._event_request is not None:
            try:
                if self._event_request.transaction_id not in (None, "NONE"):
                    self.submit(self.statement("ROLLBACK", role="event_source.snapshot_close"))
            finally:
                self._event_request.transaction_id = None
                self._event_request = None

    def _prepare(
        self,
        expression: ir.Expr,
        *,
        role: str,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        execute: bool = False,
    ) -> ScalarStatement:
        if self._event_prefix is None:
            return super()._prepare(expression, role=role, params=params, execute=execute)
        from marivo.analysis.compiler.event_time import _localize_utc

        self._check()
        localize = type(_localize_utc("UTC", ibis.timestamp("2000-01-01")).op())
        if any(
            not isinstance(node, localize)
            for node in expression.op().find((ops.InMemoryTable, ops.ScalarUDF, ops.AggUDF))
        ):
            raise self.unsupported("Event uploads or ungoverned UDFs")
        table = expression.as_table()
        if role == "event.reducer_summary":
            self._prepare_event_counts(table)
        if self._event_counts:
            table = table.op().replace(self._event_counts).to_expr()
        return ScalarStatement(
            self._compile_sql(table, params=params),
            (),
            table.schema().to_pyarrow(),
            role,
            self._context,
            native_structs=True,
        )

    def decode_cell(self, value: object, dtype: pa.DataType) -> object:
        if self._event_prefix is not None and pa.types.is_struct(dtype) and value is not None:
            if not isinstance(value, (tuple, list)) or len(value) != len(dtype):
                raise self.error(
                    "a native ROW matching the declared identity",
                    "invalid ROW transport",
                    "Correct the source identity transport before publication.",
                    stage="output_validation",
                )
            return {
                field.name: self.decode_cell(item, field.type)
                for field, item in zip(dtype, value, strict=True)
            }
        return super().decode_cell(value, dtype)

    def _compile_sql(
        self,
        expression: ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
    ) -> str:
        if self._event_prefix is None:
            return super()._compile_sql(expression, params=params)
        if params is not None:
            raise self.unsupported("parameterized Event relation")
        import sqlglot

        from marivo.analysis.materialization.trino_event_sql import compile_event_expression

        query = sqlglot.parse_one(compile_event_expression(expression), read="trino")
        prefix = sqlglot.parse_one(self._event_prefix + "SELECT 1", read="trino").args["with_"]
        existing = query.args.get("with_")
        if existing is not None:
            prefix.set("expressions", [*prefix.expressions, *existing.expressions])
        query.set("with_", prefix)
        return query.sql(dialect="trino")

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
            raise self.unsupported("Trino Event coverage provider")
        return resolve_event_coverage(
            definition,
            source_binding_fingerprint=source_binding_fingerprint,
            execution_domain_id=execution_domain_id,
            require_source_origin=require_source_origin,
        )

    def cursor(self, *, stream: bool) -> TrinoCursor:
        self._check()
        result = TrinoCursor(self, self._trino.con.cursor())
        self._cursors.add(result)
        return result

    def _lower(self, expression: ir.Expr) -> ir.Expr:
        if self._event_prefix is not None:
            return expression
        from marivo.analysis.materialization.temporal_sql import lower_temporal

        expression = lower_temporal(expression, self.engine)

        def qualify(
            node: ops.Node, _results: dict[ops.Node, ops.Node] | None = None, **kwargs: object
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
        # Scalar admission stays connector-neutral. Event replay of assertions
        # additionally requires the qualified Iceberg snapshot semantics.
        connector = self.read_scalar(
            self.statement(
                "SELECT connector_name FROM system.metadata.catalogs WHERE catalog_name = ?",
                parameters=(catalog,),
                role="source_schema",
            ),
        )
        self._source_connectors.add(str(connector))
        if "$" in name:
            raise self.unsupported(
                "an ordinary Trino relation; $-suffixed internal tables like "
                f"{name!r} (for example $partitions, $files, $snapshots or $properties) "
                "carry metadata shapes, not column sets; "
                "request the underlying ordinary relation or view instead."
            )
        qualified = ".".join(map(_identifier, (catalog, database, name)))
        relation_kind = self.read_scalar(
            self.statement(
                f"SELECT table_type FROM {_identifier(catalog)}.information_schema.tables "
                "WHERE table_schema = ? AND table_name = ?",
                parameters=(database, name),
                role="source_schema",
            ),
        )
        self._source_kinds.add(str(relation_kind))
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
                stack.callback(self._close_event_snapshot)
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
    from marivo.analysis.operators.registry import implementation, source_unsupported_reason

    if implementation(dataset).for_backend("trino") is None:
        raise MaterializationError(
            expected="a qualified Trino scalar closure",
            received=source_unsupported_reason(dataset, "trino") or "unregistered method",
            repair="Use qualified scalar methods over declared Trino relations and native civil dates.",
            stage="implementation_registration",
        )
