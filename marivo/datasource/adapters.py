"""Owned, expression-only source reads for governed datasource consumers.

Provider selection is pure. Opening a selected session is the only step that
loads its optional backend driver. The session owns every issued read and cursor.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import isfinite
from typing import Literal, Protocol, cast, runtime_checkable

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa
from ibis.backends import BaseBackend

from marivo.datasource.engines import (
    SUPPORTED_BACKEND_TYPES,
    EngineProfile,
    require_profile_for_backend_type,
)
from marivo.datasource.errors import DatasourceSourceCapabilityError, repair
from marivo.datasource.ir import (
    CsvSourceIR,
    DatasourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    TableSourceIR,
)

SourceIR = TableSourceIR | CsvSourceIR | ParquetSourceIR | JsonSourceIR
Parameter = str | int | float | bool | bytes | Decimal | date | datetime | None
Termination = Literal["local_closed", "remote_unknown", "remote_confirmed"]


def _invalid(expected: str, received: str) -> DatasourceSourceCapabilityError:
    return DatasourceSourceCapabilityError(
        message="Governed source read cannot satisfy its binding.",
        expected=expected,
        received=received,
        location="datasource adapter",
        repair=repair(
            kind="inspect",
            canonical_id="inspect",
            action="Bind the declared source and use an expression qualified for this datasource.",
        ),
    )


def provider_for(backend: str) -> EngineProfile:
    """Select one backend without loading any driver or opening a connection."""
    return require_profile_for_backend_type(backend)


def provider_names() -> tuple[str, ...]:
    return SUPPORTED_BACKEND_TYPES


def _probe_backend(backend_name: str, backend: BaseBackend) -> None:
    expression = ibis.literal(1).name("probe").as_table()
    sql = backend.compile(expression, limit=None)
    cursor = _native_cursor(backend, backend_name, sql)
    try:
        fetchone = getattr(cursor, "fetchone", None)
        if callable(fetchone):
            row = fetchone()
            if row is None or len(row) != 1 or row[0] != 1:
                raise _invalid("one Ibis literal result equal to 1", repr(row))
        else:
            rows = getattr(cursor, "result_rows", None)
            if rows != [(1,)] and rows != ((1,),):
                raise _invalid("one Ibis literal result equal to 1", repr(rows))
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()


@dataclass(frozen=True, slots=True)
class PhysicalFacts:
    """Scoped physical observations; unknown never grants business coverage."""

    source_identity: str
    schema: pa.Schema
    observed_scope: Literal["unknown", "bounded_sample", "complete"] = "unknown"
    business_coverage: Literal["unknown"] = "unknown"


@dataclass(frozen=True, slots=True, eq=False)
class BoundSource:
    source: SourceIR
    relation: ir.Table
    facts: PhysicalFacts
    _owner: object


@dataclass(frozen=True, slots=True)
class PhysicalRequirement:
    """Physical operations required by an Analysis-owned method version."""

    method_id: str
    version: int
    operations: frozenset[Literal["scan", "filter", "project", "group", "count"]]


@dataclass(frozen=True, slots=True, eq=False)
class QualifiedSource:
    binding: BoundSource
    requirement: PhysicalRequirement
    _owner: object


@dataclass(frozen=True, slots=True, eq=False)
class CompiledRead:
    """Opaque session-issued evidence of one exact Ibis compilation."""

    expression: ir.Expr
    sql: str
    params: tuple[tuple[ir.Scalar, Parameter], ...]
    purpose: str
    schema: pa.Schema
    source_identity: str
    _owner: object


@dataclass(frozen=True, slots=True)
class _IssuedRead:
    expression: ir.Expr
    sql: str
    params: tuple[tuple[ir.Scalar, Parameter], ...]
    purpose: str
    schema: pa.Schema
    source_identity: str


@dataclass(slots=True)
class SourceSubmission:
    purpose: str
    source_identity: str
    expression_identity: int
    sql: str
    state: Literal["submitted", "succeeded", "failed", "closed_early"] = "submitted"


@runtime_checkable
class _Cursor(Protocol):
    def fetchmany(self, size: int) -> Sequence[Sequence[object]]: ...
    def close(self) -> None: ...


def _native_cursor(backend: BaseBackend, backend_name: str, sql: str) -> object:
    if backend_name == "duckdb":
        connection = getattr(backend, "con", None)
        native_cursor = getattr(connection, "cursor", None)
        if not callable(native_cursor):
            raise _invalid("a DuckDB native cursor", "cursor unavailable")
        cursor = native_cursor()
        try:
            cursor.execute(sql)
        except BaseException:
            cursor.close()
            raise
        return cursor
    raw_sql = getattr(backend, "raw_sql", None)
    if not callable(raw_sql):
        raise _invalid("selected backend native read transport", "no native cursor")
    return raw_sql(sql)


def _exact_array(values: list[object], field: pa.Field) -> pa.Array:
    arrow_type = field.type
    for value in values:
        if value is None:
            if not field.nullable:
                raise _invalid(f"non-null {field.name}", "null driver value")
            continue
        if pa.types.is_boolean(arrow_type):
            valid = type(value) is bool
        elif pa.types.is_integer(arrow_type):
            valid = type(value) is int
        elif pa.types.is_floating(arrow_type):
            valid = type(value) is float and isfinite(value)
        elif pa.types.is_decimal(arrow_type):
            valid = type(value) is Decimal
        elif pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
            valid = type(value) is str
        elif (
            pa.types.is_binary(arrow_type)
            or pa.types.is_large_binary(arrow_type)
            or pa.types.is_fixed_size_binary(arrow_type)
        ):
            valid = type(value) is bytes
        elif pa.types.is_timestamp(arrow_type):
            valid = type(value) is datetime and (value.tzinfo is not None) == (
                arrow_type.tz is not None
            )
        elif pa.types.is_date(arrow_type):
            valid = type(value) is date
        elif pa.types.is_time(arrow_type):
            valid = type(value) is time
        elif pa.types.is_duration(arrow_type):
            valid = type(value) is timedelta
        elif pa.types.is_struct(arrow_type):
            valid = type(value) is dict
            if valid:
                record = cast("dict[str, object]", value)
                valid = set(record) == set(arrow_type.names)
                if valid:
                    for child in arrow_type:
                        _exact_array([record[child.name]], child)
        elif pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
            valid = type(value) is list
            if valid:
                _exact_array(cast("list[object]", value), arrow_type.value_field)
        else:
            valid = False
        if not valid:
            raise _invalid(f"exact {arrow_type} value for {field.name}", type(value).__name__)
    try:
        result = pa.array(values).cast(arrow_type, safe=True)
    except (pa.ArrowException, OverflowError, TypeError, ValueError) as exc:
        raise _invalid(f"exact {arrow_type} value for {field.name}", type(exc).__name__) from exc
    if any(
        original != decoded for original, decoded in zip(values, result.to_pylist(), strict=True)
    ):
        raise _invalid(f"lossless {arrow_type} value for {field.name}", "value changed")
    return result


class SourceBatchStream:
    """One-shot native cursor stream with a schema fixed before the first row."""

    def __init__(
        self,
        session: SourceSession,
        cursor: _Cursor,
        schema: pa.Schema,
        chunk_size: int,
        submission: SourceSubmission,
    ):
        self._session = session
        self._cursor = cursor
        self._schema = schema
        self._chunk_size = chunk_size
        self._submission = submission
        self._started = False
        self._closed = False
        self._active: Iterator[pa.RecordBatch] | None = None

    @property
    def schema(self) -> pa.Schema:
        return self._schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        if self._started or self._closed:
            raise _invalid("one open, unconsumed batch stream", "closed or repeated stream")
        self._started = True
        self._active = self._iterate()
        return self._active

    def _iterate(self) -> Iterator[pa.RecordBatch]:
        try:
            while rows := self._cursor.fetchmany(self._chunk_size):
                if any(len(row) != len(self._schema) for row in rows):
                    raise _invalid("rows matching the fixed schema", "driver column count changed")
                arrays = [
                    _exact_array([row[index] for row in rows], field)
                    for index, field in enumerate(self._schema)
                ]
                yield pa.RecordBatch.from_arrays(arrays, schema=self._schema)
            self._submission.state = "succeeded"
        except GeneratorExit:
            raise
        except BaseException:
            self._submission.state = "failed"
            raise
        finally:
            self._active = None
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._submission.state == "submitted":
                self._submission.state = "closed_early"
            try:
                if self._active is not None:
                    active = self._active
                    self._active = None
                    close_active = getattr(active, "close", None)
                    if callable(close_active):
                        close_active()
            finally:
                try:
                    self._cursor.close()
                finally:
                    self._session._streams.discard(self)


class SourceSession:
    """Own one selected backend, its bound relations and compiled submissions."""

    def __init__(self, provider: EngineProfile, datasource: DatasourceIR, backend: BaseBackend):
        if datasource.backend_type != provider.name:
            raise _invalid(provider.name, datasource.backend_type)
        if backend.name != provider.name:
            raise _invalid(provider.name, backend.name)
        self.provider = provider
        self.datasource = datasource
        self._backend = backend
        self._token = object()
        self._issued: dict[int, tuple[CompiledRead, _IssuedRead]] = {}
        self._streams: set[SourceBatchStream] = set()
        self._closed = False
        self.submissions: list[SourceSubmission] = []

    def __enter__(self) -> SourceSession:
        self._ensure_open()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise _invalid("an open source session", "closed source session")

    def bind(self, source: SourceIR, *, source_identity: str) -> BoundSource:
        """Resolve a typed physical source without accepting executable SQL."""
        self._ensure_open()
        if not source_identity:
            raise _invalid("a nonempty exact source identity", "empty identity")
        if isinstance(source, TableSourceIR):
            database = source.database
            relation = (
                self._backend.table(source.table)
                if database is None
                else self._backend.table(source.table, database=database)
            )
        elif isinstance(source, CsvSourceIR) and self.provider.name == "duckdb":
            relation = self._backend.read_csv(
                source.path, header=source.header, delimiter=source.delimiter
            )
        elif isinstance(source, ParquetSourceIR) and self.provider.name == "duckdb":
            relation = self._backend.read_parquet(
                source.path, hive_partitioning=source.hive_partitioning
            )
        elif isinstance(source, JsonSourceIR) and self.provider.name == "duckdb":
            from marivo.datasource.json_source import read_json_source

            relation = read_json_source(self._backend, source)
        else:
            raise _invalid("a source qualified for the selected backend", type(source).__name__)
        if not isinstance(relation, ir.Table):
            raise _invalid("an Ibis table relation", type(relation).__name__)
        return BoundSource(
            source,
            relation,
            PhysicalFacts(source_identity, relation.schema().to_pyarrow()),
            self._token,
        )

    def qualify(self, binding: BoundSource, need: PhysicalRequirement) -> QualifiedSource:
        """Make the single physical qualification decision for basic reads."""
        self._ensure_open()
        if binding._owner is not self._token:
            raise _invalid("a source bound by this session", "foreign source binding")
        if self.provider.name not in {"duckdb", "sqlite"}:
            raise _invalid(
                "a verified basic read route for the selected backend",
                f"{self.provider.name} is pending R1.2 physical qualification",
            )
        supported = frozenset({"scan", "filter", "project", "group", "count"})
        if (
            not need.method_id
            or need.version < 1
            or not need.operations
            or not need.operations <= supported
        ):
            raise _invalid("one registered basic physical requirement", repr(need))
        return QualifiedSource(binding, need, self._token)

    def compile(
        self,
        qualified: QualifiedSource,
        expression: ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        purpose: str,
        expected_schema: pa.Schema,
    ) -> CompiledRead:
        """Issue a handle for one bound expression and unmodified Ibis SQL."""
        self._ensure_open()
        if qualified._owner is not self._token or qualified.binding._owner is not self._token:
            raise _invalid("this session's qualified source", "foreign qualification")
        if not purpose:
            raise _invalid("a nonempty read purpose", "empty purpose")
        if not isinstance(expression, ir.Expr):
            raise _invalid("an Ibis expression", type(expression).__name__)
        relation_node = qualified.binding.relation.op()
        if not any(node is relation_node for node in expression.op().find(ops.Relation)):
            raise _invalid("an expression derived from the bound relation", "unbound expression")
        physical_leaves = (
            ops.DatabaseTable,
            ops.UnboundTable,
            ops.InMemoryTable,
            ops.SQLQueryResult,
        )
        allowed_leaves = {id(node) for node in relation_node.find(physical_leaves)}
        observed_leaves = {id(node) for node in expression.op().find(physical_leaves)}
        if not observed_leaves or not observed_leaves <= allowed_leaves:
            raise _invalid(
                "only physical inputs from the bound source", "additional physical input"
            )
        actual_schema = expression.as_table().schema().to_pyarrow()
        if not actual_schema.equals(expected_schema, check_metadata=False):
            raise _invalid(str(actual_schema), str(expected_schema))
        supplied = dict(params or {})
        sql = self._backend.compile(expression.as_table(), params=supplied, limit=None)
        if not isinstance(sql, str) or not sql:
            raise _invalid("nonempty Ibis compiled SQL", type(sql).__name__)
        issued = CompiledRead(
            expression,
            sql,
            tuple(supplied.items()),
            purpose,
            expected_schema,
            qualified.binding.facts.source_identity,
            self._token,
        )
        self._issued[id(issued)] = (
            issued,
            _IssuedRead(
                expression,
                sql,
                issued.params,
                purpose,
                expected_schema,
                qualified.binding.facts.source_identity,
            ),
        )
        return issued

    def batches(self, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        """Submit only the exact artifact issued by this open session."""
        self._ensure_open()
        stored = self._issued.get(id(read))
        if read._owner is not self._token or stored is None or stored[0] is not read:
            raise _invalid("a compiled read issued by this session", "foreign or fabricated read")
        proof = stored[1]
        if (
            read.sql != proof.sql
            or read.expression is not proof.expression
            or read.params is not proof.params
            or read.purpose != proof.purpose
            or read.source_identity != proof.source_identity
            or not read.schema.equals(proof.schema, check_metadata=False)
        ):
            raise _invalid("the unchanged compiled read", "altered compiled read")
        if type(chunk_size) is not int or chunk_size < 1:
            raise _invalid("a positive batch size", repr(chunk_size))
        submission = SourceSubmission(
            proof.purpose, proof.source_identity, id(proof.expression.op()), proof.sql
        )
        self.submissions.append(submission)
        try:
            cursor = _native_cursor(self._backend, self.provider.name, proof.sql)
        except BaseException:
            submission.state = "failed"
            raise
        if not isinstance(cursor, _Cursor):
            close = getattr(cursor, "close", None)
            if callable(close):
                close()
            submission.state = "failed"
            raise _invalid("a closable native batch cursor", type(cursor).__name__)
        stream = SourceBatchStream(self, cursor, proof.schema, chunk_size, submission)
        self._streams.add(stream)
        return stream

    def interrupt(self) -> Termination:
        """Close local resources; remote termination remains unknown without proof."""
        self.close()
        return "local_closed" if self.provider.name in {"duckdb", "sqlite"} else "remote_unknown"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            for stream in tuple(self._streams):
                stream.close()
        finally:
            self._issued.clear()
            disconnect = getattr(self._backend, "disconnect", None)
            if callable(disconnect):
                disconnect()
