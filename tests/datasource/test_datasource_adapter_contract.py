"""Expression identity, native submission and owned stream tests."""

from __future__ import annotations

import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import ibis
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marivo import _execution_log
from marivo.datasource.adapters import (
    TIMESTAMP_UNIT_METADATA_KEY,
    CompiledRead,
    PhysicalRequirement,
    SourceBatchStream,
    SourceIR,
    SourceSession,
    SourceSubmission,
    _clickhouse_deadline_settings,
    _Cursor,
    _exact_array,
    _inline_exchange,
    provider_for,
    provider_names,
)
from marivo.datasource.errors import (
    DatasourceBackendTypeUnsupportedError,
    DatasourceSourceCapabilityError,
)
from marivo.datasource.ir import (
    AiContextIR,
    CsvSourceIR,
    DatasourceIR,
    DatasourceSourceLocation,
    JsonSourceIR,
    ParquetSourceIR,
    SourceParamIR,
    TableSourceIR,
)
from tests.support.execution_logs import execution_records


def _datasource(backend: str) -> DatasourceIR:
    return DatasourceIR(
        semantic_id="source",
        name="source",
        backend_type=backend,
        fields={},
        env_refs={},
        ai_context=AiContextIR(),
        python_symbol="source",
        location=DatasourceSourceLocation("source.py", 1),
    )


@pytest.fixture(params=["duckdb", "sqlite"])
def session(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[SourceSession]:
    backend_name = request.param
    database = tmp_path / f"source.{backend_name}"
    backend = (
        ibis.duckdb.connect(database) if backend_name == "duckdb" else ibis.sqlite.connect(database)
    )
    backend.raw_sql("CREATE TABLE facts (id BIGINT, amount BIGINT)")
    backend.raw_sql("INSERT INTO facts VALUES (9007199254740993, 2), (9007199254740994, 3)")
    backend.raw_sql("CREATE VIEW facts_view AS SELECT id, amount FROM facts")
    result = SourceSession(
        provider_for(backend_name), _datasource(backend_name), backend, project_root=tmp_path
    )
    try:
        yield result
    finally:
        result.close()


def _read(session: SourceSession) -> CompiledRead:
    bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
    qualified = session.qualify(
        bound, PhysicalRequirement("basic.rows", 1, frozenset({"scan", "filter", "project"}))
    )
    table = bound.relation
    expression = table.filter(table.amount >= 2).select(table.id, table.amount)
    return session.compile(
        qualified,
        expression,
        purpose="basic.rows",
        expected_schema=expression.schema().to_pyarrow(),
    )


def test_all_six_providers_resolve_without_opening() -> None:
    assert provider_names() == ("duckdb", "sqlite", "trino", "mysql", "postgres", "clickhouse")
    assert tuple(provider_for(name).name for name in provider_names()) == provider_names()
    with pytest.raises(DatasourceBackendTypeUnsupportedError):
        provider_for("unknown")


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("inline_backend", ["duckdb", "clickhouse", "trino", "mysql"])
def test_inline_exchange_preserves_exact_scalars_multiplicity_and_empty_schema(
    empty: bool, inline_backend: str
) -> None:
    table = pa.table(
        {
            "id": pa.array([9007199254740993, 9007199254740993], type=pa.int64()),
            "label": pa.array(["quote'\\value", None], type=pa.string()),
            "value": pa.array([1.0000000000000002, -0.0], type=pa.float64()),
            "decimal": pa.array([Decimal("1.000001"), None], type=pa.decimal128(18, 6)),
            "flag": pa.array([True, None], type=pa.bool_()),
            "time": pa.array(
                [datetime(2026, 8, 1, microsecond=1, tzinfo=timezone.utc), None],
                type=pa.timestamp("us", "UTC"),
            ),
        }
    )
    if empty:
        table = table.slice(0, 0)
        schema = pa.schema(
            [
                pa.field(field.name, field.type, nullable=field.name != "id")
                for field in table.schema
            ]
        )
        table = table.cast(schema)
    relation = _inline_exchange(table, lambda: None, inline_backend)
    assert relation.op() != _inline_exchange(table, lambda: None, inline_backend).op()
    if inline_backend == "trino":
        # Ibis reuses ordinal aliases incorrectly when UNNEST enters a union.
        # This check covers the actual branching validation query shape.
        branched = relation.union(relation, distinct=False)
        assert "UNNEST" not in ibis.to_sql(branched, dialect="trino").upper()
    backend = ibis.duckdb.connect()
    try:
        actual = backend.to_pyarrow(relation).cast(table.schema)
        assert actual.equals(table)
    finally:
        backend.disconnect()


@pytest.mark.parametrize("count", [3, 5, 101])
@pytest.mark.parametrize("inline_backend", ["mysql", "trino"])
def test_inline_exchange_non_power_of_two_cardinality_and_duplicate_rows(
    count: int, inline_backend: str
) -> None:
    table = pa.table(
        {
            "id": pa.array([index % 2 for index in range(count)], type=pa.int64()),
            "value": pa.array(
                [None if index % 3 == 0 else index % 2 for index in range(count)], type=pa.int64()
            ),
        }
    )
    relation = _inline_exchange(table, lambda: None, inline_backend)
    backend = ibis.duckdb.connect()
    try:
        actual = backend.to_pyarrow(relation).cast(table.schema)
        assert actual.num_rows == count
        assert actual.sort_by([("id", "ascending"), ("value", "ascending")]).equals(
            table.sort_by([("id", "ascending"), ("value", "ascending")])
        )
    finally:
        backend.disconnect()


def test_inline_exchange_provenance_admission_and_released_read_refusal(
    session: SourceSession,
) -> None:
    """Exercise admission independently of remote Runtime staging."""
    source_read = _read(session)
    stream = session.batches(source_read, chunk_size=1)
    try:
        table = pa.Table.from_batches(tuple(stream), schema=source_read.schema)
    finally:
        stream.close()
    relation = _inline_exchange(table, lambda: None, "duckdb")
    bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
    qualified = session.qualify(bound, PhysicalRequirement("basic.rows", 1, frozenset({"scan"})))
    with pytest.raises(DatasourceSourceCapabilityError, match="unbound expression"):
        session.compile(qualified, relation, purpose="basic.rows", expected_schema=table.schema)
    session._inline_relations[relation.op()] = frozenset({"facts@v1"})
    read = session.compile(qualified, relation, purpose="basic.rows", expected_schema=table.schema)
    foreign = session.bind(TableSourceIR("facts"), source_identity="facts@v2")
    foreign_qualified = session.qualify(
        foreign, PhysicalRequirement("basic.rows", 1, frozenset({"scan"}))
    )
    with pytest.raises(DatasourceSourceCapabilityError, match="foreign staged source"):
        session.compile(
            foreign_qualified, relation, purpose="basic.rows", expected_schema=table.schema
        )
    unrelated = ibis.table({"id": "int64"}, name="unrelated")
    mixed = relation.cross_join(unrelated)
    with pytest.raises(DatasourceSourceCapabilityError, match="additional physical input"):
        session.compile(
            qualified, mixed, purpose="basic.rows", expected_schema=mixed.schema().to_pyarrow()
        )
    session.release_staged((relation,))
    assert not session._inline_relations
    with pytest.raises(DatasourceSourceCapabilityError, match="released inline stage"):
        session.batches(read, chunk_size=1)
    with pytest.raises(DatasourceSourceCapabilityError, match="already released"):
        session.release_staged((relation,))


@pytest.mark.parametrize("unit", ("ms", "us", "ns"))
def test_parquet_timestamp_facts_retain_file_unit_and_engine_carrier(
    tmp_path: Path, unit: str
) -> None:
    path = tmp_path / "instants.parquet"
    pq.write_table(pa.table({"point": pa.array([1, 2, 7], type=pa.timestamp(unit))}), path)
    with SourceSession(
        provider_for("duckdb"), _datasource("duckdb"), ibis.duckdb.connect(tmp_path / "facts.db")
    ) as source:
        bound = source.bind(ParquetSourceIR(path=str(path)), source_identity="instants")
        field = bound.facts.schema.field("point")
        assert field.type == bound.relation.schema().to_pyarrow().field("point").type
        assert field.metadata == {TIMESTAMP_UNIT_METADATA_KEY: unit.encode("ascii")}
        assert pq.read_schema(path).field("point").type == pa.timestamp(unit)


def test_nested_decode_rejects_bool_coercion() -> None:
    schema_field = pa.field("identity", pa.struct([pa.field("active", pa.bool_())]))
    with pytest.raises(DatasourceSourceCapabilityError):
        _exact_array([{"active": 1}], schema_field)


@pytest.mark.parametrize("large", [False, True])
def test_trino_nested_rows_preserve_normalized_children_without_mutating_input(large: bool) -> None:
    from trino.types import NamedRowTuple

    row = NamedRowTuple(["even", 2**53 + 1], ["coordinate", "count"], ["varchar", "bigint"])
    record = pa.struct([pa.field("coordinate", pa.string()), pa.field("count", pa.int64())])
    array_type = pa.large_list(record) if large else pa.list_(record)
    field = pa.field("state", pa.struct([pa.field("groups", array_type)]))
    groups: list[object] = [row, None]
    value: dict[str, object] = {"groups": groups}
    actual = _exact_array([value, {"groups": []}, None], field, backend_name="trino")
    assert actual.to_pylist() == [
        {"groups": [{"coordinate": "even", "count": 2**53 + 1}, None]},
        {"groups": []},
        None,
    ]
    assert value["groups"] is groups and groups[0] is row


def test_nested_backend_normalization_preserves_struct_and_list_values() -> None:
    field = pa.field("groups", pa.list_(pa.struct([pa.field("count", pa.int64())])))
    original: dict[str, object] = {"count": "9007199254740993"}
    assert _exact_array([[original]], field, backend_name="postgres").to_pylist() == [
        [{"count": 2**53 + 1}]
    ]
    assert original == {"count": "9007199254740993"}
    boolean = pa.field("groups", pa.list_(pa.struct([pa.field("active", pa.bool_())])))
    assert _exact_array([[{"active": 1}]], boolean, backend_name="clickhouse").to_pylist() == [
        [{"active": True}]
    ]


@pytest.mark.parametrize("value", [True, 2**63, 1.5, None])
def test_nested_count_transport_still_rejects_invalid_values(value: object) -> None:
    field = pa.field("groups", pa.list_(pa.struct([pa.field("count", pa.int64(), nullable=False)])))
    with pytest.raises(DatasourceSourceCapabilityError):
        _exact_array([[{"count": value}]], field, backend_name="trino")


def test_nested_trino_row_names_and_float_precision_still_reject_changes() -> None:
    from trino.types import NamedRowTuple

    row = NamedRowTuple([1], ["wrong"], ["bigint"])
    field = pa.field("groups", pa.list_(pa.struct([pa.field("count", pa.int64())])))
    with pytest.raises(DatasourceSourceCapabilityError, match="row field names changed"):
        _exact_array([[row]], field, backend_name="trino")
    floats = pa.field("groups", pa.list_(pa.struct([pa.field("value", pa.float32())])))
    with pytest.raises(DatasourceSourceCapabilityError, match="value changed"):
        _exact_array([[{"value": 1.1}]], floats)


def test_clickhouse_declared_boolean_decodes_only_exact_zero_one() -> None:
    field = pa.field("accepted", pa.bool_())
    assert _exact_array([0, 1, None], field, backend_name="clickhouse").to_pylist() == [
        False,
        True,
        None,
    ]
    for value in (2, -1, 1.0, "1"):
        with pytest.raises(DatasourceSourceCapabilityError):
            _exact_array([value], field, backend_name="clickhouse")


def test_postgres_record_integer_decode_is_canonical_only() -> None:
    field = pa.field("identity", pa.struct([pa.field("id", pa.int64())]))
    assert _exact_array([("9007199254740993",)], field, backend_name="postgres").to_pylist() == [
        {"id": 9007199254740993}
    ]
    for value in ("01", "1.0", "1e0"):
        with pytest.raises(DatasourceSourceCapabilityError):
            _exact_array([(value,)], field, backend_name="postgres")


def test_timestamp_precision_and_sqlite_storage_type_reject_loss(tmp_path: Path) -> None:
    with pytest.raises(DatasourceSourceCapabilityError):
        _exact_array([datetime(2026, 1, 1, 0, 0, 0, 123456)], pa.field("at", pa.timestamp("ms")))
    backend = ibis.sqlite.connect(tmp_path / "storage.sqlite")
    backend.raw_sql("CREATE TABLE stored (id INTEGER)")
    backend.raw_sql("INSERT INTO stored VALUES ('not_an_integer')")
    with SourceSession(provider_for("sqlite"), _datasource("sqlite"), backend) as source_session:
        bound = source_session.bind(TableSourceIR("stored"), source_identity="stored")
        qualified = source_session.qualify(
            bound, PhysicalRequirement("storage", 1, frozenset({"scan"}))
        )
        expression = bound.relation.select("id")
        read = source_session.compile(
            qualified,
            expression,
            purpose="storage",
            expected_schema=expression.schema().to_pyarrow(),
        )
        with pytest.raises(DatasourceSourceCapabilityError):
            list(source_session.batches(read, chunk_size=1))
        assert source_session.submissions[-1].state == "failed"


def test_selected_provider_does_not_import_unselected_modules() -> None:
    code = """
import builtins
import sys
original_import = builtins.__import__
drivers = ('psycopg', 'pymysql', 'trino', 'clickhouse_connect')
def reject_optional_drivers(name, *args, **kwargs):
    assert not any(name == driver or name.startswith(driver + '.') for driver in drivers), name
    return original_import(name, *args, **kwargs)
builtins.__import__ = reject_optional_drivers
from marivo.datasource.adapters import provider_for
assert provider_for('sqlite').name == 'sqlite'
for name in ('duckdb', 'postgres', 'mysql', 'trino', 'clickhouse'):
    assert 'marivo.datasource.engines.' + name not in sys.modules
for name in ('postgres', 'mysql', 'trino', 'clickhouse'):
    assert provider_for(name).name == name
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_compiled_read_submits_exact_ibis_sql_and_preserves_int64(session: SourceSession) -> None:
    read = _read(session)
    stream = session.batches(read, chunk_size=1)
    assert stream.schema.equals(read.schema, check_metadata=False)
    rows = pa.Table.from_batches(stream, schema=stream.schema).to_pylist()
    assert rows == [
        {"id": 9007199254740993, "amount": 2},
        {"id": 9007199254740994, "amount": 3},
    ]
    assert [
        (item.purpose, item.source_identity, item.expression_identity, item.sql, item.state)
        for item in session.submissions
    ] == [("basic.rows", "facts@v1", id(read.expression.op()), read.sql, "succeeded")]
    assert not session._streams


def test_basic_group_and_count_use_the_bound_expression(session: SourceSession) -> None:
    bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
    qualified = session.qualify(
        bound,
        PhysicalRequirement("basic.group", 1, frozenset({"scan", "group", "count"})),
    )
    table = bound.relation
    expression = table.group_by(table.amount).aggregate(rows=table.id.count()).order_by("amount")
    read = session.compile(
        qualified,
        expression,
        purpose="basic.group",
        expected_schema=expression.schema().to_pyarrow(),
    )
    stream = session.batches(read, chunk_size=2)
    assert pa.Table.from_batches(stream, schema=stream.schema).to_pylist() == [
        {"amount": 2, "rows": 1},
        {"amount": 3, "rows": 1},
    ]


def test_local_staging_preserves_nullable_int64_low_bits(session: SourceSession) -> None:
    from marivo.datasource.adapters import _native_cursor

    read = _read(session)
    exchange = pa.Table.from_pydict(
        {"id": [9007199254740992, 9007199254740993, None], "amount": [1, 2, 3]},
        schema=read.schema,
    )
    relation = session.stage_calculated(read, exchange)
    try:
        sql = session._backend.compile(relation.order_by("amount"))
        assert isinstance(sql, str)
        cursor = _native_cursor(session._backend, session.provider.name, sql)
        try:
            assert tuple(tuple(row) for row in cursor.fetchmany(10)) == (
                (9007199254740992, 1),
                (9007199254740993, 2),
                (None, 3),
            )
        finally:
            cursor.close()
    finally:
        session.release_staged((relation,))
    assert session._staged_relations == {}


def test_local_view_is_a_separate_bound_source(session: SourceSession) -> None:
    bound = session.bind(TableSourceIR("facts_view"), source_identity="view@v1")
    qualified = session.qualify(
        bound, PhysicalRequirement("basic.view", 1, frozenset({"scan", "project"}))
    )
    expression = bound.relation.select("id").order_by("id")
    read = session.compile(
        qualified,
        expression,
        purpose="basic.view",
        expected_schema=expression.schema().to_pyarrow(),
    )
    stream = session.batches(read, chunk_size=1)
    assert pa.Table.from_batches(stream, schema=stream.schema).column("id").to_pylist() == [
        9007199254740993,
        9007199254740994,
    ]


def test_projection_identity_and_multiple_bound_tables(session: SourceSession) -> None:
    source = TableSourceIR("facts", columns=(("value", "amount"),))
    first = session.bind(source, source_identity="first")
    assert session.bind(source, source_identity="first") is first
    assert first.relation.columns == ("value",)
    with pytest.raises(DatasourceSourceCapabilityError, match="identity reused"):
        session.bind(TableSourceIR("facts"), source_identity="first")
    second = session.bind(TableSourceIR("facts"), source_identity="second")
    requirements = (
        session.qualify(first, PhysicalRequirement("authoring.preview", 1, frozenset({"scan"}))),
        session.qualify(second, PhysicalRequirement("authoring.preview", 1, frozenset({"scan"}))),
    )
    expression = first.relation.cross_join(second.relation).select(first.relation.value)
    read = session.compile(
        requirements,
        expression,
        purpose="authoring.preview",
        expected_schema=expression.schema().to_pyarrow(),
    )
    assert read.source_identity == "first|second"
    assert (
        pa.Table.from_batches(session.batches(read, chunk_size=10), schema=read.schema).num_rows
        == 4
    )


def test_duckdb_file_bindings_preserve_projection_and_json_params(tmp_path: Path) -> None:
    csv_path = tmp_path / "facts.csv"
    csv_path.write_text("id,amount\n1,2\n2,3\n")
    parquet_path = tmp_path / "facts.parquet"
    pq.write_table(pa.table({"id": [1, 2], "amount": [2, 3]}), parquet_path)
    json_path = tmp_path / "facts.json"
    json_path.write_text('[{"id":1,"amount":2},{"id":2,"amount":3}]')
    backend = ibis.duckdb.connect(tmp_path / "files.duckdb")
    with SourceSession(provider_for("duckdb"), _datasource("duckdb"), backend) as file_session:
        cases: tuple[tuple[SourceIR, dict[str, int] | None], ...] = (
            (CsvSourceIR(str(csv_path), columns=(("value", "amount"),)), None),
            (ParquetSourceIR(str(parquet_path), columns=("amount",)), None),
            (
                JsonSourceIR(
                    str(json_path),
                    columns=(("value", "amount"),),
                    query_params=(("page", SourceParamIR("page")),),
                ),
                {"page": 1},
            ),
        )
        for index, (source, params) in enumerate(cases):
            identity = f"file-{index}"
            bound = file_session.bind(source, source_identity=identity, source_params=params)
            assert (
                file_session.collect_bounded(
                    bound.relation,
                    source_identities=(identity,),
                    purpose="authoring.sample",
                    max_rows=2,
                ).num_rows
                == 2
            )
        with pytest.raises(ValueError, match="missing"):
            file_session.bind(cases[2][0], source_identity="missing-param")


def test_compiled_read_rejects_forgery_foreign_session_and_unbound_source(
    session: SourceSession, tmp_path: Path
) -> None:
    read = _read(session)
    with pytest.raises(DatasourceSourceCapabilityError):
        session.batches(replace(read, sql="SELECT 0"), chunk_size=1)
    object.__setattr__(read, "sql", "SELECT 0")
    with pytest.raises(DatasourceSourceCapabilityError):
        session.batches(read, chunk_size=1)
    object.__setattr__(read, "sql", session._issued[id(read)][1].sql)
    object.__setattr__(read, "params", ((ibis.literal(1), 2),))
    with pytest.raises(DatasourceSourceCapabilityError):
        session.batches(read, chunk_size=1)
    other_backend = ibis.duckdb.connect(tmp_path / "other.duckdb")
    other = SourceSession(provider_for("duckdb"), _datasource("duckdb"), other_backend)
    try:
        with pytest.raises(DatasourceSourceCapabilityError):
            other.batches(read, chunk_size=1)
        bound = session.bind(TableSourceIR("facts"), source_identity="facts@v2")
        qualified = session.qualify(
            bound, PhysicalRequirement("basic.count", 1, frozenset({"count"}))
        )
        unrelated = ibis.table({"id": "int64"}, name="other")
        with pytest.raises(DatasourceSourceCapabilityError):
            session.compile(
                qualified,
                unrelated.count(),
                purpose="basic.count",
                expected_schema=unrelated.count().as_table().schema().to_pyarrow(),
            )
        mixed = bound.relation.join(unrelated, bound.relation.id == unrelated.id)
        with pytest.raises(DatasourceSourceCapabilityError, match="additional physical input"):
            session.compile(
                qualified,
                mixed,
                purpose="basic.join",
                expected_schema=mixed.schema().to_pyarrow(),
            )
    finally:
        other.close()


def test_early_close_and_empty_result_keep_fixed_schema(session: SourceSession) -> None:
    read = _read(session)
    stream = session.batches(read, chunk_size=1)
    assert next(iter(stream)).num_rows == 1
    stream.close()
    assert not session._streams
    assert session.submissions[-1].state == "closed_early"
    assert session.submissions[-1].cursor_state == (
        "connection_owned" if session.provider.name == "duckdb" else "closed"
    )
    with pytest.raises(DatasourceSourceCapabilityError):
        list(stream)

    bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
    qualified = session.qualify(
        bound, PhysicalRequirement("basic.empty", 1, frozenset({"scan", "filter"}))
    )
    expression = bound.relation.filter(bound.relation.id < 0)
    empty = session.compile(
        qualified,
        expression,
        purpose="basic.empty",
        expected_schema=expression.schema().to_pyarrow(),
    )
    empty_stream = session.batches(empty, chunk_size=1)
    assert list(empty_stream) == []
    assert empty_stream.schema.equals(empty.schema, check_metadata=False)


def test_interrupt_reports_only_local_close(session: SourceSession) -> None:
    stream = session.batches(_read(session), chunk_size=1)
    assert session.interrupt() == "local_closed"
    assert not session._streams
    assert session.submissions[-1].termination == "local_closed"
    assert session.submissions[-1].connection_disconnected is True
    cleanup = execution_records(session._log_root)[-1]
    assert cleanup["event"] == "query.cleanup"
    assert cleanup["termination"] == "local_closed"
    with pytest.raises(DatasourceSourceCapabilityError):
        list(stream)


def test_fetch_failure_closes_cursor_and_records_failure(session: SourceSession) -> None:
    class FailingCursor:
        closed = False

        def fetchmany(self, _size: int) -> list[tuple[object, ...]]:
            raise RuntimeError("driver read failed")

        def close(self) -> None:
            self.closed = True

    cursor = FailingCursor()
    submission = SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL")
    session._query_logs[id(submission)] = _execution_log.QueryLog(
        "compiled SQL",
        backend=session.provider.name,
        purpose="basic.rows",
        project_root=session._log_root,
    )
    stream = SourceBatchStream(session, cursor, pa.schema([("id", pa.int64())]), 1, submission)
    session._streams.add(stream)
    with pytest.raises(RuntimeError, match="driver read failed"):
        list(stream)
    assert cursor.closed
    assert submission.state == "failed"
    assert not session._streams
    completed = execution_records(session._log_root)[-1]
    assert completed["state"] == "failed"
    assert completed["error_type"] == "RuntimeError"
    assert completed["error_message"] == "driver read failed"


@pytest.mark.parametrize(
    ("value", "arrow_type"),
    [
        (1.5, pa.int64()),
        (2**63, pa.int64()),
        (1.1, pa.float32()),
        (1.25, pa.decimal128(10, 2)),
    ],
)
def test_lossy_driver_values_fail_and_close(
    session: SourceSession, value: object, arrow_type: pa.DataType
) -> None:
    class ValueCursor:
        closed = False
        reads = 0

        def fetchmany(self, _size: int) -> list[tuple[object, ...]]:
            self.reads += 1
            return [(value,)] if self.reads == 1 else []

        def close(self) -> None:
            self.closed = True

    cursor = ValueCursor()
    submission = SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL")
    session._query_logs[id(submission)] = _execution_log.QueryLog(
        "compiled SQL",
        backend=session.provider.name,
        purpose="basic.rows",
        project_root=session._log_root,
    )
    stream = SourceBatchStream(session, cursor, pa.schema([("value", arrow_type)]), 1, submission)
    session._streams.add(stream)
    with pytest.raises(DatasourceSourceCapabilityError):
        list(stream)
    assert cursor.closed
    assert submission.state == "failed"
    assert not session._streams


def test_decimal_decode_preserves_exact_value(session: SourceSession) -> None:
    class DecimalCursor:
        reads = 0

        def fetchmany(self, _size: int) -> list[tuple[object, ...]]:
            self.reads += 1
            return [(Decimal("12.34"),)] if self.reads == 1 else []

        def close(self) -> None:
            return None

    stream = SourceBatchStream(
        session,
        DecimalCursor(),
        pa.schema([("value", pa.decimal128(10, 2))]),
        1,
        SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL"),
    )
    session._streams.add(stream)
    assert pa.Table.from_batches(stream, schema=stream.schema).to_pylist() == [
        {"value": Decimal("12.34")}
    ]


def test_stub_provider_cannot_grant_basic_route() -> None:
    # A relation stub cannot grant physical qualification for any backend.
    backend = Mock()
    backend.name = "postgres"
    backend.table.return_value = ibis.table({"id": "int64"}, name="facts")
    session = SourceSession(provider_for("postgres"), _datasource("postgres"), backend)
    try:
        bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
        with pytest.raises(DatasourceSourceCapabilityError, match="not a live Ibis backend"):
            session.qualify(bound, PhysicalRequirement("basic.rows", 1, frozenset({"scan"})))
        assert session.interrupt() == "remote_unknown"
    finally:
        session.close()


def test_trino_owner_interrupt_does_not_wait_for_its_own_cleanup() -> None:
    backend = Mock()
    backend.name = "trino"
    session = SourceSession(provider_for("trino"), _datasource("trino"), backend)
    cursor = Mock()
    session._own_pending_cursor(cursor)
    session._pending_cursor = None
    submission = SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL")
    session._query_logs[id(submission)] = _execution_log.QueryLog(
        "compiled SQL",
        backend=session.provider.name,
        purpose="basic.rows",
        project_root=session._log_root,
    )
    session.submissions.append(submission)
    stream = SourceBatchStream(session, cursor, pa.schema([("value", pa.int64())]), 1, submission)
    session._streams.add(stream)
    assert session.interrupt() == "remote_unknown"
    cursor.cancel.assert_called_once_with()
    cursor.close.assert_called_once_with()
    assert submission.state == "closed_early"
    assert submission.connection_disconnected


@pytest.mark.parametrize(
    "fault", ["max_execution_time", "timeout_before_checking_execution_speed", "mode", "missing"]
)
def test_clickhouse_deadline_refuses_unavailable_native_policy(fault: str) -> None:
    backend = Mock()
    settings = {
        "max_execution_time": SimpleNamespace(readonly=0, value="0"),
        "timeout_before_checking_execution_speed": SimpleNamespace(readonly=0, value="0"),
        "timeout_overflow_mode": SimpleNamespace(readonly=1, value="throw"),
    }
    if fault == "missing":
        del settings["max_execution_time"]
    elif fault == "mode":
        settings["timeout_overflow_mode"].value = "break"
    else:
        settings[fault].readonly = 1
    backend.con.server_settings = settings
    with pytest.raises(DatasourceSourceCapabilityError, match="owned execute deadline") as raised:
        _clickhouse_deadline_settings(backend, 0.5)
    assert raised.value.repair is not None and raised.value.repair.kind == "reconnect"
    backend.con.query_rows_stream.assert_not_called()


def test_clickhouse_deadline_uses_request_settings_without_mutating_client() -> None:
    backend = Mock()
    backend.con.server_settings = {
        "max_execution_time": SimpleNamespace(readonly=0, value="0"),
        "timeout_before_checking_execution_speed": SimpleNamespace(readonly=0, value="10"),
        "timeout_overflow_mode": SimpleNamespace(readonly=1, value="throw"),
    }
    backend.con.params = {"max_execution_time": "99", "unrelated": "preserved"}
    first = _clickhouse_deadline_settings(backend, 0.75)
    second = _clickhouse_deadline_settings(backend, 0.25)
    assert first["max_execution_time"] == 0.75 and second["max_execution_time"] == 0.25
    assert first["query_id"] != second["query_id"]
    assert first["timeout_before_checking_execution_speed"] == 0
    assert first["timeout_overflow_mode"] == "throw"
    assert backend.con.params == {"max_execution_time": "99", "unrelated": "preserved"}


@pytest.mark.parametrize("close_fails", [False, True])
def test_post_submission_checkpoint_failure_releases_native_cursor(
    session: SourceSession, monkeypatch: pytest.MonkeyPatch, close_fails: bool
) -> None:
    read = _read(session)
    cursor = Mock()
    if close_fails:
        cursor.close.side_effect = RuntimeError("cursor close failed")

    def submitted_cursor(*_args: object) -> Mock:
        from marivo.datasource.adapters import _log_native_submission

        _log_native_submission()
        return cursor

    monkeypatch.setattr("marivo.datasource.adapters._native_cursor", submitted_cursor)
    checks = 0

    def checkpoint() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("deadline exceeded")

    session._checkpoint = checkpoint
    with pytest.raises(
        RuntimeError, match="cursor close failed" if close_fails else "deadline exceeded"
    ):
        session.batches(read, chunk_size=1)
    cursor.close.assert_called_once_with()
    assert session.submissions[-1].state == "failed"
    assert session.submissions[-1].cursor_state == (
        "close_failed"
        if close_fails
        else "connection_owned"
        if session.provider.name == "duckdb"
        else "closed"
    )
    assert session._cursor_released.is_set()
    completed = execution_records(session._log_root)[-1]
    assert completed["event"] == "query.completed"
    assert completed["state"] == "failed"
    assert completed["cursor_state"] == session.submissions[-1].cursor_state


def test_session_rejects_backend_from_another_provider(tmp_path: Path) -> None:
    backend = ibis.duckdb.connect(tmp_path / "other.duckdb")
    try:
        with pytest.raises(DatasourceSourceCapabilityError):
            SourceSession(provider_for("postgres"), _datasource("postgres"), backend)
    finally:
        backend.disconnect()


def test_join_union_require_explicit_local_engine_qualification(session: SourceSession) -> None:
    bound = session.bind(TableSourceIR("facts"), source_identity="source_operation-operations")
    requirement = PhysicalRequirement(
        "source_operation.correspondence", 1, frozenset({"scan", "join", "union"})
    )
    if session.provider.name in ("duckdb", "sqlite"):
        assert session.qualify(bound, requirement).requirement == requirement
    else:
        with pytest.raises(DatasourceSourceCapabilityError, match="basic physical requirement"):
            session.qualify(bound, requirement)


@pytest.mark.parametrize("backend", ["clickhouse", "mysql", "sqlite"])
def test_utc_decode_requires_explicit_schema_authority(backend: str) -> None:
    value = datetime(2026, 11, 1, 5, 30, 0, 123456)
    field = pa.field("instant", pa.timestamp("us", tz="UTC"))
    result = _exact_array([value], field, backend_name=backend)
    assert result.to_pylist() == [value.replace(tzinfo=timezone.utc)]
    if backend == "sqlite":
        assert _exact_array([value.isoformat()], field, backend_name=backend).to_pylist() == [
            value.replace(tzinfo=timezone.utc)
        ]
    for zone in ("America/New_York", "+08:00"):
        with pytest.raises(DatasourceSourceCapabilityError):
            _exact_array(
                [value], pa.field("instant", pa.timestamp("us", tz=zone)), backend_name=backend
            )
    with pytest.raises(DatasourceSourceCapabilityError):
        _exact_array([value], field, backend_name="postgres")


def test_cursor_close_failure_cannot_claim_success(session: SourceSession) -> None:
    class CloseFailure:
        def fetchmany(self, size: int) -> list[tuple[object, ...]]:
            return []

        def close(self) -> None:
            raise OSError("cursor release failed")

    submission = SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL")
    session._query_logs[id(submission)] = _execution_log.QueryLog(
        "compiled SQL",
        backend=session.provider.name,
        purpose="basic.rows",
        project_root=session._log_root,
    )
    stream = SourceBatchStream(
        session, CloseFailure(), pa.schema([("value", pa.int64())]), 1, submission
    )
    session._streams.add(stream)
    with pytest.raises(OSError, match="cursor release failed"):
        list(stream)
    assert submission.state == "failed"
    assert submission.cursor_state == "close_failed"
    assert not session._streams
    completed = execution_records(session._log_root)[-1]
    assert completed["state"] == "failed"
    assert completed["error_type"] == "OSError"


@pytest.mark.parametrize(
    "backend,driver",
    [
        ("postgres", "psycopg"),
        ("mysql", "MySQLdb"),
        ("trino", "trino"),
        ("clickhouse", "clickhouse_connect"),
    ],
)
def test_missing_selected_driver_has_structured_install_repair(backend: str, driver: str) -> None:
    code = """
import builtins
from marivo.datasource.adapters import provider_for
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
original = builtins.__import__
def reject_driver(name, *args, **kwargs):
    if name == DRIVER or name.startswith(DRIVER + '.'):
        raise ModuleNotFoundError('blocked optional driver', name=DRIVER)
    return original(name, *args, **kwargs)
builtins.__import__ = reject_driver
datasource = DatasourceIR(semantic_id='missing', name='missing', backend_type=BACKEND,
    fields={'host':'127.0.0.1','database':'analysis','catalog':'iceberg','user':'reader'},
    env_refs={}, ai_context=AiContextIR(), python_symbol='missing',
    location=DatasourceSourceLocation('missing.py', 1))
try:
    provider_for(BACKEND).open(datasource)
except DatasourceConnectionError as error:
    assert error.expected and error.received and error.repair
    assert 'marivo[' + BACKEND + ']' in str(error)
else:
    raise AssertionError('Missing selected dependency must fail before connection')
"""
    subprocess.run(
        [sys.executable, "-c", f"BACKEND={backend!r}\nDRIVER={driver!r}\n" + code],
        check=True,
        capture_output=True,
        text=True,
    )


def test_sqlite_date_carriers_are_exact() -> None:
    field = pa.field("left_start", pa.date32())
    assert _exact_array(["2026-08-01", None], field, backend_name="sqlite").to_pylist() == [
        date(2026, 8, 1),
        None,
    ]
    for value in ("20260801", "2026-W31-6", "2026-02-30", "2026-08-01 00:00:00"):
        with pytest.raises(DatasourceSourceCapabilityError):
            _exact_array([value], field, backend_name="sqlite")


@pytest.mark.parametrize("fault", ["none", "foreign", "unavailable", "failed"])
def test_mysql_interrupt_targets_only_the_prepared_owned_connection(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from marivo.datasource import adapters

    backend = Mock()
    backend.name = "mysql"
    backend.con.thread_id.side_effect = RuntimeError("active driver metadata is unavailable")
    backend.con.fileno.side_effect = RuntimeError("active driver metadata is unavailable")
    control = Mock()
    control.name = "mysql"
    session = SourceSession(provider_for("mysql"), _datasource("mysql"), backend)
    session._cancel_thread_id = 123
    session._mysql_connection = object() if fault == "foreign" else backend.con
    session._cancel_control = None if fault == "unavailable" else control
    session._cancel_control_released = fault == "unavailable"
    submission = SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL")
    session._query_logs[id(submission)] = _execution_log.QueryLog(
        "compiled SQL",
        backend=session.provider.name,
        purpose="basic.rows",
        project_root=session._log_root,
    )
    session.submissions.append(submission)
    session._mysql_active = submission
    execute = Mock(side_effect=RuntimeError("control failed") if fault == "failed" else None)
    monkeypatch.setattr(adapters, "execute_provider_statement", execute)
    monkeypatch.setattr(adapters, "provider_statement_log", lambda _backend: ())
    owned_socket = Mock()
    session._mysql_socket = owned_socket

    session._request_interrupt()
    captured = session._interrupted_submissions
    session._request_interrupt()
    assert session._interrupted_submissions == captured == (submission,)
    assert session.interrupt() == "remote_unknown"
    if fault in {"foreign", "unavailable"}:
        execute.assert_not_called()
    else:
        execute.assert_called_once_with(
            control,
            session.provider,
            "mysql.analysis.cancel_owned_query",
            values={"thread_id": 123},
            purpose="analysis.cancel_owned_query",
        )
    backend.con.thread_id.assert_not_called()
    backend.con.fileno.assert_not_called()
    owned_socket.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    owned_socket.close.assert_called_once_with()
    backend.disconnect.assert_called_once_with()
    if fault != "unavailable":
        control.disconnect.assert_called_once_with()
    assert submission.termination == "remote_unknown"
    assert submission.state == "failed"
    assert submission.connection_disconnected
    assert session._cancel_control is None


@pytest.mark.parametrize("state", ["failed", "closed_early", "succeeded", "unowned"])
def test_mysql_interrupt_owns_unreleased_read_after_state_change(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    from marivo.datasource import adapters

    backend = Mock()
    backend.name = "mysql"
    backend.con.thread_id.return_value = 123
    session = SourceSession(provider_for("mysql"), _datasource("mysql"), backend)
    session._cancel_thread_id = 123
    session._mysql_connection = backend.con
    session._cancel_control = Mock()
    submission = SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL")
    session._query_logs[id(submission)] = _execution_log.QueryLog(
        "compiled SQL",
        backend=session.provider.name,
        purpose="basic.rows",
        project_root=session._log_root,
    )
    session.submissions.append(submission)
    if state == "failed":
        submission.state = "failed"
    elif state == "closed_early":
        submission.state = "closed_early"
    elif state == "succeeded":
        submission.state = "succeeded"
    if state != "unowned":
        session._mysql_active = submission
    execute = Mock()
    monkeypatch.setattr(adapters, "execute_provider_statement", execute)
    monkeypatch.setattr(adapters, "provider_statement_log", lambda _backend: ())
    monkeypatch.setattr(socket, "fromfd", Mock(side_effect=OSError("unit transport unavailable")))
    session._request_interrupt()
    session._request_interrupt()
    if state in {"failed", "closed_early"}:
        execute.assert_called_once()
        assert session._interrupted_submissions == (submission,)
    else:
        execute.assert_not_called()
        assert session._interrupted_submissions == ()
    session._release_cursor(None, submission)
    assert session._mysql_active is None
    session.close()


@pytest.mark.parametrize("code", [2006, 2013, 2014])
def test_borrowed_mysql_interrupted_cursor_waits_for_external_release_ack(code: int) -> None:
    backend = Mock()
    backend.name = "mysql"
    session = SourceSession(
        provider_for("mysql"), _datasource("mysql"), backend, owns_backend=False
    )
    submission = SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL", state="failed")
    session.submissions.append(submission)
    session._mysql_active = submission
    session._mysql_cancel_requested = True
    session._interrupted_submissions = (submission,)
    cursor = Mock()
    cursor.close.side_effect = RuntimeError(code, "native cursor drain failed")
    if code == 2014:
        with pytest.raises(RuntimeError):
            session._release_cursor(cursor, submission)
    else:
        session._release_cursor(cursor, submission)
    assert submission.cursor_state == "close_failed"
    assert not submission.connection_disconnected
    assert session._mysql_active is None
    backend.disconnect.assert_not_called()
    session.close()
    assert submission.cursor_state == "close_failed"
    assert submission.termination == "remote_unknown"
    backend.disconnect.assert_not_called()
    session.mark_backend_disconnected()
    assert submission.cursor_state == "closed" and submission.connection_disconnected
    assert submission.termination == "remote_unknown"


@pytest.mark.parametrize("owns_backend", [True, False])
def test_mysql_interrupted_cursor_drain_requires_owned_disconnect(
    session: SourceSession, monkeypatch: pytest.MonkeyPatch, owns_backend: bool
) -> None:
    from marivo.datasource import adapters

    read = _read(session)
    session.provider = provider_for("mysql")
    session._owns_backend = owns_backend
    cursor = Mock()
    cursor.close.side_effect = RuntimeError("drain failed")
    disconnect = Mock(wraps=session._backend.disconnect)
    monkeypatch.setattr(session._backend, "disconnect", disconnect)
    monkeypatch.setattr(session, "_request_interrupt", Mock())
    monkeypatch.setattr(session, "_synchronize_interrupt", Mock())

    def interrupted_read(_backend: object, _name: str, _sql: str) -> None:
        session._own_pending_cursor(cursor)
        raise KeyboardInterrupt("caller interrupted")

    monkeypatch.setattr(adapters, "_native_cursor", interrupted_read)
    try:
        with pytest.raises(
            KeyboardInterrupt if owns_backend else RuntimeError,
            match="caller interrupted" if owns_backend else "drain failed",
        ):
            session.batches(read, chunk_size=1)
        cursor.close.assert_called_once_with()
        assert session.submissions[-1].state == "failed"
        assert session._cursor_released.is_set() and session._pending_cursor is None
        if owns_backend:
            assert session.submissions[-1].cursor_state == "closed"
            assert session.submissions[-1].connection_disconnected
            session.close()
            disconnect.assert_called_once_with()
        else:
            assert session.submissions[-1].cursor_state == "close_failed"
            assert not session.submissions[-1].connection_disconnected
            disconnect.assert_not_called()
    finally:
        session._owns_backend = True


def test_mysql_control_close_failure_still_releases_owned_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource import adapters

    backend = Mock()
    backend.name = "mysql"
    control = Mock()
    control.disconnect.side_effect = RuntimeError("control close failed")
    monkeypatch.setattr(adapters, "provider_statement_log", lambda _backend: ())
    session = SourceSession(provider_for("mysql"), _datasource("mysql"), backend)
    session._cancel_control = control
    session._cancel_control_released = False
    cursor = Mock()
    submission = SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL")
    session._query_logs[id(submission)] = _execution_log.QueryLog(
        "compiled SQL",
        backend=session.provider.name,
        purpose="basic.rows",
        project_root=session._log_root,
    )
    session.submissions.append(submission)
    stream = SourceBatchStream(session, cursor, pa.schema([("value", pa.int64())]), 1, submission)
    session._streams.add(stream)

    with pytest.raises(RuntimeError, match="control close failed"):
        session.close()
    cursor.close.assert_called_once_with()
    backend.disconnect.assert_called_once_with()
    assert submission.cursor_state == "closed"
    assert submission.connection_disconnected
    assert session._cancel_control is None
    assert not session._cancel_control_released


@pytest.mark.parametrize("fault", ["none", "reused", "expired", "connect_failed"])
def test_mysql_cancel_preparation_is_bounded_and_closes_rejected_control(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from ibis.backends import BaseBackend

    from marivo.datasource import backends

    backend = Mock()
    backend.name = "mysql"
    backend.con.thread_id.return_value = 123
    backend.con.fileno.return_value = 17
    control = Mock(spec=BaseBackend)
    control.name = "mysql"
    control.con = Mock()
    control.con.thread_id.return_value = 123 if fault == "reused" else 456
    build = Mock(
        return_value=control,
        side_effect=RuntimeError("connect failed") if fault == "connect_failed" else None,
    )
    monkeypatch.setattr(backends, "build_backend", build)
    owned_socket = Mock()
    fromfd = Mock(return_value=owned_socket)
    monkeypatch.setattr(socket, "fromfd", fromfd)
    session = SourceSession(provider_for("mysql"), _datasource("mysql"), backend)
    checkpoint = Mock(side_effect=[None, RuntimeError("expired")] if fault == "expired" else None)
    session._checkpoint = checkpoint
    try:
        if fault == "none":
            session._prepare_interrupt()
            assert session._cancel_control is control
            assert session._cancel_thread_id == 123
            assert session._mysql_connection is backend.con
            assert session._mysql_socket is owned_socket
            fromfd.assert_called_once_with(17, socket.AF_INET, socket.SOCK_STREAM)
            session._prepare_interrupt()
        else:
            with pytest.raises((RuntimeError, DatasourceSourceCapabilityError)):
                session._prepare_interrupt()
            assert session._cancel_control is None
            assert session._cancel_control_released == (fault != "connect_failed")
            if fault != "connect_failed":
                control.disconnect.assert_called_once_with()
        build.assert_called_once_with(
            replace(
                session.datasource,
                fields={"connect_timeout": 1, "read_timeout": 1, "write_timeout": 1},
            ),
            read_only=True,
        )
        assert session.submissions == []
    finally:
        session.close()
    if fault == "none":
        owned_socket.close.assert_called_once_with()
    else:
        fromfd.assert_not_called()


def test_persisted_sql_matches_native_driver_and_completes_after_consumption(
    session: SourceSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.datasource.driver_audit import native_audit

    backend_name = session.provider.name
    if backend_name == "sqlite":
        from sqlite3 import Connection

        connection: object = getattr(session._backend, "con", None)
        assert isinstance(connection, Connection)
        connection.commit()
    session.close()
    with native_audit(monkeypatch, backend_name) as audit:
        database = tmp_path / f"source.{backend_name}"
        backend = (
            ibis.duckdb.connect(database)
            if backend_name == "duckdb"
            else ibis.sqlite.connect(database)
        )
        with SourceSession(
            provider_for(backend_name), _datasource(backend_name), backend, project_root=tmp_path
        ) as observed:
            read = _read(observed)
            assert not [r for r in execution_records(tmp_path) if r.get("purpose") == "basic.rows"]
            stream = observed.batches(read, chunk_size=1)
            iterator = iter(stream)
            assert next(iterator).num_rows == 1
            records = [r for r in execution_records(tmp_path) if r.get("purpose") == "basic.rows"]
            assert len(records) == 1 and records[0]["event"] == "query.submitted"
            assert sum(batch.num_rows for batch in iterator) == 1
            from sqlite3 import OperationalError

            from duckdb import CatalogException

            backend.drop_table("facts")
            with pytest.raises((CatalogException, OperationalError)):
                observed.batches(read, chunk_size=1)
    native = [item for item in audit.submissions if item.category == "governed_ibis"]
    records = [r for r in execution_records(tmp_path) if r.get("purpose") == "basic.rows"]
    assert len(native) == 2
    assert records[0]["sql"] == native[0].sql == records[2]["sql"] == native[1].sql == read.sql
    assert records[1]["state"] == "succeeded"
    assert records[1]["consumed_rows"] == 2 and records[1]["consumed_arrow_bytes"] == 32
    assert records[3]["state"] == native[1].state == "failed"
    assert records[3]["error_type"] == native[1].error_type
    assert records[3]["consumed_rows"] == records[3]["consumed_arrow_bytes"] == 0


def test_native_owner_checkpoint_failure_is_not_logged_as_a_submission(tmp_path: Path) -> None:
    from ibis.backends import BaseBackend

    from marivo.datasource.adapters import _BEFORE_SUBMIT, _CURSOR_OWNER, _native_cursor

    cursor = Mock()
    cursor.fetchmany = Mock(return_value=[])
    cursor.close = Mock()
    connection = Mock()
    connection.cursor.return_value = cursor
    backend: BaseBackend = Mock(spec=BaseBackend)
    backend.con = connection

    def checkpoint(_cursor: _Cursor) -> None:
        raise TimeoutError("expired before native execute")

    def submitted() -> None:
        _execution_log.QueryLog(
            "SELECT 1", backend="trino", purpose="test.prepared", project_root=tmp_path
        )

    owner_token = _CURSOR_OWNER.set(checkpoint)
    log_token = _BEFORE_SUBMIT.set(submitted)
    try:
        with pytest.raises(TimeoutError, match="expired before native execute"):
            _native_cursor(backend, "trino", "SELECT 1")
    finally:
        _CURSOR_OWNER.reset(owner_token)
        _BEFORE_SUBMIT.reset(log_token)
    cursor.execute.assert_not_called()
    cursor.close.assert_called_once_with()
    assert execution_records(tmp_path) == []


def test_early_close_diagnostics_report_only_consumed_rows(
    session: SourceSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = session.batches(_read(session), chunk_size=1)
    assert next(iter(stream)).num_rows == 1
    release = session._release_cursor

    def audited_release(cursor: _Cursor | None, submission: SourceSubmission) -> None:
        assert execution_records(session._log_root)[-1]["event"] == "query.submitted"
        release(cursor, submission)

    monkeypatch.setattr(session, "_release_cursor", audited_release)
    stream.close()
    completed = execution_records(session._log_root)[-1]
    assert completed["state"] == "closed_early"
    assert completed["consumed_rows"] == 1 and completed["consumed_arrow_bytes"] == 16


def test_cancellation_worker_preserves_query_context_across_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from threading import Thread

    root = tmp_path / "source"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    backend = Mock()
    backend.name = "mysql"
    control = Mock()
    control.name = "mysql"
    cursor = Mock()
    cursor.description = ()
    cursor.fetchall.return_value = []
    control.raw_sql.return_value = cursor
    control._marivo_provider_submissions = []
    session = SourceSession(provider_for("mysql"), _datasource("mysql"), backend, project_root=root)
    with _execution_log.scope(root, operation_id="operation", session_id="session", run_id="run"):
        query = _execution_log.QueryLog("SELECT 1", backend="mysql", purpose="basic.rows")
    submission = SourceSubmission("basic.rows", "facts@v1", 1, "SELECT 1")
    session.submissions.append(submission)
    session._query_logs[id(submission)] = query
    session._mysql_active = submission
    session._mysql_connection = backend.con
    session._cancel_thread_id = 123
    session._cancel_control = control
    worker = Thread(target=session._request_interrupt)
    try:
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        control.raw_sql.assert_called_once_with("KILL QUERY 123")
        cursor.close.assert_called_once_with()
        records = [
            r for r in execution_records(root) if r.get("purpose") == "analysis.cancel_owned_query"
        ]
        assert [r["event"] for r in records] == ["query.submitted", "query.completed"]
        assert records[0]["sql"] == "KILL QUERY 123"
        assert records[0]["parent_query_id"] == query.context.fields["query_id"]
        assert records[0]["query_id"] != records[0]["parent_query_id"]
        assert records[0]["operation_id"] == "operation"
        assert records[0]["session_id"] == "session" and records[0]["run_id"] == "run"
        assert "interrupt_requested" not in records[0]
        assert not (elsewhere / ".marivo").exists()
    finally:
        query.finish("closed_early")
        session.close()
