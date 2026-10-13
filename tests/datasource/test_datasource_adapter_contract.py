"""Expression identity, native submission and owned stream tests."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import NoReturn
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


@pytest.mark.parametrize("backend", ["sqlite", "sqlite3"])
def test_removed_backend_refuses_before_provider_loading(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource import engines

    def forbidden_load(_backend: str) -> NoReturn:
        raise AssertionError("Removed datasource reached provider loading")

    monkeypatch.setattr(engines, "_load_profile", forbidden_load)
    with pytest.raises(DatasourceBackendTypeUnsupportedError) as caught:
        provider_for(backend)
    error = caught.value
    assert error.expected and error.received == backend and error.repair is not None
    assert error.repair.candidates == ("clickhouse", "duckdb", "mysql", "postgres", "trino")


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


@pytest.fixture
def session(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[SourceSession]:
    backend_name: object = getattr(request, "param", "duckdb")
    assert isinstance(backend_name, str) and backend_name == "duckdb"
    database = tmp_path / f"source.{backend_name}"
    backend = ibis.duckdb.connect(database)
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
    assert provider_names() == ("duckdb", "trino", "mysql", "postgres", "clickhouse")
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
    relation = _inline_exchange(table, inline_backend)
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


@pytest.mark.parametrize("count", [3, 101])
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
    relation = _inline_exchange(table, inline_backend)
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
    relation = _inline_exchange(table, "duckdb")
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


def test_timestamp_precision_rejects_loss() -> None:
    with pytest.raises(DatasourceSourceCapabilityError):
        _exact_array([datetime(2026, 1, 1, 0, 0, 0, 123456)], pa.field("at", pa.timestamp("ms")))


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
assert provider_for('duckdb').name == 'duckdb'
for name in ('postgres', 'mysql', 'trino', 'clickhouse'):
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


@pytest.mark.parametrize(
    "session",
    [
        "duckdb",
    ],
    indirect=True,
)
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


@pytest.mark.parametrize(
    "session",
    [
        "duckdb",
    ],
    indirect=True,
)
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


@pytest.mark.parametrize(
    "session",
    [
        "duckdb",
    ],
    indirect=True,
)
def test_borrowed_session_releases_stream_without_closing_connection(
    session: SourceSession, tmp_path: Path
) -> None:
    borrowed = SourceSession(
        session.provider,
        session.datasource,
        session._backend,
        owns_backend=False,
        project_root=tmp_path,
    )
    try:
        stream = borrowed.batches(_read(borrowed), chunk_size=1)
        assert next(iter(stream)).num_rows == 1
    finally:
        borrowed.close()
    borrowed.close()
    assert not borrowed._streams
    assert borrowed.submissions[-1].state == "closed_early"
    assert not borrowed.submissions[-1].connection_disconnected
    with closing(session.batches(_read(session), chunk_size=1)) as stream:
        assert sum(batch.num_rows for batch in stream) == 2


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
    finally:
        session.close()


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
    if session.provider.name == "duckdb":
        assert session.qualify(bound, requirement).requirement == requirement
    else:
        with pytest.raises(DatasourceSourceCapabilityError, match="basic physical requirement"):
            session.qualify(bound, requirement)


@pytest.mark.parametrize(
    "backend",
    [
        "clickhouse",
        "mysql",
    ],
)
def test_utc_decode_requires_explicit_schema_authority(backend: str) -> None:
    value = datetime(2026, 11, 1, 5, 30, 0, 123456)
    field = pa.field("instant", pa.timestamp("us", tz="UTC"))
    result = _exact_array([value], field, backend_name=backend)
    assert result.to_pylist() == [value.replace(tzinfo=timezone.utc)]

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


def test_missing_selected_drivers_have_structured_install_repairs() -> None:
    code = """
import builtins
from marivo.datasource.adapters import provider_for
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
original = builtins.__import__
blocked_driver = None
def reject_driver(name, *args, **kwargs):
    if blocked_driver is not None and (name == blocked_driver or name.startswith(blocked_driver + '.')):
        raise ModuleNotFoundError('blocked optional driver', name=blocked_driver)
    return original(name, *args, **kwargs)
builtins.__import__ = reject_driver
for backend, blocked_driver in (
    ('postgres', 'psycopg'),
    ('mysql', 'MySQLdb'),
    ('trino', 'trino'),
    ('clickhouse', 'clickhouse_connect'),
):
    datasource = DatasourceIR(semantic_id='missing', name='missing', backend_type=backend,
        fields={'host':'127.0.0.1','database':'analysis','catalog':'iceberg','user':'reader'},
        env_refs={}, ai_context=AiContextIR(), python_symbol='missing',
        location=DatasourceSourceLocation('missing.py', 1))
    try:
        provider_for(backend).open(datasource)
    except DatasourceConnectionError as error:
        assert error.expected and error.received and error.repair
        assert 'marivo[' + backend + ']' in str(error)
    else:
        raise AssertionError('Missing selected dependency must fail before connection')
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


@pytest.mark.parametrize(
    "session",
    [
        "duckdb",
    ],
    indirect=True,
)
def test_persisted_sql_matches_native_driver_and_completes_after_consumption(
    session: SourceSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.datasource.driver_audit import native_audit

    backend_name = session.provider.name

    session.close()
    with native_audit(monkeypatch, backend_name) as audit:
        database = tmp_path / f"source.{backend_name}"
        backend = ibis.duckdb.connect(database)
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
            from duckdb import CatalogException

            backend.drop_table("facts")
            with pytest.raises(CatalogException):
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
