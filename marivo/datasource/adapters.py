"""Owned, expression-only source reads for governed datasource consumers.

Provider selection is pure. Opening a selected session is the only step that
loads its optional backend driver. The session owns every issued read and cursor.
"""

from __future__ import annotations

import re
import socket
from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, nullcontext, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from importlib import import_module
from itertools import islice
from math import isfinite
from threading import Event, Lock, get_ident
from typing import TYPE_CHECKING, Literal, Protocol, cast, runtime_checkable
from uuid import uuid4

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa
from ibis.backends import BaseBackend

from marivo.datasource.capabilities import (
    ProviderStatementSubmission,
    execute_provider_statement,
    provider_statement_log,
)
from marivo.datasource.engines import (
    SUPPORTED_BACKEND_TYPES,
    EngineProfile,
    require_profile_for_backend_type,
)
from marivo.datasource.errors import (
    DatasourceSourceCapabilityError,
    _backend_failure_summary,
    repair,
)
from marivo.datasource.ir import (
    CsvSourceIR,
    DatasourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    QueryParamScalar,
    QueryParamScalarList,
    TableSourceIR,
)
from marivo.datasource.table_source import table_source_expression

if TYPE_CHECKING:
    from marivo.analysis.domains.completeness import EventCoverageProvider

DURATION_UNIT_METADATA_KEY = b"marivo:duration_unit"
TIMESTAMP_UNIT_METADATA_KEY = b"marivo:timestamp_unit"

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
        rows = cursor.fetchmany(2)
        if len(rows) != 1 or len(rows[0]) != 1 or rows[0][0] != 1:
            raise _invalid("one Ibis literal result equal to 1", repr(rows))
    finally:
        cursor.close()


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
    operations: frozenset[
        Literal["scan", "filter", "project", "group", "count", "join", "union", "window", "sort"]
    ]


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
    inline_relations: tuple[ops.Relation, ...]


@dataclass(slots=True)
class SourceSubmission:
    purpose: str
    source_identity: str
    expression_identity: int
    sql: str
    state: Literal["submitted", "succeeded", "failed", "closed_early"] = "submitted"
    cursor_state: Literal["open", "closed", "connection_owned", "close_failed"] = "open"
    connection_disconnected: bool = False
    termination: Termination | None = None


@runtime_checkable
class _Cursor(Protocol):
    def fetchmany(self, size: int) -> Sequence[Sequence[object]]: ...
    def close(self) -> None: ...


class _ControlPool(Protocol):
    def clear(self) -> None: ...


_CURSOR_OWNER: ContextVar[Callable[[_Cursor], None] | None] = ContextVar(
    "source_cursor_owner", default=None
)

_CLICKHOUSE_DEADLINE: ContextVar[dict[str, str | float | int] | None] = ContextVar(
    "clickhouse_read_deadline", default=None
)


def _clickhouse_deadline_settings(
    backend: BaseBackend, seconds: float
) -> dict[str, str | float | int]:
    connection = getattr(backend, "con", None)
    observed = getattr(connection, "server_settings", None)
    settings = observed if isinstance(observed, Mapping) else {}
    unavailable = [
        name
        for name in ("max_execution_time", "timeout_before_checking_execution_speed")
        if getattr(settings.get(name), "readonly", 1) != 0
    ]
    mode = settings.get("timeout_overflow_mode")
    if mode is None or (
        getattr(mode, "readonly", 1) != 0 and getattr(mode, "value", None) != "throw"
    ):
        unavailable.append("timeout_overflow_mode=throw")
    if unavailable:
        raise DatasourceSourceCapabilityError(
            message="ClickHouse cannot enforce the owned execute deadline.",
            expected="writable native timeout settings and throwing timeout overflow",
            received="unavailable or locked: " + ", ".join(unavailable),
            location="datasource adapter",
            repair=repair(
                kind="reconnect",
                canonical_id="test",
                action="Allow the reported timeout settings for the read-only account and retry the datasource connection.",
            ),
        )
    return {
        "max_execution_time": seconds,
        "timeout_before_checking_execution_speed": 0,
        "timeout_overflow_mode": "throw",
        "query_id": uuid4().hex,
    }


class _ClickHouseNativeStream(Protocol):
    def __enter__(self) -> Iterator[Sequence[object]]: ...
    def __exit__(self, *args: object) -> object: ...


class _ClickHouseCursor:
    """Own the ClickHouse row stream returned for one compiled expression."""

    def __init__(self, native: _ClickHouseNativeStream):
        self._native = native
        self._closed = False
        self._rows: Iterator[Sequence[object]] = iter(())
        try:
            owner = _CURSOR_OWNER.get()
            if owner is not None:
                owner(self)
            self._rows = iter(native.__enter__())
        except BaseException:
            self.close()
            raise

    def fetchmany(self, size: int) -> Sequence[Sequence[object]]:
        if self._closed:
            raise _invalid("an open ClickHouse row stream", "closed stream")
        return tuple(tuple(row) for row in islice(self._rows, size))

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._native.__exit__(None, None, None)


@runtime_checkable
class _DuckDBNativeResult(Protocol):
    def fetchmany(self, size: int) -> Sequence[Sequence[object]]: ...


class _DuckDBCursor:
    """Keep connection-local file and JSON registrations on their owning backend."""

    def __init__(self, connection: _DuckDBNativeResult):
        self._connection = connection
        self._closed = False

    def fetchmany(self, size: int) -> Sequence[Sequence[object]]:
        if self._closed:
            raise _invalid("an open DuckDB result", "closed result")
        return self._connection.fetchmany(size)

    def close(self) -> None:
        self._closed = True


class _MySQLCursor:
    """Restore exact temporal converters when an unbuffered result closes."""

    def __init__(self, native: _Cursor, converter: dict[int, object], original: dict[int, object]):
        self._native = native
        self._converter = converter
        self._original = original
        self._closed = False

    def fetchmany(self, size: int) -> Sequence[Sequence[object]]:
        return self._native.fetchmany(size)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._native.close()
            finally:
                self._converter.update(self._original)


def _native_cursor(backend: BaseBackend, backend_name: str, sql: str) -> _Cursor:
    connection = getattr(backend, "con", None)
    if backend_name == "duckdb":
        execute = getattr(connection, "execute", None)
        if not callable(execute) or not isinstance(connection, _DuckDBNativeResult):
            raise _invalid("a DuckDB connection-local result", "connection unavailable")
        execute(sql)
        return _DuckDBCursor(connection)
    if backend_name == "clickhouse":
        stream = getattr(connection, "query_rows_stream", None)
        if not callable(stream):
            raise _invalid("a ClickHouse row stream", "stream unavailable")
        settings = _CLICKHOUSE_DEADLINE.get()
        cursor = _ClickHouseCursor(
            stream(sql, settings=settings) if settings is not None else stream(sql)
        )
        return cursor
    factory = getattr(connection, "cursor", None)
    if not callable(factory):
        raise _invalid("a selected backend native cursor", "cursor unavailable")
    converter: dict[int, object] | None = None
    original: dict[int, object] | None = None
    if backend_name == "mysql":
        assert connection is not None
        field_type = import_module("MySQLdb.constants.FIELD_TYPE")
        selected = (field_type.DATE, field_type.DATETIME, field_type.TIMESTAMP)
        converter = connection.converter
        original = {kind: converter[kind] for kind in selected}

        def exact_temporal(raw: str | bytes) -> str:
            return raw if isinstance(raw, str) else raw.decode("ascii")

        converter.update(dict.fromkeys(selected, exact_temporal))
        try:
            cursor = factory(import_module("MySQLdb.cursors").SSCursor)
        except BaseException:
            converter.update(original)
            raise
    else:
        cursor = factory()
    pending: _Cursor | None = (
        _MySQLCursor(cursor, converter, original)
        if converter is not None and original is not None and isinstance(cursor, _Cursor)
        else cursor
        if isinstance(cursor, _Cursor)
        else None
    )
    owner = _CURSOR_OWNER.get()
    try:
        if backend_name in ("mysql", "trino") and pending is not None and owner is not None:
            owner(pending)
        cursor.execute(sql)
    except BaseException:
        if backend_name == "mysql" and owner is not None:
            raise
        try:
            cursor.close()
        finally:
            if converter is not None and original is not None:
                converter.update(original)
        raise
    if not isinstance(cursor, _Cursor):
        cursor.close()
        if converter is not None and original is not None:
            converter.update(original)
        raise _invalid("a closable native batch cursor", type(cursor).__name__)
    return (
        pending
        if converter is not None and original is not None and pending is not None
        else cursor
    )


def _exact_array(
    values: list[object], field: pa.Field, *, backend_name: str | None = None
) -> pa.Array:
    arrow_type = field.type
    normalized_values: list[object] = []
    for value in values:
        if value is None:
            if not field.nullable:
                raise _invalid(f"non-null {field.name}", "null driver value")
            normalized_values.append(None)
            continue
        if pa.types.is_boolean(arrow_type):
            if (
                backend_name in ("sqlite", "mysql", "clickhouse")
                and type(value) is int
                and value in (0, 1)
            ):
                value = bool(value)
            valid = type(value) is bool
        elif pa.types.is_integer(arrow_type):
            if (
                backend_name in ("postgres", "mysql")
                and type(value) is Decimal
                and value.is_finite()
                and value == value.to_integral_value()
            ):
                value = int(value)
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
            if backend_name in {"sqlite", "mysql"} and type(value) is str:
                if not re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:[+-][0-9]{2}:[0-9]{2})?",
                    value,
                ):
                    raise _invalid(f"exact {arrow_type} value for {field.name}", "text timestamp")
                try:
                    value = datetime.fromisoformat(value)
                except ValueError as exc:
                    raise _invalid(
                        f"exact {arrow_type} value for {field.name}", "invalid text timestamp"
                    ) from exc
            # These UTC readers return naive native/text carriers. Only an
            # explicitly UTC Arrow schema authorizes restoring the zone.
            if (
                backend_name in ("clickhouse", "mysql", "sqlite")
                and arrow_type.tz == "UTC"
                and type(value) is datetime
                and value.tzinfo is None
            ):
                value = value.replace(tzinfo=timezone.utc)
            valid = type(value) is datetime and (value.tzinfo is not None) == (
                arrow_type.tz is not None
            )
        elif pa.types.is_date(arrow_type):
            if backend_name in {"mysql", "sqlite"} and type(value) is str:
                if backend_name == "sqlite" and not re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value
                ):
                    raise _invalid(f"exact {arrow_type} value for {field.name}", "text date")
                try:
                    value = date.fromisoformat(value)
                except ValueError as exc:
                    raise _invalid(
                        f"exact {arrow_type} value for {field.name}", "invalid text date"
                    ) from exc
            valid = type(value) is date
        elif pa.types.is_time(arrow_type):
            valid = type(value) is time
        elif pa.types.is_duration(arrow_type):
            valid = type(value) is timedelta
        elif pa.types.is_struct(arrow_type):
            trino_row = (
                backend_name == "trino"
                and type(value).__module__ == "trino.types"
                and type(value).__name__ == "NamedRowTuple"
            )
            if type(value) is tuple or trino_row:
                row_values = cast("tuple[object, ...]", value)
                if len(row_values) != len(arrow_type):
                    raise _invalid(
                        f"exact {arrow_type} value for {field.name}", "tuple field count changed"
                    )
                if trino_row:
                    names = getattr(value, "_names", None)
                    if names != list(arrow_type.names):
                        raise _invalid(
                            f"exact {arrow_type} value for {field.name}", "row field names changed"
                        )
                value = dict(zip(arrow_type.names, row_values, strict=True))
            valid = type(value) is dict
            if valid:
                record = cast("dict[str, object]", value)
                valid = set(record) == set(arrow_type.names)
                if valid:
                    for child in arrow_type:
                        child_value = record[child.name]
                        if (
                            backend_name == "postgres"
                            and type(child_value) is str
                            and pa.types.is_integer(child.type)
                            and re.fullmatch(r"(?:0|-[1-9][0-9]*|[1-9][0-9]*)", child_value)
                        ):
                            child_value = int(child_value)
                            record[child.name] = child_value
                        _exact_array([child_value], child, backend_name=backend_name)
        elif pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
            valid = type(value) is list
            if valid:
                _exact_array(
                    cast("list[object]", value), arrow_type.value_field, backend_name=backend_name
                )
        else:
            valid = False
        if not valid:
            raise _invalid(f"exact {arrow_type} value for {field.name}", type(value).__name__)
        normalized_values.append(value)
    try:
        result = pa.array(normalized_values, type=arrow_type, safe=True)
    except (pa.ArrowException, OverflowError, TypeError, ValueError) as exc:
        raise _invalid(f"exact {arrow_type} value for {field.name}", type(exc).__name__) from exc
    if any(
        original != decoded
        for original, decoded in zip(normalized_values, result.to_pylist(), strict=True)
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
            check = self._session._checkpoint
            check()
            while rows := self._cursor.fetchmany(self._chunk_size):
                check()
                if any(len(row) != len(self._schema) for row in rows):
                    raise _invalid("rows matching the fixed schema", "driver column count changed")
                arrays = [
                    _exact_array(
                        [row[index] for row in rows],
                        field,
                        backend_name=self._session.provider.name,
                    )
                    for index, field in enumerate(self._schema)
                ]
                yield pa.RecordBatch.from_arrays(arrays, schema=self._schema)
            check()
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
            if (
                self._session.provider.name in {"clickhouse", "mysql"}
                and self._submission.state != "succeeded"
            ):
                self._session._request_interrupt()
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
                    self._session._release_cursor(self._cursor, self._submission)
                except BaseException:
                    self._submission.state = "failed"
                    raise
                finally:
                    self._session._streams.discard(self)


def _capture_indices(count: int, checkpoint: Callable[[], None]) -> ir.Table:
    """Build every capture index with a small product of two-row digits."""
    if count < 2:
        raise _invalid("at least two captured rows", str(count))
    relation: ir.Table | None = None
    fields = tuple(f"capture_bit_{bit}" for bit in range((count - 1).bit_length()))
    for field in fields:
        checkpoint()
        digit = (
            ibis.literal(0, type="int64")
            .name(field)
            .as_table()
            .union(ibis.literal(1, type="int64").name(field).as_table(), distinct=False)
            .view()
        )
        relation = digit if relation is None else relation.cross_join(digit)
    assert relation is not None
    index: ir.Value = ibis.literal(0, type="int64")
    for bit, field in enumerate(fields):
        index = index + relation[field] * (1 << bit)
    indices = relation.select(capture_index=index)
    return indices.filter(indices.capture_index < count).limit(count)


def _inline_exchange(table: pa.Table, checkpoint: Callable[[], None], backend: str) -> ir.Table:
    """Represent a complete captured exchange as typed read-only Ibis literals."""
    schema = ibis.schema(table.schema)
    if not schema.names:
        raise _invalid("a nonempty captured schema", "zero columns")

    def scalar(value: object, dtype: dt.DataType) -> ir.Value:
        if value is None:
            return ibis.null().cast(dtype)
        if dtype.is_string() and isinstance(value, str):
            return ibis.literal(value, type=dtype)
        if dtype.is_boolean() and type(value) is bool:
            return ibis.literal(value, type=dtype)
        if dtype.is_integer() and type(value) is int:
            return ibis.literal(str(value)).cast(dtype)
        if dtype.is_floating() and type(value) is float and isfinite(value):
            return ibis.literal(repr(value)).cast(dtype)
        if dtype.is_decimal() and isinstance(value, Decimal) and value.is_finite():
            return ibis.literal(str(value)).cast(dtype)
        if dtype.is_timestamp() and isinstance(value, datetime):
            if dtype.timezone == "UTC" and value.tzinfo is not None:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            elif dtype.timezone is not None or value.tzinfo is not None:
                raise _invalid("a captured UTC or unzoned timestamp", str(dtype))
            return ibis.literal(value.isoformat(sep=" ", timespec="microseconds")).cast(dtype)
        if dtype.is_date() and isinstance(value, date) and not isinstance(value, datetime):
            return ibis.literal(value.isoformat()).cast(dtype)
        raise _invalid(f"a captured scalar matching {dtype}", type(value).__name__)

    def empty_value(dtype: dt.DataType) -> object:
        if dtype.nullable:
            return None
        if dtype.is_string():
            return ""
        if dtype.is_boolean():
            return False
        if dtype.is_integer():
            return 0
        if dtype.is_floating():
            return 0.0
        if dtype.is_decimal():
            return Decimal(0)
        if dtype.is_timestamp():
            return datetime(1970, 1, 1, tzinfo=timezone.utc if dtype.timezone == "UTC" else None)
        if dtype.is_date():
            return date(1970, 1, 1)
        raise _invalid(f"an empty captured scalar matching {dtype}", "unsupported type")

    rows: list[ir.Table] = []
    array_rows: list[ir.Value] = []
    array_exchange = backend in ("clickhouse", "trino")
    captured: dict[str, list[ir.Value]] = {name: [] for name in schema.names}
    for index in range(max(1, table.num_rows)):
        checkpoint()
        columns = {
            name: scalar(
                table.column(name)[index].as_py() if table.num_rows else empty_value(dtype), dtype
            ).name(name)
            for name, dtype in schema.items()
        }
        first, *rest = schema.names
        if table.num_rows > 1 and array_exchange:
            array_rows.append(ibis.struct(columns))
        elif table.num_rows > 1:
            for name, value in columns.items():
                captured[name].append(value)
            if backend != "mysql":
                rows.append(ibis.literal(index, type="int64").name("capture_index").as_table())
        else:
            row = columns[first].as_table().mutate(**{name: columns[name] for name in rest})
            rows.append(row if table.num_rows else row.filter(ibis.literal(False)))
    if array_rows:
        if backend == "trino":
            indices = _capture_indices(table.num_rows, checkpoint)
            nested = indices.select(captured_row=ibis.array(array_rows)[indices.capture_index])
        else:
            nested = ibis.array(array_rows).unnest().name("captured_row").as_table()
        payload = nested.captured_row
        relation = nested.select(
            **{name: payload[name].cast(dtype) for name, dtype in schema.items()}
        ).view()
        if not relation.schema().to_pyarrow().equals(table.schema, check_metadata=False):
            raise _invalid("the complete captured schema", str(relation.schema()))
        return relation
    while len(rows) > 1:
        checkpoint()
        rows = [
            rows[index].union(rows[index + 1], distinct=False)
            if index + 1 < len(rows)
            else rows[index]
            for index in range(0, len(rows), 2)
        ]
    relation = (
        _capture_indices(table.num_rows, checkpoint)
        if backend == "mysql" and table.num_rows > 1
        else rows[0]
    )
    if table.num_rows > 1:
        # Unique row indices preserve duplicate captured rows while the narrow
        # union avoids repeating every typed column in each native branch.
        relation = relation.distinct().limit(table.num_rows)
        relation = relation.select(
            **{
                name: ibis.cases(
                    *(
                        (relation.capture_index == index, value)
                        for index, value in enumerate(captured[name][:-1])
                    ),
                    else_=captured[name][-1],
                ).cast(dtype)
                for name, dtype in schema.items()
            }
        )
    if backend == "mysql" and table.num_rows > 1:
        # Keep the complete typed projection behind the same exact cardinality
        # barrier, rather than letting native joins duplicate every CASE branch.
        relation = relation.limit(table.num_rows)
    relation = relation.view()
    if not relation.schema().to_pyarrow().equals(table.schema, check_metadata=False):
        raise _invalid("the complete captured schema", str(relation.schema()))
    return relation


class SourceSession:
    """Own one selected backend, its bound relations and compiled submissions."""

    def __init__(
        self,
        provider: EngineProfile,
        datasource: DatasourceIR,
        backend: BaseBackend,
        *,
        owns_backend: bool = True,
    ):
        if datasource.backend_type != provider.name:
            raise _invalid(provider.name, datasource.backend_type)
        if backend.name != provider.name:
            raise _invalid(provider.name, backend.name)
        self.provider = provider
        self.datasource = datasource
        self._backend = backend
        self._owns_backend = owns_backend
        self._backend_disconnected = False
        self._token = object()
        self._issued: dict[int, tuple[CompiledRead, _IssuedRead]] = {}
        self._bindings_by_identity: dict[str, tuple[SourceIR, dict[str, object]]] = {}
        self._bound_sources: dict[str, BoundSource] = {}
        self._staged_relations: dict[ops.Relation, tuple[frozenset[str], str]] = {}
        self._inline_relations: dict[ops.Relation, frozenset[str]] = {}
        self._streams: set[SourceBatchStream] = set()
        self._closed = False
        self.submissions: list[SourceSubmission] = []
        self._interrupted_submissions: tuple[SourceSubmission, ...] = ()
        self._pending_cursor: _Cursor | None = None
        self._cancel_control: BaseBackend | None = None
        self._cancel_pool: _ControlPool | None = None
        self._cancel_thread_id: int | None = None
        self._mysql_connection: object | None = None
        self._mysql_socket: socket.socket | None = None
        self._mysql_cancel_requested = False
        self._mysql_active: SourceSubmission | None = None
        self._clickhouse_reader: str | None = None
        self._clickhouse_active: tuple[SourceSubmission, str] | None = None
        self._clickhouse_cancel_requested = False
        self._clickhouse_cancel_failure: DatasourceSourceCapabilityError | None = None
        self._cancel_control_released = True
        self._cancel_lock = Lock()
        self.cancel_submissions: tuple[ProviderStatementSubmission, ...] = ()
        self._cursor_released = Event()
        self._cursor_thread: int | None = None
        self.domain_authority: dict[str, object] | None = None
        self.domain_time_units: dict[tuple[str, str], str] = {}
        self._domain_coverage_provider: EventCoverageProvider | None = None
        self._checkpoint: Callable[[], None] = lambda: None
        self._seconds_remaining: Callable[[], float | None] = lambda: None

    def __enter__(self) -> SourceSession:
        self._ensure_open()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise _invalid("an open source session", "closed source session")

    def bind(
        self,
        source: SourceIR,
        *,
        source_identity: str,
        source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None = None,
    ) -> BoundSource:
        """Resolve a typed physical source without accepting executable SQL."""
        self._ensure_open()
        if not source_identity:
            raise _invalid("a nonempty exact source identity", "empty identity")
        supplied_source_params: dict[str, object] = dict(source_params or {})
        prior = self._bindings_by_identity.get(source_identity)
        if prior is not None and prior != (source, supplied_source_params):
            raise _invalid("one source and parameter binding per identity", "identity reused")
        if prior is not None:
            return self._bound_sources[source_identity]
        if isinstance(source, TableSourceIR):
            if source_params:
                raise _invalid(
                    "no runtime parameters for a table source", repr(tuple(source_params))
                )
            relation = table_source_expression(self._backend, source)
        elif isinstance(source, CsvSourceIR) and self.provider.name == "duckdb":
            if source_params:
                raise _invalid("no runtime parameters for a CSV source", repr(tuple(source_params)))
            csv_options: dict[str, object] = {}
            if not source.header:
                csv_options["header"] = False
            if source.delimiter != ",":
                csv_options["delim"] = source.delimiter
            relation = self._backend.read_csv(source.path, **csv_options)
            if source.columns:
                relation = relation.select(
                    *(relation[physical].name(output) for output, physical in source.columns)
                )
        elif isinstance(source, ParquetSourceIR) and self.provider.name == "duckdb":
            if source_params:
                raise _invalid(
                    "no runtime parameters for a Parquet source", repr(tuple(source_params))
                )
            relation = self._backend.read_parquet(
                source.path, hive_partitioning=source.hive_partitioning
            )
            if source.columns is not None:
                relation = relation.select(*source.columns)
        elif isinstance(source, JsonSourceIR) and self.provider.name == "duckdb":
            from marivo.datasource.json_source import read_json_source

            relation = read_json_source(self._backend, source, source_params=source_params)
        else:
            raise _invalid("a source qualified for the selected backend", type(source).__name__)
        if not isinstance(relation, ir.Table):
            raise _invalid("an Ibis table relation", type(relation).__name__)
        physical_schema = relation.schema().to_pyarrow()
        if isinstance(source, ParquetSourceIR):
            from pathlib import Path

            import pyarrow.parquet as pq

            # Arrow's fixed Duration logical metadata survives Parquet even though
            # DuckDB presents its lossless tick carrier as BIGINT.
            if Path(source.path).is_file():
                arrow_schema = pq.read_schema(source.path)
                fields = []
                for field in physical_schema:
                    index = arrow_schema.get_field_index(field.name)
                    original = arrow_schema.field(index).type if index >= 0 else None
                    if original is not None and pa.types.is_duration(original):
                        if field.type != pa.int64():
                            raise _invalid("int64 Parquet duration ticks", str(field.type))
                        field = field.with_metadata(
                            {
                                **(field.metadata or {}),
                                DURATION_UNIT_METADATA_KEY: original.unit.encode("ascii"),
                            }
                        )
                    if original is not None and pa.types.is_timestamp(original):
                        if not pa.types.is_timestamp(field.type):
                            raise _invalid("timestamp Parquet carrier", str(field.type))
                        field = field.with_metadata(
                            {
                                **(field.metadata or {}),
                                TIMESTAMP_UNIT_METADATA_KEY: original.unit.encode("ascii"),
                            }
                        )
                    fields.append(field)
                physical_schema = pa.schema(fields, metadata=physical_schema.metadata)
        binding = BoundSource(
            source,
            relation,
            PhysicalFacts(source_identity, physical_schema),
            self._token,
        )
        self._bindings_by_identity[source_identity] = (source, supplied_source_params)
        self._bound_sources[source_identity] = binding
        return binding

    def binding_for(self, source_identity: str) -> BoundSource:
        """Return this session's exact typed binding for a governed consumer."""
        self._ensure_open()
        binding = self._bound_sources.get(source_identity)
        if binding is None:
            raise _invalid("a source bound by this session", source_identity)
        return binding

    def qualify(self, binding: BoundSource, need: PhysicalRequirement) -> QualifiedSource:
        """Make the single physical qualification decision for basic reads."""
        self._ensure_open()
        if binding._owner is not self._token:
            raise _invalid("a source bound by this session", "foreign source binding")
        if not isinstance(self._backend, BaseBackend):
            raise _invalid(
                "a verified basic read route for the selected backend",
                f"{self.provider.name} backend is not a live Ibis backend",
            )
        supported = frozenset({"scan", "filter", "project", "group", "count"})
        if self.provider.name == "duckdb":
            supported |= {"join", "union", "window", "sort"}
        elif self.provider.name in ("sqlite", "postgres", "mysql", "trino", "clickhouse"):
            supported |= {"join", "union"}
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
        qualified: QualifiedSource | Sequence[QualifiedSource],
        expression: ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        purpose: str,
        expected_schema: pa.Schema,
    ) -> CompiledRead:
        """Issue a handle for one bound expression and unmodified Ibis SQL."""
        self._ensure_open()
        self._checkpoint()
        sources = (qualified,) if isinstance(qualified, QualifiedSource) else tuple(qualified)
        if not sources or any(
            item._owner is not self._token or item.binding._owner is not self._token
            for item in sources
        ):
            raise _invalid("this session's qualified sources", "empty or foreign qualification")
        if not purpose:
            raise _invalid("a nonempty read purpose", "empty purpose")
        if not isinstance(expression, ir.Expr):
            raise _invalid("an Ibis expression", type(expression).__name__)
        relation_nodes = tuple(item.binding.relation.op() for item in sources)
        expression_relations = tuple(expression.op().find(ops.Relation))
        selected_ids = {item.binding.facts.source_identity for item in sources}
        present_ids = {
            item.binding.facts.source_identity
            for item in sources
            if any(node == item.binding.relation.op() for node in expression_relations)
        }
        present_staged = tuple(
            (relation, origins)
            for relation, (origins, _name) in self._staged_relations.items()
            if any(node == relation for node in expression_relations)
        )
        present_inline = tuple(
            (relation, origins)
            for relation, origins in self._inline_relations.items()
            if any(node == relation for node in expression_relations)
        )
        for _relation, origins in (*present_staged, *present_inline):
            if not origins <= selected_ids:
                raise _invalid(
                    "only staged results of the selected sources", "foreign staged source"
                )
            present_ids.update(origins)
        if present_ids != selected_ids:
            raise _invalid("an expression derived from the bound relation", "unbound expression")
        physical_leaves = (
            ops.DatabaseTable,
            ops.UnboundTable,
            ops.InMemoryTable,
            ops.SQLQueryResult,
        )
        allowed_leaves = {
            node for relation_node in relation_nodes for node in relation_node.find(physical_leaves)
        }
        allowed_leaves.update(
            leaf for relation, _origins in present_staged for leaf in relation.find(physical_leaves)
        )
        observed_leaves = set(expression.op().find(physical_leaves))
        if (not observed_leaves and not present_inline) or not observed_leaves <= allowed_leaves:
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
        self._checkpoint()
        source_identity = "|".join(sorted({item.binding.facts.source_identity for item in sources}))
        issued = CompiledRead(
            expression,
            sql,
            tuple(supplied.items()),
            purpose,
            expected_schema,
            source_identity,
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
                source_identity,
                tuple(relation for relation, _origins in present_inline),
            ),
        )
        return issued

    def stage_derived(self, read: CompiledRead) -> tuple[ir.Table, pa.Table]:
        """Capture one exact read into an owned Ibis relation without remote writes."""
        self._ensure_open()
        stored = self._issued.get(id(read))
        if stored is None or stored[0] is not read or read._owner is not self._token:
            raise _invalid("an exact local read issued by this session", "unowned stage")
        with closing(self.batches(read, chunk_size=1024)) as stream:
            table = pa.Table.from_batches(stream, schema=stored[1].schema)
        return self.stage_calculated(read, table), table

    def stage_calculated(self, read: CompiledRead, table: pa.Table) -> ir.Table:
        """Stage an exact-schema method exchange with its issued source provenance."""
        self._ensure_open()
        stored = self._issued.get(id(read))
        if stored is None or stored[0] is not read or read._owner is not self._token:
            raise _invalid("an exact read owned by this session", "unowned calculated exchange")
        if not table.schema.equals(stored[1].schema, check_metadata=False):
            raise _invalid("the issued exchange schema", str(table.schema))
        if self.provider.name not in ("duckdb", "sqlite"):
            relation = _inline_exchange(table, self._checkpoint, self.provider.name)
            self._inline_relations[relation.op()] = frozenset(stored[1].source_identity.split("|"))
            return relation
        name = "mv_graph_" + uuid4().hex
        from marivo.datasource.engines.sqlite import owned_temporary_writes

        with (
            owned_temporary_writes(self._backend, frozenset({name, name + "_input"}))
            if self.provider.name == "sqlite"
            else nullcontext()
        ):
            try:
                if self.provider.name == "sqlite":
                    from pandas import DataFrame

                    columns: dict[str, list[object]] = {
                        field.name: [
                            value.isoformat(sep=" ", timespec="microseconds")
                            if isinstance(value, datetime)
                            else value
                            for value in table.column(field.name).to_pylist()
                        ]
                        for field in table.schema
                    }
                    # Object columns preserve nullable int64 low bits. Timestamp
                    # strings match SQLite's lexical comparison with Ibis literals.
                    data = (
                        ibis.memtable(
                            DataFrame(columns, dtype=object), schema=ibis.schema(table.schema)
                        )
                        .op()
                        .copy(name=name + "_input")
                        .to_expr()
                    )
                    try:
                        relation = self._backend.create_table(name, data, temp=True)
                    finally:
                        self._backend.drop_table(name + "_input", database="temp", force=True)
                else:
                    relation = self._backend.create_table(name, table, temp=True)
                if not isinstance(relation, ir.Table):
                    raise _invalid("one temporary Ibis relation", type(relation).__name__)
            except BaseException:
                self._backend.drop_table(name, force=True)
                raise
        self._staged_relations[relation.op()] = (
            frozenset(stored[1].source_identity.split("|")),
            name,
        )
        return relation

    def release_staged(self, relations: Sequence[ir.Table]) -> None:
        """Release only the captured relations owned by this invocation."""
        self._ensure_open()
        failure: BaseException | None = None
        for relation in reversed(tuple(relations)):
            if relation.op() in self._inline_relations:
                del self._inline_relations[relation.op()]
                continue
            owned = self._staged_relations.get(relation.op())
            if owned is None:
                if failure is None:
                    failure = _invalid(
                        "an owned staged relation", "foreign or already released stage"
                    )
                continue
            try:
                from marivo.datasource.engines.sqlite import owned_temporary_writes

                with (
                    owned_temporary_writes(self._backend, frozenset({owned[1]}))
                    if self.provider.name == "sqlite"
                    else nullcontext()
                ):
                    self._backend.drop_table(owned[1], force=True)
                del self._staged_relations[relation.op()]
            except BaseException as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure

    def batches(self, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        """Submit only the exact artifact issued by this open session."""
        self._ensure_open()
        self._checkpoint()
        stored = self._issued.get(id(read))
        if read._owner is not self._token or stored is None or stored[0] is not read:
            raise _invalid("a compiled read issued by this session", "foreign or fabricated read")
        proof = stored[1]
        if any(relation not in self._inline_relations for relation in proof.inline_relations):
            raise _invalid("live captured relations owned by this session", "released inline stage")
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
        if self._streams:
            raise _invalid("one active result per source session", "concurrent source streams")
        seconds = self._seconds_remaining()
        native_settings = (
            _clickhouse_deadline_settings(self._backend, seconds)
            if self.provider.name == "clickhouse" and seconds is not None
            else None
        )
        submission = SourceSubmission(
            proof.purpose, proof.source_identity, id(proof.expression.op()), proof.sql
        )
        self.submissions.append(submission)
        if self.provider.name == "mysql" and self._cancel_control is not None:
            with self._cancel_lock:
                self._mysql_active = submission
                self._mysql_cancel_requested = False
        if self.provider.name == "clickhouse" and self._cancel_control is not None:
            if native_settings is None:
                native_settings = {"query_id": uuid4().hex}
            native_id = native_settings["query_id"]
            assert isinstance(native_id, str)
            with self._cancel_lock:
                self._clickhouse_active = (submission, native_id)
                self._clickhouse_cancel_requested = False
                self._clickhouse_cancel_failure = None
        cursor: _Cursor | None = None
        owner_token = _CURSOR_OWNER.set(self._own_pending_cursor)
        deadline_token = _CLICKHOUSE_DEADLINE.set(native_settings)
        try:
            cursor = _native_cursor(self._backend, self.provider.name, proof.sql)
            self._checkpoint()
        except BaseException as error:
            if (
                self.provider.name == "mysql" and isinstance(error, KeyboardInterrupt)
            ) or self.provider.name == "clickhouse":
                self._request_interrupt()
            try:
                selected_cursor = (
                    cursor
                    if cursor is not None
                    else self._pending_cursor
                    if self.provider.name in {"mysql", "clickhouse"}
                    else None
                )
                self._release_cursor(selected_cursor, submission)
            except BaseException:
                if (
                    self.provider.name == "mysql"
                    and isinstance(error, KeyboardInterrupt)
                    and self._owns_backend
                ):
                    self._backend.disconnect()
                    self.mark_backend_disconnected()
                    submission.cursor_state = "closed"
                else:
                    submission.cursor_state = "close_failed"
                    raise
            finally:
                submission.state = "failed"
            raise
        finally:
            self._pending_cursor = None
            _CURSOR_OWNER.reset(owner_token)
            _CLICKHOUSE_DEADLINE.reset(deadline_token)
        if not isinstance(cursor, _Cursor):
            close = getattr(cursor, "close", None)
            if callable(close):
                close()
            submission.state = "failed"
            submission.cursor_state = "closed"
            raise _invalid("a closable native batch cursor", type(cursor).__name__)
        stream = SourceBatchStream(self, cursor, proof.schema, chunk_size, submission)
        self._streams.add(stream)
        return stream

    def _own_pending_cursor(self, cursor: _Cursor) -> None:
        self._cursor_released.clear()
        self._cursor_thread = get_ident()
        self._pending_cursor = cursor
        self._checkpoint()

    def collect_bounded(
        self,
        expression: ir.Table,
        *,
        source_identities: Sequence[str] | None = None,
        purpose: str,
        max_rows: int,
        chunk_size: int = 1024,
    ) -> pa.Table:
        """Collect a guarded authoring read through the session's exact bindings."""
        if type(max_rows) is not int or max_rows < 1:
            raise _invalid("a positive authoring row bound", repr(max_rows))
        if source_identities is None:
            relations = tuple(expression.op().find(ops.Relation))
            source_identities = tuple(
                identity
                for identity, binding in self._bound_sources.items()
                if any(binding.relation.op() == relation for relation in relations)
            )
        sources = tuple(
            self.qualify(
                self.binding_for(identity),
                PhysicalRequirement(
                    purpose, 1, frozenset({"scan", "filter", "project", "group", "count"})
                ),
            )
            for identity in source_identities
        )
        bounded = expression.limit(max_rows)
        read = self.compile(
            sources,
            bounded,
            purpose=purpose,
            expected_schema=bounded.schema().to_pyarrow(),
        )
        with closing(self.batches(read, chunk_size=chunk_size)) as stream:
            return pa.Table.from_batches(stream, schema=stream.schema)

    def _request_interrupt(self) -> None:
        """Request native cancellation; leave resource cleanup on the owner thread."""
        if self.provider.name == "clickhouse":
            with self._cancel_lock:
                active = self._clickhouse_active
                if self._closed or active is None or self._clickhouse_cancel_requested:
                    return
                submission, query_id = active
                if submission.state == "succeeded":
                    return
                self._interrupted_submissions = (submission,)
                self._clickhouse_cancel_requested = True
                control = self._cancel_control
                reader = self._clickhouse_reader
                if control is None or reader is None:
                    return
                try:
                    execute_provider_statement(
                        control,
                        self.provider,
                        "clickhouse.analysis.cancel_owned_query",
                        parameters={"id": query_id, "user": reader},
                        purpose="analysis.cancel_owned_query",
                    )
                except Exception as error:
                    observed = _backend_failure_summary(error)
                    permission_refused = observed.backend_code in {"497", "497."}
                    failure = DatasourceSourceCapabilityError(
                        message="ClickHouse could not cancel the owned reader query.",
                        expected=(
                            "same-reader cancellation with SELECT(query, query_id, user) ON system.processes"
                            if permission_refused
                            else "an available bounded control connection for the authenticated reader"
                        ),
                        received=observed.message,
                        location="datasource adapter cancellation",
                        repair=repair(
                            kind="reconnect",
                            canonical_id="test",
                            action=(
                                f"Allow the reported system.processes column permissions for connected reader {reader!r}, then retry the governed read; remote termination is unconfirmed."
                                if permission_refused
                                else f"Repair the bounded control connection for connected reader {reader!r}, then retry the governed read; remote termination is unconfirmed."
                            ),
                        ),
                    )
                    failure.__cause__ = error
                    self._clickhouse_cancel_failure = failure
                finally:
                    self.cancel_submissions = provider_statement_log(control)
            return
        if self.provider.name == "mysql":
            with self._cancel_lock:
                mysql_active = self._mysql_active
                if (
                    self._closed
                    or self._mysql_cancel_requested
                    or mysql_active is None
                    or mysql_active.state == "succeeded"
                ):
                    return
                self._interrupted_submissions = (mysql_active,)
                self._mysql_cancel_requested = True
                control = self._cancel_control
                try:
                    if control is None or self._backend.con is not self._mysql_connection:
                        return
                    execute_provider_statement(
                        control,
                        self.provider,
                        "mysql.analysis.cancel_owned_query",
                        values={"thread_id": self._cancel_thread_id},
                        purpose="analysis.cancel_owned_query",
                    )
                except Exception:
                    # Failed control does not establish remote termination.
                    pass
                finally:
                    if control is not None:
                        self.cancel_submissions = provider_statement_log(control)
                    if self._mysql_socket is not None:
                        with suppress(OSError, ValueError):
                            self._mysql_socket.shutdown(socket.SHUT_RDWR)
            return
        self._interrupted_submissions = tuple(
            submission for submission in self.submissions if submission.state == "submitted"
        )
        if self.provider.name in {"duckdb", "sqlite"}:
            native = getattr(getattr(self._backend, "con", None), "interrupt", None)
            if callable(native):
                native()
        elif self.provider.name == "postgres":
            native = getattr(getattr(self._backend, "con", None), "cancel", None)
            if callable(native):
                native()
        elif self.provider.name == "trino":
            # A first cancel can precede the driver's initial HTTP response and
            # do nothing. Retry until the execution thread releases the cursor.
            while not self._cursor_released.is_set():
                cursor = self._pending_cursor
                if cursor is None:
                    cursor = next((stream._cursor for stream in self._streams), None)
                if cursor is None:
                    return
                native = getattr(cursor, "cancel", None)
                if callable(native):
                    native()
                if get_ident() == self._cursor_thread:
                    return
                self._cursor_released.wait(0.02)

    def _synchronize_interrupt(self) -> None:
        """Wait for owned control work before native cursor cleanup."""
        with self._cancel_lock:
            failure = self._clickhouse_cancel_failure
        if failure is not None:
            raise failure

    def _release_clickhouse_query(self, submission: SourceSubmission) -> None:
        with self._cancel_lock:
            if self._clickhouse_active is not None and self._clickhouse_active[0] is submission:
                self._clickhouse_active = None

    def _release_cursor(self, cursor: _Cursor | None, submission: SourceSubmission) -> None:
        failure: BaseException | None = None
        try:
            self._synchronize_interrupt()
        except BaseException as error:
            failure = error
        try:
            if cursor is not None:
                cursor.close()
        except BaseException as close_error:
            submission.cursor_state = "close_failed"
            if (
                self.provider.name == "mysql"
                and self._mysql_cancel_requested
                and self._owns_backend
            ):
                self._backend.disconnect()
                self.mark_backend_disconnected()
                submission.cursor_state = "closed"
            elif (
                self.provider.name == "mysql"
                and self._mysql_cancel_requested
                and not self._owns_backend
                and isinstance(close_error, Exception)
                and _backend_failure_summary(close_error).backend_code in {"2006", "2013"}
            ):
                # The external connection owner must acknowledge release.
                pass
            else:
                raise
        else:
            submission.cursor_state = (
                "connection_owned" if self.provider.name == "duckdb" else "closed"
            )
        finally:
            self._cursor_released.set()
            self._release_clickhouse_query(submission)
            with self._cancel_lock:
                if self._mysql_active is submission:
                    self._mysql_active = None
        if failure is not None:
            raise failure

    def _prepare_interrupt(self) -> None:
        """Prepare the selected reader's authorized bounded control connection."""
        if self.provider.name == "clickhouse":
            if self._cancel_control is not None:
                return
            self._ensure_open()
            self._checkpoint()
            captured: object = getattr(self._backend, "_con_kwargs", None)
            if not isinstance(captured, Mapping):
                raise _invalid(
                    "the connected ClickHouse reader credentials", "connection facts unavailable"
                )
            native: object = getattr(self._backend, "con", None)
            native_headers: object = getattr(native, "headers", None)
            authentication: object = (
                native_headers.get("Authorization") if isinstance(native_headers, Mapping) else None
            )
            if not isinstance(authentication, str) or not authentication.startswith("Basic "):
                raise _invalid(
                    "the authenticated ClickHouse username from the selected reader transport",
                    "username authentication identity unavailable",
                )
            try:
                reader, separator, _password = (
                    b64decode(authentication.removeprefix("Basic "), validate=True)
                    .decode("utf-8")
                    .partition(":")
                )
            except (Base64Error, UnicodeError):
                raise _invalid(
                    "the authenticated ClickHouse username from the selected reader transport",
                    "invalid username authentication identity",
                ) from None
            if not reader or not separator:
                raise _invalid(
                    "a nonempty authenticated ClickHouse reader identity",
                    "username authentication identity unavailable",
                )
            raw_settings: object = captured.get("settings")
            settings: dict[str, object] = (
                dict(raw_settings) if isinstance(raw_settings, Mapping) else {}
            )
            settings.update({"readonly": 1, "max_execution_time": 1})
            connection_kwargs: dict[str, object] = dict(captured)
            connection_kwargs.pop("session_id", None)
            raw_headers: object = captured.get("headers")
            headers: dict[str, object] = (
                dict(raw_headers) if isinstance(raw_headers, Mapping) else {}
            )
            headers["Authorization"] = authentication
            connection_kwargs["headers"] = headers
            settings.pop("session_id", None)
            from urllib3 import PoolManager

            pool = PoolManager()
            self._cancel_pool = pool
            self._cancel_control_released = False
            control: BaseBackend | None = None
            try:
                control = self.provider.connect(
                    self.datasource.name,
                    {
                        **connection_kwargs,
                        "connect_timeout": 1,
                        "send_receive_timeout": 1,
                        "query_retries": 0,
                        "autogenerate_session_id": False,
                        "pool_mgr": pool,
                        "settings": settings,
                    },
                )
                if control.name != "clickhouse" or control.con is self._backend.con:
                    raise _invalid(
                        "an independent ClickHouse reader control", "data connection reused"
                    )
                self._checkpoint()
            except BaseException:
                try:
                    try:
                        if control is not None:
                            control.disconnect()
                    finally:
                        pool.clear()
                except BaseException:
                    raise
                else:
                    self._cancel_control_released = True
                finally:
                    self._cancel_pool = None
                raise
            self._clickhouse_reader = reader
            self._cancel_control = control
            return
        if self.provider.name != "mysql" or self._cancel_control is not None:
            return
        self._ensure_open()
        self._checkpoint()
        thread_id = self._backend.con.thread_id()
        if type(thread_id) is not int or thread_id <= 0:
            raise _invalid("a native positive MySQL connection identity", repr(thread_id))
        from dataclasses import replace

        from marivo.datasource.backends import build_backend

        control_datasource = replace(
            self.datasource,
            fields={
                **self.datasource.fields,
                "connect_timeout": 1,
                "read_timeout": 1,
                "write_timeout": 1,
            },
        )
        self._cancel_control_released = False
        control = build_backend(control_datasource, read_only=True)
        try:
            self._checkpoint()
            if not isinstance(control, BaseBackend) or control.name != "mysql":
                raise _invalid("a selected MySQL control backend", type(control).__name__)
            if control.con.thread_id() == thread_id:
                raise _invalid("a separate MySQL control connection", "data connection reused")
            # Capture ownership while the driver is idle. Its metadata methods
            # cannot run on another thread while a native query is active.
            owned_socket = socket.fromfd(
                self._backend.con.fileno(), socket.AF_INET, socket.SOCK_STREAM
            )
        except BaseException:
            control.disconnect()
            self._cancel_control_released = True
            raise
        self._cancel_thread_id = thread_id
        self._mysql_connection = self._backend.con
        self._mysql_socket = owned_socket
        self._cancel_control = control

    def interrupt(self) -> Termination:
        """Close local resources; remote termination remains unknown without proof."""
        self._request_interrupt()
        termination: Termination = (
            "local_closed" if self.provider.name in {"duckdb", "sqlite"} else "remote_unknown"
        )
        self.close()
        return termination

    def close(self) -> None:
        if self._closed:
            return
        if self.provider.name in {"clickhouse", "mysql"}:
            self._request_interrupt()
        self._closed = True
        control_error: BaseException | None = None
        try:
            with self._cancel_lock:
                if self._cancel_control is not None:
                    control = self._cancel_control
                    self.cancel_submissions = provider_statement_log(control)
                    self._cancel_control = None
                    try:
                        try:
                            control.disconnect()
                        finally:
                            if self._cancel_pool is not None:
                                pool = self._cancel_pool
                                self._cancel_pool = None
                                pool.clear()
                    except BaseException as error:
                        control_error = error
                    else:
                        self._cancel_control_released = True
                if self._mysql_socket is not None:
                    self._mysql_socket.close()
                    self._mysql_socket = None
                    self._mysql_connection = None
            for stream in tuple(self._streams):
                stream.close()
        finally:
            for _relation, (_origins, name) in tuple(self._staged_relations.items()):
                with suppress(Exception):
                    self._backend.drop_table(name, force=True)
            self._staged_relations.clear()
            self._inline_relations.clear()
            self._issued.clear()
            self._bindings_by_identity.clear()
            self._bound_sources.clear()
            if self._owns_backend and not self._backend_disconnected:
                disconnect = getattr(self._backend, "disconnect", None)
                if callable(disconnect):
                    disconnect()
                    self.mark_backend_disconnected()
        for submission in self._interrupted_submissions:
            if submission.state == "submitted":
                # SIGINT can arrive while the driver's failure handler unwinds.
                # Owner release must not leave that interrupted read pending.
                submission.state = "failed"
            submission.termination = (
                "local_closed" if self.provider.name in {"duckdb", "sqlite"} else "remote_unknown"
            )
        if control_error is not None:
            raise control_error

    def mark_backend_disconnected(self) -> None:
        """Record the connection release performed by this session's owner."""
        self._backend_disconnected = True
        for submission in self.submissions:
            submission.connection_disconnected = True
            if (
                self.provider.name == "mysql"
                and submission.cursor_state == "close_failed"
                and any(submission is active for active in self._interrupted_submissions)
            ):
                submission.cursor_state = "closed"
            if submission.state in {"failed", "closed_early"}:
                submission.termination = (
                    "local_closed"
                    if self.provider.name in {"duckdb", "sqlite"}
                    else "remote_unknown"
                )
