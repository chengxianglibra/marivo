"""Owned scalar SQL transport shared by the scalar source concrete adapters."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler.nodes import CompiledSampleFence
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.domains.completeness import EventCoverageProvider, EventCoverageResolution
from marivo.analysis.domains.contracts import EventDefinition
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import ExecutionContext, Parameter, Statement
from marivo.analysis.materialization.scalar_projection import project
from marivo.datasource.timezone import DatasourceEngineTimezone

if TYPE_CHECKING:
    from ibis.backends.clickhouse import Backend as ClickHouseBackend
    from ibis.backends.mysql import Backend as MySQLBackend
    from ibis.backends.sqlite import Backend as SQLiteBackend
    from ibis.backends.trino import Backend as TrinoBackend


class Cursor(Protocol):
    def execute(self, query: str, parameters: tuple[Parameter, ...] = ()) -> object: ...
    def fetchmany(self, size: int) -> Sequence[tuple[object, ...]]: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ScalarStatement(Statement):
    columns: tuple[tuple[int, ...], ...] = ()


def _cell(value: object, dtype: pa.DataType, *, run_ref: str | None = None) -> object:
    if value is None:
        return None
    if pa.types.is_date(dtype) and isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError as cause:
            raise MaterializationError(
                expected="a valid canonical ISO date",
                received="invalid source date text",
                repair="Correct source dates to valid YYYY-MM-DD values before retrying.",
                stage="output_validation",
                run_ref=run_ref,
            ) from cause
        raise MaterializationError(
            expected="a valid canonical ISO date",
            received="noncanonical source date text",
            repair="Correct source dates to valid YYYY-MM-DD values before retrying.",
            stage="output_validation",
            run_ref=run_ref,
        )
    if pa.types.is_integer(dtype) and isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise MaterializationError(
                expected="a finite integral Decimal for an integer result",
                received="non-integral or non-finite source Decimal",
                repair="Correct source values or the declared result type before retrying.",
                stage="output_validation",
                run_ref=run_ref,
            )
        return int(value)
    return value


class ScalarBatchStream:
    """Incremental driver reads with exact Arrow reconstruction and owned cleanup."""

    def __init__(
        self,
        adapter: ScalarExecutionAdapter,
        statement: Statement,
        chunk_size: int,
        record: Callable[[str, str], None] | None,
    ) -> None:
        self._adapter = adapter
        self._schema = statement.schema
        self._columns = (
            statement.columns
            if isinstance(statement, ScalarStatement)
            else tuple((i,) for i in range(len(self._schema)))
        )
        self._chunk_size = chunk_size
        self._closed = False
        self._cursor = adapter.cursor(stream=True)
        try:
            if record is not None:
                record(statement.role, statement.sql)
            if statement.parameters:
                self._cursor.execute(statement.sql, statement.parameters)
            else:
                self._cursor.execute(statement.sql)
        except BaseException:
            with suppress(BaseException):
                self._cursor.close()
            raise

    @property
    def schema(self) -> pa.Schema:
        return self._schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        if self._closed:
            raise self._adapter.error(
                "an open stream", "stream is already closed", "Read each stream once."
            )
        try:
            while rows := self._cursor.fetchmany(self._chunk_size):
                arrays = []
                for field, positions in zip(self._schema, self._columns, strict=True):
                    values: list[object]
                    if pa.types.is_struct(field.type):
                        values = [
                            {
                                child.name: _cell(
                                    row[index], child.type, run_ref=self._adapter._run_ref
                                )
                                for child, index in zip(field.type, positions, strict=True)
                            }
                            for row in rows
                        ]
                    else:
                        values = [
                            _cell(row[positions[0]], field.type, run_ref=self._adapter._run_ref)
                            for row in rows
                        ]
                    arrays.append(pa.array(values, type=field.type))
                yield pa.RecordBatch.from_arrays(arrays, schema=self._schema)
            self._adapter.validate_result()
        except BaseException:
            with suppress(BaseException):
                self.close()
            raise
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._cursor.close()
            finally:
                self._adapter._streams.discard(self)


class ScalarRows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = iter(rows)

    def fetchone(self) -> tuple[object, ...] | None:
        return next(self._rows, None)


class ScalarExecutionAdapter:
    """Common action ownership; concrete adapters own metadata and physical effects."""

    engine: str

    def __init__(
        self,
        backend: MySQLBackend | SQLiteBackend | TrinoBackend | ClickHouseBackend,
        *,
        run_ref: str | None = None,
    ) -> None:
        self._backend = backend
        self._run_ref = run_ref
        self._context = ExecutionContext()
        self._closed = False
        self._streams: set[ScalarBatchStream] = set()
        self._declared_columns: frozenset[str] | None = None

    def error(
        self, expected: str, received: str, repair: str, *, stage: str = "execution_boundary"
    ) -> MaterializationError:
        return MaterializationError(
            expected=expected, received=received, repair=repair, stage=stage, run_ref=self._run_ref
        )

    def cursor(self, *, stream: bool) -> Cursor:
        raise NotImplementedError

    def validate_result(self) -> None:
        """Reject driver-side lossy conversions before successful publication."""

    def _check(self, statement: Statement | None = None) -> None:
        if self._closed:
            raise self.error(
                "an open action-owned connection",
                "closed execution context",
                "Create a new action-owned execution context.",
            )
        if statement is not None and (
            statement.context is not self._context or statement.preparations
        ):
            raise self.error(
                "a statement owned by this context without preparations",
                "foreign statement or unsupported preparation",
                "Prepare the expression on this adapter without uploads or UDFs.",
            )

    def _lower(self, expression: ir.Expr) -> ir.Expr:
        raise NotImplementedError

    def _prepare(
        self,
        expression: ir.Expr,
        *,
        role: str,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        execute: bool = False,
    ) -> ScalarStatement:
        self._check()
        if expression.op().find((ops.InMemoryTable, ops.ScalarUDF, ops.AggUDF)):
            raise self.error(
                "declared relational input",
                "upload or UDF expression",
                "Use the registered source-only Group A shape.",
                stage="implementation_registration",
            )
        physical = project(self._lower(expression), run_ref=self._run_ref)
        if execute:
            self._backend._run_pre_execute_hooks(physical.expression)
        sql = self._backend.compile(physical.expression, params=params, limit=None)
        names = physical.expression.columns
        columns = tuple(tuple(names.index(name) for name in group) for group in physical.columns)
        return ScalarStatement(sql, (), physical.schema, role, self._context, columns=columns)

    def prepare(self, expression: ir.Expr, *, role: str = "query") -> Statement:
        return self._prepare(expression, role=role)

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
        self._check()
        for value in inputs:
            self._check(value)
        return Statement(sql, parameters, pa.schema([]), role, self._context)

    def submit(self, statement: Statement) -> ScalarRows:
        self._check(statement)
        cursor = self.cursor(stream=False)
        try:
            if statement.parameters:
                cursor.execute(statement.sql, statement.parameters)
            else:
                cursor.execute(statement.sql)
            rows: list[tuple[object, ...]] = []
            while batch := cursor.fetchmany(1024):
                rows.extend(batch)
            self.validate_result()
        except BaseException:
            with suppress(BaseException):
                cursor.close()
            raise
        cursor.close()
        return ScalarRows(rows)

    def batches(
        self,
        value: Statement | ir.Expr,
        *,
        chunk_size: int,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
        record: Callable[[str, str], None] | None = None,
    ) -> ScalarBatchStream:
        statement: Statement
        if isinstance(value, ir.Expr):
            statement = self._prepare(value, role=role, params=params, execute=True)
        else:
            if params is not None:
                raise self.error(
                    "parameters owned by the Statement",
                    "separate params supplied",
                    "Use Statement.parameters or pass an Ibis expression.",
                )
            statement = value
        self._check(statement)
        stream = ScalarBatchStream(self, statement, chunk_size, record)
        self._streams.add(stream)
        return stream

    def read_table(
        self,
        value: Statement | ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
        record: Callable[[str, str], None] | None = None,
    ) -> pa.Table:
        stream = self.batches(value, chunk_size=65536, params=params, role=role, record=record)
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
        record: Callable[[str, str], None] | None = None,
    ) -> object:
        if isinstance(value, ir.Expr):
            table = self.read_table(value, params=params, role=role, record=record)
            if table.num_rows == 1 and table.num_columns == 1:
                result: object = table.column(0)[0].as_py()
                return result
        else:
            if params is not None:
                raise self.error(
                    "Statement parameters", "separate params", "Use Statement.parameters."
                )
            if record is not None:
                record(value.role, value.sql)
            rows = self.submit(value)
            row = rows.fetchone()
            if row is not None and len(row) == 1 and rows.fetchone() is None:
                return row[0]
        raise self.error(
            "one row and one scalar",
            "non-scalar result",
            "Use read_table for relations.",
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
        raise NotImplementedError

    def table(self, name: str) -> ir.Table:
        return ibis.table(self.get_schema(name), name=name)

    def timezone(self) -> DatasourceEngineTimezone:
        raise NotImplementedError

    def initialize(self) -> None:
        self._check()

    def prepare_dataset(self, dataset: LogicalDataset) -> None:
        from marivo.analysis.compiler.normalize import required_entities
        from marivo.analysis.operators.registry import implementation
        from marivo.semantic.ir import TableSourceIR

        if implementation(dataset).for_backend(self.engine) is None:
            raise self.error(
                "an exact Group A registration",
                "unsupported dependency closure",
                "Use a single unversioned table and registered direct-column Metrics.",
                stage="implementation_registration",
            )

        columns: set[str] = set()
        for entity in required_entities(dataset):
            if isinstance(entity.source, TableSourceIR):
                columns.update(binding.source for _, binding in entity.source.columns)
        self._declared_columns = frozenset(columns)

    def interrupt(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        if self._closed:
            return
        from contextlib import ExitStack

        try:
            with ExitStack() as stack:
                stack.callback(self._backend.disconnect)
                for stream in tuple(self._streams):
                    stack.callback(stream.close)
        finally:
            self._closed = True

    def finish(self) -> None:
        self.disconnect()

    def unsupported(self, operation: str) -> MaterializationError:
        return self.error(
            "a registered read-only Group A operation",
            operation,
            "Keep this operation on an explicitly registered execution owner.",
            stage="implementation_registration",
        )

    def read_parquet(self, path: str, *, table_name: str) -> ir.Table:
        raise self.unsupported("read_parquet")

    def freeze_reader(self, name: str, reader: pa.RecordBatchReader) -> ir.Table:
        raise self.unsupported("freeze_reader")

    def table_statement(self, name: str, expression: ir.Table) -> Statement:
        raise self.unsupported("table_statement")

    def read_json(
        self, path: str, *, table_name: str, columns: Mapping[str, str], format: str
    ) -> ir.Table:
        raise self.unsupported("read_json")

    def sample_sql(self, fence: CompiledSampleFence) -> str:
        raise self.unsupported("sample_sql")

    def sample_validation_sql(self, fence: CompiledSampleFence) -> str:
        raise self.unsupported("sample_validation_sql")

    def resolve_coverage(
        self,
        definition: EventDefinition,
        *,
        provider: EventCoverageProvider | None,
        source_binding_fingerprint: str,
        execution_domain_id: str,
        require_source_origin: bool,
    ) -> EventCoverageResolution:
        raise self.unsupported("resolve_coverage")
