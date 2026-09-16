"""Opt-in real SELECT-only ClickHouse Group A Dataset acceptance."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from uuid import uuid4

import ibis.expr.types as ir
import pytest
from clickhouse_connect.driver.common import StreamContext
from clickhouse_connect.driver.httpclient import HttpClient

from marivo.analysis import time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.clickhouse_execution import ClickHouseExecutionAdapter
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import Parameter, Statement
from marivo.analysis.materialization.scalar_sql_execution import ScalarBatchStream
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_acceptance_capture import counts
from tests.lazy_scalar_source_fixtures import (
    registry_for,
)
from tests.multisource_environment import clickhouse_analysis as clickhouse

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1", reason="opt-in ClickHouse service"
    ),
]
REVENUE = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")


def _watch_primary_streams(
    monkeypatch: pytest.MonkeyPatch, receive: Callable[[StreamContext], None]
) -> None:
    """Observe only responses submitted by the primary execution role."""
    original_batches = ClickHouseExecutionAdapter.batches
    original_query = HttpClient.query_rows_stream
    in_primary = False

    def batches(
        adapter: ClickHouseExecutionAdapter,
        value: Statement | ir.Expr,
        *,
        chunk_size: int,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        role: str = "query",
        record: Callable[[str, str], None] | None = None,
    ) -> ScalarBatchStream:
        nonlocal in_primary
        in_primary = role == "primary" or (isinstance(value, Statement) and value.role == "primary")
        try:
            return original_batches(
                adapter, value, chunk_size=chunk_size, params=params, role=role, record=record
            )
        finally:
            in_primary = False

    def query(client: HttpClient, sql: str, **kwargs: object) -> StreamContext:
        stream = original_query(client, sql, **kwargs)
        if in_primary:
            receive(stream)
        return stream

    monkeypatch.setattr(ClickHouseExecutionAdapter, "batches", batches)
    monkeypatch.setattr(HttpClient, "query_rows_stream", query)


@pytest.fixture
def source_table(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("MARIVO_TEST_CLICKHOUSE_PASSWORD", clickhouse.password())
    table = "dataset_" + uuid4().hex
    with clickhouse.connection(admin=True) as con:
        con.command(
            f'CREATE TABLE {table}(id Nullable(Int64), tenant Nullable(String), customer_id Nullable(Int64), order_id Nullable(Int64), amount Nullable(Float64), weight Nullable(Float64), region Nullable(String), channel Nullable(String), day Nullable(Date), start Nullable(Date), "end" Nullable(Date)) ENGINE=MergeTree ORDER BY tuple()'
        )
        con.command(
            f"INSERT INTO {table}(id,amount,channel,day) VALUES "
            "(1,10.25,'a',DATE '2026-02-02'),(2,20.5,'a',DATE '2026-02-03'),"
            "(3,30.75,'b',DATE '2026-02-04'),(4,NULL,'b',DATE '2026-02-05'),"
            "(5,-2.0,'c',DATE '2026-02-06'),(6,999.0,'outside',DATE '2026-03-01')"
        )
        try:
            yield table
        finally:
            con.command(f"DROP TABLE IF EXISTS {table}")


@pytest.mark.parametrize("kind", ["grouped", "population", "entity", "reducers"])
def test_group_a(tmp_path: Path, source_table: str, kind: str) -> None:
    registry, sidecar = registry_for(tmp_path / "unused", engine="clickhouse", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, kind)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    if kind == "population":
        target = sources.population(ref.entity("sales.orders"))
    elif kind == "entity":
        target = sources.observe(REVENUE)
    elif kind == "reducers":
        target = sources.observe(
            (
                REVENUE,
                ref.metric("sales.order_count"),
                ref.metric("sales.min_amount"),
                ref.metric("sales.max_amount"),
            )
        ).aggregate()
    else:
        grouped = (
            sources.observe(REVENUE, time_scope=time_scope(start="2026-02-01", end="2026-03-01"))
            .with_dimensions(CHANNEL)
            .aggregate()
            .where(gt(REVENUE, 0))
        )
        target = grouped.rank(grouped.fields.metric(REVENUE)).limit(2).metric(REVENUE)
    result = target.execute()
    frame = result.to_pandas()
    if kind in ("population", "entity"):
        assert frame.entity_identity.tolist() == [(i,) for i in range(1, 7)]
    elif kind == "reducers":
        assert frame.revenue.tolist() == [1058.5]
        assert frame.order_count.tolist() == [5]
        assert frame.min_amount.tolist() == [-2.0]
        assert frame.max_amount.tolist() == [999.0]
    else:
        expected = [30.75] * 2
        assert frame.revenue.tolist() == expected
        assert frame.channel.tolist() == ["a", "b"]
        assert runtime.statistics.transferred_rows == 2
    assert runtime.statistics.primary_queries == 1
    before = counts(runtime)
    assert target.execute().state.artifact_ref == result.state.artifact_ref
    assert counts(runtime) == before
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.parametrize("invalid", ["duplicate", "null", "nan", "inf"])
def test_invalid_source(tmp_path: Path, source_table: str, invalid: str) -> None:
    statements = {
        "duplicate": f"INSERT INTO {source_table}(id) VALUES (1)",
        "null": f"INSERT INTO {source_table}(id) VALUES (NULL)",
        "nan": f"INSERT INTO {source_table}(id,amount) VALUES (7,toFloat64('nan'))",
        "inf": f"INSERT INTO {source_table}(id,amount) VALUES (7,toFloat64('inf'))",
    }
    with clickhouse.connection(admin=True) as con:
        con.command(statements[invalid])
    registry, sidecar = registry_for(tmp_path / "unused", engine="clickhouse", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, invalid)
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(REVENUE)
        .where(gt(REVENUE, 1e9))
        .aggregate()
    )
    with pytest.raises(MaterializationError):
        target.execute()
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()


def test_read_only_account() -> None:
    evidence = clickhouse.setup()
    assert evidence["user"] == "analysis_reader"
    assert len(evidence["denied"]) == 4


@pytest.mark.parametrize("empty", [False, True])
def test_null_empty(tmp_path: Path, source_table: str, empty: bool) -> None:
    with clickhouse.connection(admin=True) as con:
        con.command(f"TRUNCATE TABLE {source_table}")
        if not empty:
            con.command(f"INSERT INTO {source_table}(id) VALUES (1),(2)")
    registry, sidecar = registry_for(tmp_path / "unused", engine="clickhouse", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "null-empty")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(
            [
                REVENUE,
                ref.metric("sales.order_count"),
                ref.metric("sales.min_amount"),
                ref.metric("sales.max_amount"),
            ]
        )
        .aggregate()
    )
    frame = target.execute().to_pandas()
    assert frame.revenue.isna().all()
    assert frame.order_count.tolist() == [0]
    assert frame.min_amount.isna().all()
    assert frame.max_amount.isna().all()


def numeric_registry(
    tmp_path: Path, source_table: str, logical: str
) -> tuple[Registry, CompiledExpressionSidecar]:
    from dataclasses import replace

    registry, sidecar = registry_for(tmp_path / "unused", engine="clickhouse", table=source_table)
    entity = registry.entities["sales.orders"]
    source = replace(
        entity.source,
        columns=tuple(
            (name, replace(binding, data_type=logical) if name == "amount" else binding)
            for name, binding in entity.source.columns
        ),
    )
    registry = replace(
        registry, entities={**registry.entities, "sales.orders": replace(entity, source=source)}
    )
    registry.freeze()
    return registry, sidecar


@pytest.mark.parametrize(
    "logical,physical,value",
    [
        ("int64", "Int64", "9223372036854775807"),
        ("int8", "Int8", "127"),
        ("int16", "Int16", "32767"),
        ("int32", "Int32", "2147483647"),
        ("decimal(38,6)", "Decimal(38,6)", "'123456789012345678901234567890.123456'"),
        ("float32", "Float32", "1.25"),
    ],
)
def test_exact_types(
    tmp_path: Path, source_table: str, logical: str, physical: str, value: str
) -> None:
    from decimal import Decimal

    with clickhouse.connection(admin=True) as con:
        con.command(f"TRUNCATE TABLE {source_table}")
        con.command(f"ALTER TABLE {source_table} MODIFY COLUMN amount Nullable({physical})")
        con.command(f"INSERT INTO {source_table}(id,amount) VALUES (9223372036854775807,{value})")
    registry, sidecar = numeric_registry(tmp_path, source_table, logical)
    runtime = DatasetRuntime.create(tmp_path, "types")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    assert sources.observe(REVENUE).execute().to_pandas().entity_identity.tolist() == [(2**63 - 1,)]
    result = sources.observe(REVENUE).aggregate().execute().to_pandas()
    assert Decimal(str(result.revenue.iloc[0])) == Decimal(value.strip("'"))


@pytest.mark.parametrize(
    "logical,physical,value",
    [
        ("int64", "Int64", "9223372036854775807"),
        ("int64", "Int64", "-9223372036854775808"),
        ("decimal(38,6)", "Decimal(38,6)", "'99999999999999999999999999999999.999999'"),
        ("float64", "Float64", "1.1e308"),
    ],
)
def test_overflow_no_publication(
    tmp_path: Path, source_table: str, logical: str, physical: str, value: str
) -> None:
    import pyarrow as pa
    from clickhouse_connect.driver.exceptions import DatabaseError

    with clickhouse.connection(admin=True) as con:
        con.command(f"TRUNCATE TABLE {source_table}")
        con.command(f"ALTER TABLE {source_table} MODIFY COLUMN amount Nullable({physical})")
        con.command(f"INSERT INTO {source_table}(id,amount) VALUES (1,{value}),(2,{value})")
    registry, sidecar = numeric_registry(tmp_path, source_table, logical)
    runtime = DatasetRuntime.create(tmp_path, "overflow")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    with pytest.raises((OverflowError, pa.ArrowInvalid, MaterializationError, DatabaseError)):
        target.execute()
    assert counts(runtime)["dataset_artifacts"] == 0
    with clickhouse.connection(admin=True) as con:
        con.command(f"TRUNCATE TABLE {source_table}")
        con.command(f"INSERT INTO {source_table}(id,amount) VALUES (1,1)")
    assert target.execute().to_pandas().revenue.tolist() == [1]


def test_large_source_and_blocks(
    tmp_path: Path, source_table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from tests.lazy_scalar_source_fixtures import capture_submissions

    with clickhouse.connection(admin=True) as con:
        con.command(f"TRUNCATE TABLE {source_table}")
        con.command(
            f"INSERT INTO {source_table}(id,amount,channel) SELECT number,toFloat64(number%100),toString(number%5) FROM numbers(20000)"
        )

    wire = []
    original_context_query = HttpClient._query_with_context
    original_command = HttpClient.command

    def context_query(client, context):
        wire.append({"operation": "query", "sql": context.query})
        return original_context_query(client, context)

    def command(client, sql, *args, **kwargs):
        wire.append({"operation": "command", "sql": sql})
        return original_command(client, sql, *args, **kwargs)

    monkeypatch.setattr(HttpClient, "_query_with_context", context_query)
    monkeypatch.setattr(HttpClient, "command", command)
    submitted = capture_submissions(monkeypatch)
    registry, sidecar = registry_for(tmp_path / "unused", engine="clickhouse", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "economics")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    grouped = sources.observe(REVENUE).with_dimensions(CHANNEL).aggregate()
    result = grouped.rank(grouped.fields.metric(REVENUE)).limit(2).execute().to_pandas()
    assert list(zip(result.revenue, result.channel, strict=True)) == [
        (206000.0, "4"),
        (202000.0, "3"),
    ]
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.transferred_rows == 2
    receipt = {
        "backend": "clickhouse",
        "source_rows": 20000,
        "primary_queries": 1,
        "transferred_rows": 2,
        "transferred_bytes": runtime.statistics.transferred_bytes,
        "statements": list(runtime.statistics.statements),
        "submitted": list(submitted),
        "server_scan_metrics": "unavailable",
        "wire_operations_including_connection": list(wire),
    }
    # A separate larger Population journey proves actual primary Native blocks.
    from clickhouse_connect.driver.query import QueryResult

    with clickhouse.connection(admin=True) as con:
        con.command(
            f"INSERT INTO {source_table}(id,amount) SELECT number,1 FROM numbers(20000,180000)"
        )
    original = QueryResult._row_block_stream
    primary_results = []
    blocks = []

    def receive(stream: StreamContext) -> None:
        primary_results.append(stream.source)

    _watch_primary_streams(monkeypatch, receive)

    def row_blocks(query):
        for block in original(query):
            if any(query is primary for primary in primary_results):
                blocks.append(len(block))
            yield block

    monkeypatch.setattr(QueryResult, "_row_block_stream", row_blocks)
    population = sources.population(ref.entity("sales.orders")).execute().to_pandas()
    assert len(population) == 200000
    assert len(primary_results) == 1
    assert len(blocks) > 1 and sum(blocks) == 200000
    receipt["population_source_rows"] = 200000
    receipt["primary_native_blocks"] = blocks
    output = os.environ.get("MARIVO_SLICE6_RECEIPT")
    if output:
        Path(output).write_text(json.dumps(receipt, indent=2) + "\n")


@pytest.mark.parametrize(
    "physical",
    [
        "UInt64",
        "Int128",
        "Enum8('z'=1,'a'=2)",
        "DateTime64(6,'UTC')",
        "Array(Int64)",
        "LowCardinality(String)",
        "FixedString(4)",
        "Date32",
    ],
)
def test_unqualified_physical_types(tmp_path: Path, source_table: str, physical: str) -> None:
    with clickhouse.connection(admin=True) as con:
        con.command(f"TRUNCATE TABLE {source_table}")
        con.command(f"ALTER TABLE {source_table} DROP COLUMN region")
        con.command(f"ALTER TABLE {source_table} ADD COLUMN region {physical}")
    registry, sidecar = registry_for(tmp_path / "unused", engine="clickhouse", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "physical-rejection")
    with pytest.raises(MaterializationError):
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(
            REVENUE
        ).aggregate().execute()
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.statistics.primary_queries == 0


def test_view_and_effective_join_nulls(tmp_path: Path, source_table: str) -> None:
    with clickhouse.connection() as con:
        rows = con.query(
            "SELECT b.value FROM (SELECT 1 AS id) a LEFT JOIN (SELECT 2 AS id, 7 AS value) b ON a.id=b.id"
        ).result_rows
        assert rows == [(None,)]
    view = source_table + "_view"
    with clickhouse.connection(admin=True) as con:
        con.command(f"CREATE VIEW {view} AS SELECT * FROM {source_table}")
        try:
            registry, sidecar = registry_for(tmp_path / "unused", engine="clickhouse", table=view)
            runtime = DatasetRuntime.create(tmp_path, "view")
            with pytest.raises(MaterializationError):
                runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(
                    REVENUE
                ).aggregate().execute()
            assert runtime.statistics.primary_queries == 0
        finally:
            con.command(f"DROP VIEW {view}")


def test_between_checks_and_output_update(
    tmp_path: Path, source_table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.clickhouse_execution import ClickHouseCursor

    original = ClickHouseCursor.execute
    updated = False

    def execute(cursor, sql, parameters=()):
        nonlocal updated
        if "isFinite" in sql and not updated:
            result = original(cursor, sql, parameters)
            with clickhouse.connection(admin=True) as con:
                con.command(f"INSERT INTO {source_table}(id,amount) VALUES (99,1)")
            updated = True
            return result
        return original(cursor, sql, parameters)

    monkeypatch.setattr(ClickHouseCursor, "execute", execute)
    registry, sidecar = registry_for(tmp_path / "unused", engine="clickhouse", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "changed-observation")
    result = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(REVENUE)
        .aggregate()
        .execute()
    )
    assert updated
    assert result.to_pandas().revenue.tolist() == [1059.5]


@pytest.mark.parametrize("fault", ["submit", "fetch", "cleanup"])
def test_actual_driver_failure_recovers(
    tmp_path: Path, source_table: str, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:

    registry, sidecar = registry_for(tmp_path / "unused", engine="clickhouse", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "driver-failure")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    original_next = StreamContext.__next__
    original_exit = StreamContext.__exit__
    armed = set()

    def receive(stream: StreamContext) -> None:
        armed.add(id(stream))
        if fault == "submit":
            stream.__exit__(None, None, None)
            raise OSError("submit acknowledgement lost")

    def fetch(stream):
        if id(stream) in armed and fault == "fetch":
            raise OSError("native block disconnected")
        return original_next(stream)

    def close(stream, *args):
        result = original_exit(stream, *args)
        if id(stream) in armed and fault == "cleanup":
            raise OSError("remote cleanup status unknown")
        return result

    with monkeypatch.context() as patch:
        _watch_primary_streams(patch, receive)
        patch.setattr(StreamContext, "__next__", fetch)
        patch.setattr(StreamContext, "__exit__", close)
        with pytest.raises(OSError):
            target.execute()
    assert counts(runtime)["dataset_artifacts"] == 0
    assert target.execute().to_pandas().revenue.tolist() == [1058.5]


def test_live_partial_native_stream_cancel() -> None:
    from dataclasses import replace

    import ibis

    with clickhouse.connection() as con:
        adapter = ClickHouseExecutionAdapter(ibis.clickhouse.from_connection(con))
        statement = replace(
            adapter.statement("SELECT toInt64(number) AS id FROM numbers(100000)"),
            schema=ibis.schema({"id": "int64"}).to_pyarrow(),
        )
        stream = adapter.batches(statement, chunk_size=1)
        iterator = iter(stream)
        assert next(iterator).to_pylist() == [{"id": 0}]
        adapter.interrupt()
        assert not adapter._streams and not adapter._cursors
        with pytest.raises(MaterializationError):
            next(iterator)
    with clickhouse.connection() as con:
        assert con.query("SELECT 1").first_row == (1,)


def test_count_conversion_is_checked_before_unsigned_wrap() -> None:
    import ibis
    from clickhouse_connect.driver.exceptions import DatabaseError

    with clickhouse.connection() as con:
        adapter = ClickHouseExecutionAdapter(ibis.clickhouse.from_connection(con))
        compiled = adapter.compile(ibis.table({"id": "int64"}, name="permission_probe").count())
        assert "Decimal256" in compiled or "Decimal(76" in compiled
        assert "Int64" in compiled
        # Exercise the exact nested conversion from the lowered COUNT without
        # requiring an impractically large physical source.
        expression = ibis.literal(2**63, type="uint64").cast("decimal(76,0)").cast("int64")
        with pytest.raises(DatabaseError, match="overflow"):
            adapter.read_table(expression)
        adapter.disconnect()


def test_live_exact_composite_identity_and_large_cell() -> None:
    from decimal import Decimal

    import ibis

    from marivo.analysis.materialization.scalar_sql_execution import ScalarStatement

    with clickhouse.connection() as con:
        adapter = ClickHouseExecutionAdapter(ibis.clickhouse.from_connection(con))
        statement = ScalarStatement(
            "SELECT toDecimal128('1234567890123456789012.123456',6), repeat('x',300000)",
            (),
            ibis.schema({"identity": "struct<id:decimal(28,6),label:string>"}).to_pyarrow(),
            "primary",
            adapter._context,
            columns=((0, 1),),
        )
        assert adapter.read_table(statement).to_pylist() == [
            {"identity": {"id": Decimal("1234567890123456789012.123456"), "label": "x" * 300000}}
        ]
        adapter.disconnect()


def test_engine_timezone_metadata_and_physical_nullability(source_table: str) -> None:
    from zoneinfo import ZoneInfo

    import ibis

    with clickhouse.connection(admin=True) as con:
        con.command(f"ALTER TABLE {source_table} ADD COLUMN required_id Int64 DEFAULT 0")
    with clickhouse.connection() as con:
        expected = con.query("SELECT timezone()").first_row[0]
        adapter = ClickHouseExecutionAdapter(ibis.clickhouse.from_connection(con))
        timezone = adapter.timezone()
        assert timezone.engine_timezone_name == expected
        assert timezone.engine_timezone_tz == ZoneInfo(expected)
        assert timezone.read_tz_resolution == "engine"
        assert timezone.warning is None
        schema = adapter.get_schema(source_table)
        assert not schema["required_id"].nullable
        assert schema["amount"].nullable
        adapter.disconnect()
