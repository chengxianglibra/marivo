"""Concrete DuckDB compilation, transport and owned connection lifetime."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import replace
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa
from duckdb import DuckDBPyConnection, InvalidInputException
from ibis.backends.duckdb import Backend
from sqlglot import expressions as sge

from marivo.analysis.compiler.nodes import CompiledSampleFence
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.domains.completeness import EventCoverageProvider, EventCoverageResolution
from marivo.analysis.domains.contracts import EventDefinition
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import ExecutionContext, Parameter, Statement
from marivo.datasource.timezone import DatasourceEngineTimezone


def _invalid(stage: str, *, run_ref: str | None = None) -> MaterializationError:
    return MaterializationError(
        expected="an owned, open DuckDB execution context and its declared execution inputs",
        received="an unsupported preparation or invalid execution lifetime",
        repair="Use the registered Dataset implementation and reconcile the failed Run before retrying.",
        stage=stage,
        run_ref=run_ref,
    )


def quote(name: str) -> str:
    return sge.to_identifier(name, quoted=True).sql(dialect="duckdb")


class DuckDBBatchStream:
    """Close the driver reader even if iteration never starts or stops early."""

    def __init__(self, native: pa.RecordBatchReader, schema: pa.Schema) -> None:
        self._native = native
        self._reader = pa.RecordBatchReader.from_batches(schema, native)

    @property
    def schema(self) -> pa.Schema:
        return self._reader.schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        yield from self._reader

    def close(self) -> None:
        try:
            self._reader.close()
        except BaseException:
            with suppress(BaseException):
                self._native.close()
            raise
        self._native.close()


class DuckDBExecutionAdapter:
    """One action-local connection for normal Ibis execution and explicit driver SQL."""

    def __init__(self, backend: Backend, *, reserve: Callable[[str], None] | None = None) -> None:
        self._backend = backend
        self._reserve = reserve
        self._context = ExecutionContext()
        self._compiled: dict[ops.Node, Statement] = {}
        self._prepared: dict[str, ops.Node | type[ops.ScalarUDF]] = {}
        self._closed = False

    def prepare(self, expression: ir.Expr, *, role: str = "query") -> Statement:
        node = expression.op()
        if node not in self._compiled:
            # Compile is pure. Resource-producing hooks are declared here and
            # applied only under the reserved connection before submission.
            if node.find((ops.GeoSpatialUnOp, ops.GeoSpatialBinOp)):
                raise _invalid("implementation_registration")
            self._backend._verify_in_memory_tables_are_unique(expression)
            hooks = tuple(
                x.to_expr()
                for x in node.find((ops.InMemoryTable, ops.ScalarUDF))
                if isinstance(x, ops.InMemoryTable) or x.__input_type__.name != "BUILTIN"
            )
            sql = self._backend.compile(expression.as_table(), limit=None)
            statement = Statement(
                sql, (), expression.as_table().schema().to_pyarrow(), role, self._context, hooks
            )
            self._compiled[node] = statement
        return replace(self._compiled[node], role=role)

    def compile(self, expression: ir.Expr) -> str:
        return self.prepare(expression).sql

    def statement(
        self,
        sql: str,
        *,
        role: str = "statement",
        parameters: tuple[Parameter, ...] = (),
        inputs: tuple[Statement, ...] = (),
    ) -> Statement:
        if any(value.context is not self._context for value in inputs):
            raise _invalid("execution_boundary")
        preparations = tuple(expression for value in inputs for expression in value.preparations)
        return Statement(sql, parameters, pa.schema([]), role, self._context, preparations)

    def _check(self, statement: Statement) -> None:
        if self._closed or statement.context is not self._context:
            raise _invalid("execution_boundary")
        for expression in statement.preparations:
            node = expression.op()
            name = node.name if isinstance(node, ops.InMemoryTable) else type(node).__name__
            identity = node if isinstance(node, ops.InMemoryTable) else type(node)
            previous = self._prepared.get(name)
            if previous is not None:
                if previous != identity:
                    raise _invalid("implementation_registration")
                continue
            if self._reserve is None:
                raise _invalid("implementation_registration")
            self._reserve(name)
            if isinstance(node, ops.InMemoryTable):
                self._backend._register_in_memory_tables(expression)
            else:
                if not isinstance(node, ops.ScalarUDF) or node.__input_type__.name not in (
                    "PYTHON",
                    "PYARROW",
                ):
                    raise _invalid("implementation_registration")
                with suppress(InvalidInputException):
                    self._backend.con.remove_function(name)
                register: Callable[[DuckDBPyConnection], None] = (
                    self._backend._register_python_udf(node)
                    if node.__input_type__.name == "PYTHON"
                    else self._backend._register_pyarrow_udf(node)
                )
                register(self._backend.con)
            self._prepared[name] = identity

    def submit(self, statement: Statement) -> DuckDBPyConnection:
        self._check(statement)
        # The native driver receives exactly the selected SQL and parameters.
        cursor: object = self._backend.con.execute(statement.sql, statement.parameters or None)
        if not isinstance(cursor, DuckDBPyConnection):
            raise _invalid("execution_boundary")
        return cursor

    @contextmanager
    def _ibis_execution(
        self, expression: ir.Expr, *, role: str, record: Callable[[str, str], None] | None
    ) -> Iterator[None]:
        if self._closed:
            raise _invalid("execution_boundary")
        # This backend belongs exclusively to this action. Observe its actual
        # raw submissions while Ibis owns compilation, parameters and hooks.
        # Restore the original method even when preparation or submission fails.
        backend = self._backend
        original = backend.raw_sql
        had_override = "raw_sql" in vars(backend)
        node = expression.op()
        if node.find((ops.GeoSpatialUnOp, ops.GeoSpatialBinOp)):
            raise _invalid("implementation_registration")
        backend._verify_in_memory_tables_are_unique(expression)
        for operation in node.find((ops.InMemoryTable, ops.ScalarUDF)):
            if isinstance(operation, ops.ScalarUDF) and operation.__input_type__.name == "BUILTIN":
                continue
            if self._reserve is None:
                raise _invalid("implementation_registration")
            name = (
                operation.name
                if isinstance(operation, ops.InMemoryTable)
                else type(operation).__name__
            )
            identity = operation if isinstance(operation, ops.InMemoryTable) else type(operation)
            previous = self._prepared.get(name)
            if previous is not None and previous != identity:
                raise _invalid("implementation_registration")
            if previous is None:
                self._reserve(name)
                self._prepared[name] = identity

        def raw_sql(query: str | sge.Expression, **kwargs: object) -> DuckDBPyConnection:
            sql = query if isinstance(query, str) else query.sql(dialect="duckdb")
            if record is not None:
                record(role, sql)
            if not kwargs:
                return self.submit(self.statement(sql, role=role))
            result: object = original(query, **kwargs)
            if not isinstance(result, DuckDBPyConnection):
                raise _invalid("execution_boundary")
            return result

        backend.raw_sql = raw_sql
        try:
            yield
        finally:
            if had_override:
                backend.raw_sql = original
            else:
                delattr(backend, "raw_sql")

    def read_table(
        self,
        value: Statement | ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
        record: Callable[[str, str], None] | None = None,
    ) -> pa.Table:
        from ibis.backends.duckdb.converter import DuckDBPyArrowData

        if isinstance(value, ir.Expr):
            stream = self.batches(
                value, chunk_size=1_000_000, params=params, role=role, record=record
            )
            try:
                table = pa.Table.from_batches(stream, schema=stream.schema)
            except BaseException:
                with suppress(BaseException):
                    stream.close()
                raise
            stream.close()
            return DuckDBPyArrowData.convert_table(table, value.as_table().schema())
        if params is not None:
            raise _invalid("execution_boundary")
        cursor = self.submit(value)
        table = cursor.to_arrow_table()
        if value.schema:
            table = DuckDBPyArrowData.convert_table(table, ibis.Schema.from_pyarrow(value.schema))
        return table

    def read_scalar(
        self,
        value: Statement | ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
        record: Callable[[str, str], None] | None = None,
    ) -> object:
        if isinstance(value, ir.Expr):
            table = self.read_table(value, params=params, role=role, record=record)
            if table.num_rows != 1 or table.num_columns != 1:
                raise _invalid("output_validation")
            result: object = table.column(0)[0].as_py()
            return result
        if params is not None:
            raise _invalid("execution_boundary")
        cursor = self.submit(value)
        row: object = cursor.fetchone()
        if not isinstance(row, tuple) or len(row) != 1 or cursor.fetchone() is not None:
            raise _invalid("output_validation")
        return row[0]

    def batches(
        self,
        value: Statement | ir.Expr,
        *,
        chunk_size: int,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
        record: Callable[[str, str], None] | None = None,
    ) -> DuckDBBatchStream:
        if isinstance(value, ir.Expr):
            with self._ibis_execution(value, role=role, record=record):
                native = self._backend.to_pyarrow_batches(
                    value, params=params, limit=None, chunk_size=chunk_size
                )
            schema = value.as_table().schema().to_pyarrow()
        else:
            if params is not None:
                raise _invalid("execution_boundary")
            cursor = self.submit(value)
            native = cursor.to_arrow_reader(batch_size=chunk_size)
            schema = value.schema
        try:
            return DuckDBBatchStream(native, schema)
        except BaseException:
            native.close()
            raise

    def interrupt(self) -> None:
        self._backend.con.interrupt()

    def configure(self) -> None:
        for sql in ("SET threads=1",):
            self.submit(self.statement(sql, role="source_setting"))

    def initialize(self) -> None:
        self.configure()
        self.submit(self.statement("SET TimeZone='UTC'", role="source_setting"))

    def disconnect(self) -> None:
        if not self._closed:
            self._backend.disconnect()
            self._closed = True
            self._compiled.clear()

    def finish(self) -> None:
        self.disconnect()

    def get_schema(
        self,
        name: str,
        *,
        database: str | None = None,
        catalog: str | None = None,
        record: Callable[[str, str], None] | None = None,
    ) -> ibis.Schema:
        sql = describe_statement(name, database, catalog)
        if record is not None:
            record("source_schema", sql)
        rows = self.submit(self.statement(sql, role="source_schema"))
        fields: list[tuple[str, dt.DataType]] = []
        while (row := rows.fetchone()) is not None:
            if len(row) < 3 or not isinstance(row[0], str) or not isinstance(row[1], str):
                raise _invalid("source_binding")
            fields.append(
                (
                    row[0],
                    self._backend.compiler.type_mapper.from_string(
                        row[1], nullable=row[2] == "YES"
                    ),
                )
            )
        return ibis.schema(fields)

    def table(self, name: str) -> ir.Table:
        return ibis.table(self.get_schema(name), name=name)

    def table_statement(self, name: str, expression: ir.Table) -> Statement:
        return self.statement(
            f"CREATE TEMPORARY TABLE {quote(name)} AS {self.compile(expression)}",
            role="source_fence",
            inputs=(self.prepare(expression),),
        )

    def freeze_reader(self, name: str, reader: pa.RecordBatchReader) -> ir.Table:
        registered = name + "_input"
        if self._reserve is not None:
            self._reserve(registered)
            self._reserve(name)
        self._backend.con.register(registered, reader)
        try:
            self.submit(
                self.statement(
                    f"CREATE TEMPORARY TABLE {quote(name)} AS SELECT * FROM {quote(registered)}",
                    role="retained_fence",
                )
            )
        finally:
            self._backend.con.unregister(registered)
        return ibis.table(ibis.Schema.from_pyarrow(reader.schema), name=name)

    def read_parquet(self, path: str, *, table_name: str) -> ir.Table:
        if self._reserve is not None:
            self._reserve(table_name)
        sql = f"CREATE TEMPORARY VIEW {quote(table_name)} AS SELECT * FROM read_parquet({sge.Literal.string(path).sql(dialect='duckdb')})"
        self.submit(self.statement(sql, role="retained_scan"))
        return self.table(table_name)

    def read_json(
        self,
        path: str,
        *,
        table_name: str,
        columns: Mapping[str, str],
        format: str,
    ) -> ir.Table:
        sql = json_statement(table_name, path, columns, format)
        self.submit(self.statement(sql, role="source_fence_reader"))
        return ibis.table({name: dt.dtype(kind) for name, kind in columns.items()}, name=table_name)

    def timezone(self) -> DatasourceEngineTimezone:
        from marivo.datasource.engines import require_profile_for_backend_type
        from marivo.datasource.timezone import _fallback

        query = require_profile_for_backend_type("duckdb").timezone_probe_sql
        if query is None:
            return _fallback()
        try:
            name = str(self.read_scalar(self.statement(query, role="source_timezone")))
            zone = ZoneInfo(name)
        except Exception as error:
            return _fallback(f"engine timezone probe failed: {error}")
        return DatasourceEngineTimezone(name, zone, "iana", "engine")

    def prepare_dataset(self, dataset: LogicalDataset) -> None:
        from marivo.analysis.compiler.normalize import logical_roots
        from marivo.analysis.operators.driver_contracts import DriverCandidatePayload

        if any(isinstance(root.payload, DriverCandidatePayload) for root in logical_roots(dataset)):
            self.install_numeric()

    def sample_sql(self, fence: CompiledSampleFence) -> str:
        from marivo.analysis.materialization.duckdb_sampling import sample_sql

        return sample_sql(self, fence)

    def sample_validation_sql(self, fence: CompiledSampleFence) -> str:
        from marivo.analysis.materialization.duckdb_statements import reservoir_validation

        return reservoir_validation(fence.relation_name, fence.identity_columns)

    def install_numeric(self) -> None:
        from marivo.analysis.compiler.driver_numeric import DRIVER_NUMERIC_SETUP_SQL

        for name, sql in zip(
            ("__marivo_driver_float_units", "__marivo_driver_float_from_units"),
            DRIVER_NUMERIC_SETUP_SQL,
            strict=True,
        ):
            if self._reserve is not None:
                self._reserve(name)
            self.submit(self.statement(sql, role="source_preparation"))

    def resolve_coverage(
        self,
        definition: EventDefinition,
        *,
        provider: EventCoverageProvider | None,
        source_binding_fingerprint: str,
        execution_domain_id: str,
        require_source_origin: bool,
    ) -> EventCoverageResolution:
        from marivo.analysis.domains.completeness import resolve_event_coverage

        # Preserve the existing provider's owned native connection contract.
        return resolve_event_coverage(
            definition,
            provider=provider,
            backend=self._backend,
            source_binding_fingerprint=source_binding_fingerprint,
            execution_domain_id=execution_domain_id,
            require_source_origin=require_source_origin,
        )


def bind_duckdb(
    candidate: object, *, reserve: Callable[[str], None], run_ref: str
) -> DuckDBExecutionAdapter:
    if not isinstance(candidate, Backend):
        raise _invalid("execution_boundary", run_ref=run_ref)
    return DuckDBExecutionAdapter(candidate, reserve=reserve)


def describe_statement(name: str, database: str | None, catalog: str | None) -> str:
    return sge.Describe(
        this=sge.Table(
            this=sge.to_identifier(name, quoted=True),
            db=None if database is None else sge.to_identifier(database, quoted=True),
            catalog=None if catalog is None else sge.to_identifier(catalog, quoted=True),
        )
    ).sql(dialect="duckdb")


def json_statement(name: str, path: str, columns: Mapping[str, str], format: str) -> str:
    physical = {
        key: Backend.compiler.type_mapper.to_string(dt.dtype(kind)) for key, kind in columns.items()
    }
    options = [
        sge.to_identifier("format").eq(sge.convert(format)),
        sge.to_identifier("columns").eq(
            sge.Struct.from_arg_list(
                [
                    sge.PropertyEQ(this=sge.to_identifier(key), expression=sge.convert(kind))
                    for key, kind in physical.items()
                ]
            )
        ),
    ]
    reader = sge.Anonymous(this="read_json_auto", expressions=[sge.convert(path), *options])
    return f"CREATE OR REPLACE TEMPORARY VIEW {quote(name)} AS {sge.select('*').from_(reader).sql(dialect='duckdb')}"


def open_native_backend() -> Backend:
    return ibis.duckdb.connect()


def admit_dataset(dataset: LogicalDataset) -> None:
    """Validate DuckDB physical sampling parameters before connection or Run creation."""
    from marivo.analysis.compiler.normalize import logical_roots
    from marivo.analysis.materialization.duckdb_sampling import admit_sampling
    from marivo.analysis.observation.contracts import PopulationPayload
    from marivo.analysis.observation.population_sample import PopulationSamplePayload

    for root in logical_roots(dataset):
        if isinstance(root.payload, PopulationPayload) and root.payload.sampling is not None:
            admit_sampling(root.payload.sampling)
        elif isinstance(root.payload, PopulationSamplePayload):
            admit_sampling(root.payload.policy)
