"""Read-only PostgreSQL scalar execution and action-owned server cursors."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, suppress
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa
from sqlglot import expressions as sge

from marivo.analysis.compiler.source_dependencies import EntitySourceDependency
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.domains.completeness import EventCoverageProvider, EventCoverageResolution
from marivo.analysis.domains.contracts import EventDefinition
from marivo.analysis.materialization.errors import (
    MaterializationError,
    source_type_errors,
    unsupported_source_type,
)
from marivo.analysis.materialization.execution import ExecutionContext, Parameter, Statement
from marivo.analysis.materialization.submissions import ObservedExecution
from marivo.analysis.materialization.temporal_sql import governed_temporal_operation
from marivo.analysis.operators.postgres_support import supported_type
from marivo.datasource.timezone import DatasourceEngineTimezone

if TYPE_CHECKING:
    from ibis.backends.postgres import Backend


_FailureReason = Literal[
    "closed_context",
    "closed_stream",
    "foreign_statement",
    "preparation",
    "statement_params",
    "scalar_shape",
    "cross_catalog",
    "schema_row",
    "missing_relation",
    "timezone_probe",
    "backend_type",
    "unsupported_dataset",
    "record_shape",
    "unsupported_operation",
]


def _invalid(
    reason: _FailureReason,
    *,
    detail: str | None = None,
    run_ref: str | None = None,
) -> MaterializationError:
    failures: dict[_FailureReason, tuple[str, str, str, str]] = {
        "closed_context": (
            "execution_boundary",
            "an open action-owned PostgreSQL connection",
            "the PostgreSQL execution context is closed",
            "Create a new action-owned execution context; do not reuse a finished adapter.",
        ),
        "closed_stream": (
            "execution_boundary",
            "an open PostgreSQL result stream",
            "the PostgreSQL result stream is closed",
            "Consume each stream once and do not iterate it after close or exhaustion.",
        ),
        "foreign_statement": (
            "execution_boundary",
            "a statement prepared by this execution context",
            "the statement belongs to a different execution context",
            "Prepare the statement on the adapter that will execute it.",
        ),
        "preparation": (
            "implementation_registration",
            "read-only relational inputs without uploads or UDF registration",
            "the expression or statement requests unregistered preparation",
            "Use declared PostgreSQL table columns and admitted read-only scalar or relational methods.",
        ),
        "statement_params": (
            "execution_boundary",
            "Statement.parameters or Ibis expression parameters, not both",
            "params was supplied alongside a prepared Statement",
            "Put driver parameters in Statement.parameters and omit params, or pass an Ibis expression.",
        ),
        "scalar_shape": (
            "output_validation",
            "exactly one row and one column for a scalar read",
            "the query returned a non-scalar result shape",
            "Use read_table for a relation or correct the scalar query to return one value.",
        ),
        "cross_catalog": (
            "source_binding",
            "a table in the database selected by this datasource connection",
            "a cross-database catalog qualifier was supplied",
            "Declare the correct datasource database and use only schema/table qualification.",
        ),
        "schema_row": (
            "source_binding",
            "column name, PostgreSQL type and boolean nullability metadata",
            "the PostgreSQL metadata query returned a malformed column row",
            "Inspect the datasource metadata response and restore the registered PostgreSQL schema contract.",
        ),
        "missing_relation": (
            "source_binding",
            "a relation resolved by the declared schema or connection search_path",
            "no columns were found for the requested relation",
            "Verify the declared table and schema, search_path, and read-only role visibility.",
        ),
        "timezone_probe": (
            "source_binding",
            "the PostgreSQL profile's effective timezone probe",
            "the registered PostgreSQL profile has no timezone query",
            "Restore the PostgreSQL datasource profile timezone probe before executing temporal reads.",
        ),
        "backend_type": (
            "execution_boundary",
            "an Ibis PostgreSQL backend for the selected PostgreSQL binding",
            "the binding supplied a different backend type",
            "Resolve the selected datasource through its PostgreSQL connection owner.",
        ),
        "unsupported_dataset": (
            "implementation_registration",
            "an exact PostgreSQL registration for the Dataset's full dependency closure",
            "the Dataset has no PostgreSQL implementation registration",
            "Use PostgreSQL source types and method shapes admitted by the implementation registry.",
        ),
        "record_shape": (
            "output_validation",
            "a binary RECORD tuple matching its declared Arrow struct fields",
            "the driver RECORD value does not match the declared struct shape",
            "Verify the registered identity projection and binary driver decoding before publishing results.",
        ),
        "unsupported_operation": (
            "implementation_registration",
            "an operation implemented by the read-only PostgreSQL adapter",
            "an unsupported physical operation was requested",
            "Keep this operation on an explicitly registered implementation; do not upload or prepare it in PostgreSQL.",
        ),
    }
    stage, expected, received, repair = failures[reason]
    return MaterializationError(
        expected=expected,
        received=received if detail is None else f"{received}: {detail}",
        repair=repair,
        stage=stage,
        run_ref=run_ref,
    )


def _postgres_expression(expression: ir.Expr) -> ir.Expr:
    """Lower boolean numeric casts through PostgreSQL's supported int4 cast."""
    replacements = {
        node: ops.Cast(ops.Cast(node.arg, to=dt.int32), to=node.to)
        for node in expression.op().find(ops.Cast)
        if node.arg.dtype.is_boolean() and node.to.is_integer() and node.to != dt.int32
    }
    return expression.op().replace(replacements).to_expr() if replacements else expression


class PostgresScalarRows:
    """Detached assertion rows; the driver cursor is closed before decoding."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = iter(rows)

    def fetchone(self) -> tuple[object, ...] | None:
        return next(self._rows, None)


def _transport_row(
    schema: pa.Schema, row: tuple[object, ...], *, run_ref: str | None = None
) -> tuple[object, ...]:
    """Map binary RECORD tuples to Arrow field names without text or numeric loss."""

    def value(field: pa.Field, raw: object) -> object:
        if pa.types.is_struct(field.type) and raw is not None:
            if not isinstance(raw, tuple) or len(raw) != field.type.num_fields:
                raise _invalid(
                    "record_shape",
                    detail=f"field={field.name}; type={type(raw).__name__}",
                    run_ref=run_ref,
                )
            return {
                child.name: value(child, item) for child, item in zip(field.type, raw, strict=True)
            }
        from marivo.analysis.materialization.scalar_sql_execution import _cell

        return _cell(raw, field.type, run_ref=run_ref)

    return tuple(value(field, item) for field, item in zip(schema, row, strict=True))


class PostgresBatchStream:
    """Own a named cursor and its read-resource transaction until explicit close."""

    def __init__(
        self,
        adapter: PostgresExecutionAdapter,
        statement: Statement,
        chunk_size: int,
    ) -> None:
        self._adapter = adapter
        self._schema = statement.schema
        self._chunk_size = chunk_size
        self._stack = ExitStack()
        self._closed = False
        try:
            self._stack.enter_context(adapter._backend.con.transaction())
            self._cursor = self._stack.enter_context(
                adapter._backend.con.cursor(name="marivo_" + uuid4().hex, binary=True)
            )
            sql = statement.sql
            with adapter.submission(statement.role, sql) as receipt:
                self._receipt = receipt
                self._cursor.execute(sql, statement.parameters or None)
        except BaseException:
            with suppress(BaseException):
                self._stack.close()
            raise

    @property
    def schema(self) -> pa.Schema:
        return self._schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        if self._closed:
            raise self._adapter._error("closed_stream")
        try:
            while rows := self._cursor.fetchmany(self._chunk_size):
                if any(
                    pa.types.is_struct(field.type) or pa.types.is_timestamp(field.type)
                    for field in self._schema
                ):
                    rows = [
                        _transport_row(self._schema, row, run_ref=self._adapter._run_ref)
                        for row in rows
                    ]
                arrays = [
                    pa.array([row[index] for row in rows], type=field.type)
                    for index, field in enumerate(self._schema)
                ]
                yield pa.RecordBatch.from_arrays(arrays, schema=self._schema)
        except BaseException as error:
            self._receipt.fail(error)
            with suppress(BaseException):
                self.close()
            raise
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._stack.close()
            finally:
                self._adapter._streams.discard(self)


class PostgresExecutionAdapter(ObservedExecution):
    """One real connection; assertions and output use independent read lifetimes."""

    def __init__(self, backend: Backend, *, run_ref: str | None = None) -> None:
        super().__init__()
        self._backend = backend
        self._run_ref = run_ref
        self._context = ExecutionContext()
        self._closed = False
        self._streams: set[PostgresBatchStream] = set()

    def _error(self, reason: _FailureReason, *, detail: str | None = None) -> MaterializationError:
        return _invalid(reason, detail=detail, run_ref=self._run_ref)

    def _expression(self, expression: ir.Expr) -> None:
        if self._closed:
            raise self._error("closed_context")
        if any(
            not governed_temporal_operation(node)
            for node in expression.op().find((ops.InMemoryTable, ops.ScalarUDF, ops.AggUDF))
        ):
            raise self._error("preparation")

    def prepare(self, expression: ir.Expr, *, role: str = "query") -> Statement:
        self._expression(expression)
        return Statement(
            self._backend.compile(_postgres_expression(expression).as_table(), limit=None),
            (),
            expression.as_table().schema().to_pyarrow(),
            role,
            self._context,
        )

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
        for value in inputs:
            self._check(value)
        return Statement(sql, parameters, pa.schema([]), role, self._context)

    def _check(self, statement: Statement) -> None:
        if self._closed:
            raise self._error("closed_context")
        if statement.context is not self._context:
            raise self._error("foreign_statement")
        if statement.preparations:
            raise self._error("preparation")

    def submit(self, statement: Statement) -> PostgresScalarRows:
        self._check(statement)
        with self._backend.con.cursor() as cursor, self.submission(statement.role, statement.sql):
            cursor.execute(statement.sql, statement.parameters or None)
            rows: list[tuple[object, ...]] = cursor.fetchall() if cursor.description else []
        return PostgresScalarRows(rows)

    def batches(
        self,
        value: Statement | ir.Expr,
        *,
        chunk_size: int,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
    ) -> PostgresBatchStream:
        if isinstance(value, ir.Expr):
            self._expression(value)
            self._backend._run_pre_execute_hooks(value)
            statement = Statement(
                self._backend.compile(
                    _postgres_expression(value).as_table(), params=params, limit=None
                ),
                (),
                value.as_table().schema().to_pyarrow(),
                role,
                self._context,
            )
        else:
            if params is not None:
                raise self._error("statement_params")
            statement = value
        self._check(statement)
        stream = PostgresBatchStream(self, statement, chunk_size)
        self._streams.add(stream)
        return stream

    def read_table(
        self,
        value: Statement | ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
    ) -> pa.Table:
        stream = self.batches(value, chunk_size=65536, params=params, role=role)
        try:
            result = pa.Table.from_batches(stream, schema=stream.schema)
        except BaseException:
            with suppress(BaseException):
                stream.close()
            raise
        stream.close()
        return result

    def read_scalar(
        self,
        value: Statement | ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
    ) -> object:
        if isinstance(value, ir.Expr):
            table = self.read_table(value, params=params, role=role)
            if table.num_rows != 1 or table.num_columns != 1:
                raise self._error(
                    "scalar_shape", detail=f"rows={table.num_rows}; columns={table.num_columns}"
                )
            result: object = table.column(0)[0].as_py()
            return result
        if params is not None:
            raise self._error("statement_params")
        rows = self.submit(value)
        row = rows.fetchone()
        if row is None or len(row) != 1 or rows.fetchone() is not None:
            raise self._error("scalar_shape")
        return row[0]

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
            raise self._error("cross_catalog")
        qualified = ".".join(
            sge.to_identifier(part, quoted=True).sql(dialect="postgres")
            for part in ((name,) if database is None else (database, name))
        )
        table = sge.Literal.string(qualified).sql(dialect="postgres")
        sql = (
            "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), "
            "NOT a.attnotnull FROM pg_catalog.pg_attribute a "
            "WHERE a.attnum > 0 AND NOT a.attisdropped "
            f"AND a.attrelid = pg_catalog.to_regclass({table}) ORDER BY a.attnum"
        )
        rows = self.submit(self.statement(sql, role="source_schema"))
        fields = {}
        while (row := rows.fetchone()) is not None:
            if (
                len(row) != 3
                or not isinstance(row[0], str)
                or not isinstance(row[1], str)
                or type(row[2]) is not bool
            ):
                raise self._error("schema_row")
            if columns is not None and row[0] not in columns:
                continue
            if dependency is not None and (
                row[1].startswith("character(") or row[1] == "character"
            ):
                raise unsupported_source_type(dependency, row[0], row[1])
            with source_type_errors(dependency, row[0], row[1]):
                fields[row[0]] = self._backend.compiler.type_mapper.from_string(
                    row[1], nullable=bool(row[2])
                )
            datatype = fields[row[0]]
            if datatype.is_string():
                fields[row[0]] = datatype = dt.string.copy(nullable=bool(row[2]))
            kind = (
                "timestamp"
                if isinstance(datatype, dt.Timestamp)
                and datatype.timezone is None
                and datatype.scale in (None, 0, 1, 2, 3, 4, 5, 6)
                else str(datatype.copy(nullable=True))
            )
            if dependency is not None and not supported_type(kind):
                raise unsupported_source_type(dependency, row[0], row[1])
        temporal = [column for column, dtype in fields.items() if dtype.is_timestamp()]
        if temporal:
            predicates = " OR ".join(
                "NOT isfinite("
                + sge.to_identifier(column, quoted=True).sql(dialect="postgres")
                + ")"
                for column in temporal
            )
            violations = self.read_scalar(
                self.statement(
                    f"SELECT count(*) FROM {qualified} WHERE {predicates}",
                    role="engine_check.postgres_timestamps",
                ),
            )
            if violations != 0:
                raise MaterializationError(
                    expected="finite PostgreSQL timestamp values or NULL",
                    received="non-finite necessary timestamp column",
                    repair="Correct infinite timestamps before executing again.",
                    stage="output_validation",
                    run_ref=self._run_ref,
                )
        if not fields and dependency is None:
            raise self._error("missing_relation", detail=qualified)
        return ibis.schema(fields)

    def table(self, name: str) -> ir.Table:
        return ibis.table(self.get_schema(name), name=name)

    def timezone(self) -> DatasourceEngineTimezone:
        from marivo.datasource.engines import require_profile_for_backend_type
        from marivo.datasource.timezone import resolve_engine_timezone

        return resolve_engine_timezone(
            require_profile_for_backend_type("postgres").timezone_probe_sql,
            lambda query: self.read_scalar(self.statement(query, role="source_timezone")),
        )

    def initialize(self) -> None:
        if self._closed:
            raise self._error("closed_context")

    def prepare_dataset(self, dataset: LogicalDataset) -> None:
        admit_dataset(dataset)

    def interrupt(self) -> None:
        self._backend.con.cancel()

    def disconnect(self) -> None:
        if self._closed:
            return
        try:
            with ExitStack() as stack:
                stack.callback(self._backend.disconnect)
                for stream in tuple(self._streams):
                    stack.callback(stream.close)
        finally:
            self._closed = True

    def finish(self) -> None:
        self.disconnect()

    def read_parquet(self, path: str, *, table_name: str) -> ir.Table:
        raise self._error("unsupported_operation", detail="read_parquet")

    def freeze_reader(self, name: str, reader: pa.RecordBatchReader) -> ir.Table:
        raise self._error("unsupported_operation", detail="freeze_reader")

    def table_statement(self, name: str, expression: ir.Table) -> Statement:
        raise self._error("unsupported_operation", detail="table_statement")

    def read_json(
        self,
        path: str,
        *,
        table_name: str,
        columns: Mapping[str, str],
        format: str,
    ) -> ir.Table:
        raise self._error("unsupported_operation", detail="read_json")

    def resolve_coverage(
        self,
        definition: EventDefinition,
        *,
        provider: EventCoverageProvider | None,
        source_binding_fingerprint: str,
        execution_domain_id: str,
        require_source_origin: bool,
    ) -> EventCoverageResolution:
        raise self._error("unsupported_operation", detail="resolve_coverage")


def bind_postgres(
    candidate: object,
    *,
    reserve: Callable[[str], None],
    run_ref: str,
) -> PostgresExecutionAdapter:
    from ibis.backends.postgres import Backend

    if not isinstance(candidate, Backend):
        raise _invalid("backend_type", detail=type(candidate).__name__, run_ref=run_ref)
    # The common factory supplies reserve; this read-only adapter needs no preparation resources.
    return PostgresExecutionAdapter(candidate, run_ref=run_ref)


def admit_dataset(dataset: LogicalDataset) -> None:
    from marivo.analysis.operators.postgres_support import unsupported_reason
    from marivo.analysis.operators.registry import implementation

    if implementation(dataset).for_backend("postgres") is None:
        raise _invalid("unsupported_dataset", detail=unsupported_reason(dataset))
