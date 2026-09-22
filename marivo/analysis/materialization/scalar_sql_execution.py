"""Owned scalar SQL transport shared by the scalar source concrete adapters."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler.source_dependencies import EntitySourceDependency
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.domains.completeness import EventCoverageProvider, EventCoverageResolution
from marivo.analysis.domains.contracts import EventDefinition
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import ExecutionContext, Parameter, Statement
from marivo.analysis.materialization.scalar_projection import project
from marivo.analysis.materialization.submissions import ObservedExecution
from marivo.analysis.materialization.temporal_sql import governed_temporal_operation
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
    if isinstance(dtype, pa.ListType) and pa.types.is_boolean(dtype.value_type):
        if not isinstance(value, (list, tuple)):
            raise MaterializationError(
                expected="a source Boolean array",
                received="invalid array representation",
                repair="Correct the source array transport before publication.",
                stage="output_validation",
                run_ref=run_ref,
            )
        return [_cell(item, dtype.value_type, run_ref=run_ref) for item in value]
    if pa.types.is_boolean(dtype):
        if type(value) is bool:
            return value
        if type(value) is int and value in (0, 1):
            return bool(value)
        raise MaterializationError(
            expected="Boolean or exact integer 0/1",
            received="invalid Boolean representation",
            repair="Correct the source Boolean storage to 0, 1 or NULL.",
            stage="output_validation",
            run_ref=run_ref,
        )
    if pa.types.is_timestamp(dtype):
        if isinstance(value, str):
            try:
                parsed_time = datetime.fromisoformat(value)
                if parsed_time.isoformat(sep=" ", timespec="microseconds") != value:
                    raise ValueError("noncanonical timestamp")
                value = parsed_time
            except ValueError as cause:
                raise MaterializationError(
                    expected="canonical civil YYYY-MM-DD HH:MM:SS.ffffff text",
                    received="invalid timestamp representation",
                    repair="Correct source timestamp storage without timezone or precision loss.",
                    stage="output_validation",
                    run_ref=run_ref,
                ) from cause
        if not isinstance(value, datetime) or (value.tzinfo is not None) != (dtype.tz is not None):
            raise MaterializationError(
                expected="a datetime matching the declared timezone semantics",
                received="incompatible timestamp representation",
                repair="Correct the source timestamp type and timezone binding.",
                stage="output_validation",
                run_ref=run_ref,
            )
        quantum = {"s": 1000000, "ms": 1000, "us": 1, "ns": 1}[dtype.unit]
        if value.microsecond % quantum:
            raise MaterializationError(
                expected=f"an exactly representable {dtype} value",
                received="timestamp fractional precision exceeds output unit",
                repair="Use a timestamp declaration preserving the source precision.",
                stage="output_validation",
                run_ref=run_ref,
            )
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
        value = int(value)
    if pa.types.is_integer(dtype):
        bits = dtype.bit_width
        lower = 0 if pa.types.is_unsigned_integer(dtype) else -(1 << (bits - 1))
        upper = (1 << (bits if pa.types.is_unsigned_integer(dtype) else bits - 1)) - 1
        if type(value) is not int or not lower <= value <= upper:
            raise MaterializationError(
                expected=f"an exact {dtype} result in [{lower}, {upper}]",
                received="non-integral or out-of-range integer result",
                repair="Use representable inputs or a Metric with an appropriate exact result type.",
                stage="output_validation",
                run_ref=run_ref,
            )
    return value


class ScalarBatchStream:
    """Incremental driver reads with exact Arrow reconstruction and owned cleanup."""

    def __init__(
        self,
        adapter: ScalarExecutionAdapter,
        statement: Statement,
        chunk_size: int,
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
            with adapter.submission(statement.role, statement.sql) as receipt:
                self._receipt = receipt
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
                                child.name: self._adapter.decode_cell(row[index], child.type)
                                for child, index in zip(field.type, positions, strict=True)
                            }
                            for row in rows
                        ]
                    else:
                        values = [
                            self._adapter.decode_cell(row[positions[0]], field.type) for row in rows
                        ]
                    arrays.append(pa.array(values, type=field.type))
                yield pa.RecordBatch.from_arrays(arrays, schema=self._schema)
            self._adapter.validate_result()
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
                self._cursor.close()
            finally:
                self._adapter._streams.discard(self)


class ScalarRows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = iter(rows)

    def fetchone(self) -> tuple[object, ...] | None:
        return next(self._rows, None)


class ScalarExecutionAdapter(ObservedExecution):
    """Common action ownership; concrete adapters own metadata and physical effects."""

    engine: str

    def __init__(
        self,
        backend: MySQLBackend | SQLiteBackend | TrinoBackend | ClickHouseBackend,
        *,
        run_ref: str | None = None,
    ) -> None:
        super().__init__()
        self._backend = backend
        self._run_ref = run_ref
        self._context = ExecutionContext()
        self._closed = False
        self._streams: set[ScalarBatchStream] = set()

    def error(
        self, expected: str, received: str, repair: str, *, stage: str = "execution_boundary"
    ) -> MaterializationError:
        return MaterializationError(
            expected=expected, received=received, repair=repair, stage=stage, run_ref=self._run_ref
        )

    def cursor(self, *, stream: bool) -> Cursor:
        raise NotImplementedError

    def decode_cell(self, value: object, dtype: pa.DataType) -> object:
        if (
            self.engine in {"mysql", "clickhouse"}
            and pa.types.is_timestamp(dtype)
            and dtype.tz is not None
            and isinstance(value, datetime)
            and value.tzinfo is None
        ):
            # These drivers return UTC instants without a tzinfo under the verified
            # UTC connection/physical transport contract.
            value = value.replace(tzinfo=timezone.utc)
        return _cell(value, dtype, run_ref=self._run_ref)

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
        from marivo.analysis.compiler.source_time import NATIVE_PARSE_OPERATIONS

        if any(
            not governed_temporal_operation(node) and not isinstance(node, NATIVE_PARSE_OPERATIONS)
            for node in expression.op().find((ops.InMemoryTable, ops.ScalarUDF, ops.AggUDF))
        ):
            raise self.error(
                "declared relational input",
                "upload or UDF expression",
                "Use an admitted read-only expression without uploads or UDF preparation.",
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
            with self.submission(statement.role, statement.sql):
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
        stream = ScalarBatchStream(self, statement, chunk_size)
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
            if table.num_rows == 1 and table.num_columns == 1:
                result: object = table.column(0)[0].as_py()
                return result
        else:
            if params is not None:
                raise self.error(
                    "Statement parameters", "separate params", "Use Statement.parameters."
                )
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
        dependency: EntitySourceDependency | None = None,
    ) -> ibis.Schema:
        raise NotImplementedError

    def table(self, name: str) -> ir.Table:
        return ibis.table(self.get_schema(name), name=name)

    def timezone(self) -> DatasourceEngineTimezone:
        raise NotImplementedError

    def initialize(self) -> None:
        self._check()

    def prepare_dataset(self, dataset: LogicalDataset) -> None:
        from marivo.analysis.operators.registry import implementation

        if implementation(dataset).for_backend(self.engine) is None:
            raise self.error(
                "an exact backend method registration",
                "unsupported dependency closure",
                "Use source types and method shapes admitted by this backend.",
                stage="implementation_registration",
            )

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
            "a registered read-only source operation",
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
