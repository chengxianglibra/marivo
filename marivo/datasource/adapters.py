"""Owned, expression-only source reads for governed datasource consumers.

Provider selection is pure. Opening a selected session is the only step that
loads its optional backend driver. The session owns every issued read and cursor.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, nullcontext, suppress
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from importlib import import_module
from itertools import islice
from math import isfinite
from typing import Literal, Protocol, cast, runtime_checkable
from uuid import uuid4

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
    QueryParamScalar,
    QueryParamScalarList,
    TableSourceIR,
)
from marivo.datasource.table_source import table_source_expression

DURATION_UNIT_METADATA_KEY = b"marivo:duration_unit"

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


@dataclass(slots=True)
class SourceSubmission:
    purpose: str
    source_identity: str
    expression_identity: int
    sql: str
    state: Literal["submitted", "succeeded", "failed", "closed_early"] = "submitted"
    cursor_state: Literal["open", "closed", "connection_owned"] = "open"
    connection_disconnected: bool = False
    termination: Termination | None = None


@runtime_checkable
class _Cursor(Protocol):
    def fetchmany(self, size: int) -> Sequence[Sequence[object]]: ...
    def close(self) -> None: ...


class _ClickHouseNativeStream(Protocol):
    def __enter__(self) -> Iterator[Sequence[object]]: ...
    def __exit__(self, *args: object) -> object: ...


class _ClickHouseCursor:
    """Own the ClickHouse row stream returned for one compiled expression."""

    def __init__(self, native: _ClickHouseNativeStream):
        self._native = native
        self._rows = iter(native.__enter__())
        self._closed = False

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
        return _ClickHouseCursor(stream(sql))
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
    try:
        cursor.execute(sql)
    except BaseException:
        cursor.close()
        if converter is not None and original is not None:
            converter.update(original)
        raise
    if not isinstance(cursor, _Cursor):
        cursor.close()
        if converter is not None and original is not None:
            converter.update(original)
        raise _invalid("a closable native batch cursor", type(cursor).__name__)
    return (
        _MySQLCursor(cursor, converter, original)
        if converter is not None and original is not None
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
            if backend_name == "sqlite" and type(value) is int and value in (0, 1):
                value = bool(value)
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
            valid = type(value) is datetime and (value.tzinfo is not None) == (
                arrow_type.tz is not None
            )
        elif pa.types.is_date(arrow_type):
            if backend_name == "mysql" and type(value) is str:
                try:
                    value = date.fromisoformat(value)
                except ValueError as exc:
                    raise _invalid(
                        f"exact {arrow_type} value for {field.name}", "invalid MySQL date"
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
            while rows := self._cursor.fetchmany(self._chunk_size):
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
                    self._submission.cursor_state = (
                        "connection_owned" if isinstance(self._cursor, _DuckDBCursor) else "closed"
                    )
                    self._session._streams.discard(self)


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
        self._token = object()
        self._issued: dict[int, tuple[CompiledRead, _IssuedRead]] = {}
        self._bindings_by_identity: dict[str, tuple[SourceIR, dict[str, object]]] = {}
        self._bound_sources: dict[str, BoundSource] = {}
        self._staged_relations: dict[ops.Relation, tuple[frozenset[str], str]] = {}
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
        elif self.provider.name == "sqlite":
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
        for _relation, origins in present_staged:
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
            ),
        )
        return issued

    def stage_derived(self, read: CompiledRead) -> tuple[ir.Table, pa.Table]:
        """Capture one exact local read into an owned temporary Ibis relation."""
        self._ensure_open()
        stored = self._issued.get(id(read))
        if (
            self.provider.name not in ("duckdb", "sqlite")
            or stored is None
            or stored[0] is not read
            or read._owner is not self._token
        ):
            raise _invalid("an exact local read issued by this session", "unowned stage")
        with closing(self.batches(read, chunk_size=1024)) as stream:
            table = pa.Table.from_batches(stream, schema=stored[1].schema)
        name = "mv_graph_" + uuid4().hex
        from marivo.datasource.engines.sqlite import owned_temporary_writes

        with (
            owned_temporary_writes(self._backend, frozenset({name, name + "_input"}))
            if self.provider.name == "sqlite"
            else nullcontext()
        ):
            try:
                if self.provider.name == "sqlite":
                    columns: dict[str, list[object]] = {
                        field.name: [
                            value.isoformat(sep=" ", timespec="microseconds")
                            if isinstance(value, datetime)
                            else value
                            for value in table.column(field.name).to_pylist()
                        ]
                        for field in table.schema
                    }
                    # SQLite compares timestamp storage lexically. Ibis registers
                    # pandas Timestamp with a T separator, unlike its literals.
                    data = (
                        ibis.memtable(columns, schema=ibis.schema(table.schema))
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
        return relation, table

    def release_staged(self, relations: Sequence[ir.Table]) -> None:
        """Drop only the temporary relations owned by this invocation."""
        self._ensure_open()
        failure: BaseException | None = None
        for relation in reversed(tuple(relations)):
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
        if self._streams:
            raise _invalid("one active result per source session", "concurrent source streams")
        submission = SourceSubmission(
            proof.purpose, proof.source_identity, id(proof.expression.op()), proof.sql
        )
        self.submissions.append(submission)
        try:
            cursor = _native_cursor(self._backend, self.provider.name, proof.sql)
        except BaseException:
            submission.state = "failed"
            submission.cursor_state = (
                "connection_owned" if self.provider.name == "duckdb" else "closed"
            )
            raise
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

    def interrupt(self) -> Termination:
        """Close local resources; remote termination remains unknown without proof."""
        active = tuple(stream._submission for stream in self._streams)
        termination: Termination = (
            "local_closed" if self.provider.name in {"duckdb", "sqlite"} else "remote_unknown"
        )
        self.close()
        for submission in active:
            submission.termination = termination
        return termination

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            for stream in tuple(self._streams):
                stream.close()
        finally:
            for _relation, (_origins, name) in tuple(self._staged_relations.items()):
                with suppress(Exception):
                    self._backend.drop_table(name, force=True)
            self._staged_relations.clear()
            self._issued.clear()
            self._bindings_by_identity.clear()
            self._bound_sources.clear()
            if self._owns_backend:
                disconnect = getattr(self._backend, "disconnect", None)
                if callable(disconnect):
                    disconnect()
                    self.mark_backend_disconnected()

    def mark_backend_disconnected(self) -> None:
        """Record the connection release performed by this session's owner."""
        for submission in self.submissions:
            submission.connection_disconnected = True
