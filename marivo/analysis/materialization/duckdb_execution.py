"""Concrete DuckDB compilation, transport and owned connection lifetime."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import suppress
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
        finally:
            self._native.close()


class DuckDBExecutionAdapter:
    """One action-local connection; execution consumes statements, never expressions."""

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

    def read_table(self, statement: Statement) -> pa.Table:
        from ibis.backends.duckdb.converter import DuckDBPyArrowData

        cursor = self.submit(statement)
        table = cursor.to_arrow_table()
        if statement.schema:
            table = DuckDBPyArrowData.convert_table(
                table, ibis.Schema.from_pyarrow(statement.schema)
            )
        return table

    def read_scalar(self, statement: Statement) -> object:
        cursor = self.submit(statement)
        row: object = cursor.fetchone()
        if not isinstance(row, tuple) or len(row) != 1 or cursor.fetchone() is not None:
            raise _invalid("output_validation")
        return row[0]

    def batches(self, statement: Statement, *, chunk_size: int) -> DuckDBBatchStream:
        cursor = self.submit(statement)
        native = cursor.to_arrow_reader(batch_size=chunk_size)
        try:
            return DuckDBBatchStream(native, statement.schema)
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
        self, name: str, *, database: str | None = None, catalog: str | None = None
    ) -> ibis.Schema:
        sql = describe_statement(name, database, catalog)
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


def native_versions() -> tuple[str, str]:
    from duckdb import __version__

    return __version__, ibis.__version__
