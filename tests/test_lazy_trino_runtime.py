"""Opt-in real SELECT-only Trino Group A Dataset acceptance."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from marivo.analysis import time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_acceptance_capture import counts
from tests.lazy_scalar_source_fixtures import (
    registry_for,
)
from tests.multisource_environment import trino_analysis as trino

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1", reason="opt-in Trino service"
    ),
]
REVENUE = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")


@pytest.fixture
def source_table(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    table = "dataset_" + uuid4().hex
    with trino.connection(admin=True) as con:
        cur = con.cursor()
        cur.execute(
            f'CREATE TABLE {table}(id BIGINT, tenant VARCHAR, customer_id BIGINT, order_id BIGINT, amount DOUBLE, weight DOUBLE, region VARCHAR, channel VARCHAR, day DATE, start DATE, "end" DATE)'
        )
        cur.execute(
            f"INSERT INTO {table}(id,amount,channel,day) VALUES "
            "(1,10.25,'a',DATE '2026-02-02'),(2,20.5,'a',DATE '2026-02-03'),"
            "(3,30.75,'b',DATE '2026-02-04'),(4,NULL,'b',DATE '2026-02-05'),"
            "(5,-2.0,'c',DATE '2026-02-06'),(6,999.0,'outside',DATE '2026-03-01')"
        ).fetchall()
        try:
            yield table
        finally:
            cur.execute(f"DROP TABLE IF EXISTS {table}").fetchall()
            cur.close()


@pytest.mark.parametrize("kind", ["grouped", "population", "entity", "reducers"])
def test_group_a(tmp_path: Path, source_table: str, kind: str) -> None:
    registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=source_table)
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
        "nan": f"INSERT INTO {source_table}(id,amount) VALUES (7,nan())",
        "inf": f"INSERT INTO {source_table}(id,amount) VALUES (7,infinity())",
    }
    with trino.connection(admin=True) as con:
        cur = con.cursor()
        try:
            cur.execute(statements[invalid]).fetchall()
        finally:
            cur.close()
    registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=source_table)
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
    evidence = trino.setup()
    assert evidence["user"] == "analysis_reader"
    assert len(evidence["denied"]) == 4


@pytest.mark.parametrize("empty", [False, True])
def test_null_empty(tmp_path: Path, source_table: str, empty: bool) -> None:
    with trino.connection(admin=True) as con:
        cur = con.cursor()
        try:
            cur.execute(f"DELETE FROM {source_table}").fetchall()
            if not empty:
                cur.execute(f"INSERT INTO {source_table}(id) VALUES (1),(2)").fetchall()
        finally:
            cur.close()
    registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "null-empty")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe([REVENUE, ref.metric("sales.order_count")])
        .aggregate()
    )
    frame = target.execute().to_pandas()
    assert frame.revenue.isna().all()
    assert frame.order_count.tolist() == [0]


@pytest.mark.parametrize("namespace", [("iceberg", "analysis"), "iceberg.analysis", "analysis"])
def test_source_namespace_override(
    tmp_path: Path, source_table: str, namespace: str | tuple[str, str]
) -> None:
    from dataclasses import replace

    registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=source_table)
    entities = dict(registry.entities)
    entity = entities["sales.orders"]
    entities["sales.orders"] = replace(entity, source=replace(entity.source, database=namespace))
    datasources = {
        key: replace(value, fields={**value.fields, "schema": "nonexistent"})
        for key, value in registry.datasources.items()
    }
    registry = replace(registry, entities=entities, datasources=datasources)
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path, "namespace")
    result = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(REVENUE)
        .aggregate()
        .execute()
    )
    assert result.to_pandas().revenue.tolist() == [1058.5]


def test_large_source_and_real_http_pages(
    tmp_path: Path, source_table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from collections import Counter

    from trino.client import TrinoRequest

    from tests.lazy_scalar_source_fixtures import capture_submissions

    with trino.connection(admin=True) as con:
        cur = con.cursor()
        try:
            cur.execute(f"DELETE FROM {source_table}").fetchall()
            cur.execute(
                f"INSERT INTO {source_table}(id,amount,channel) "
                "SELECT i,CAST(i % 100 AS DOUBLE),CAST(i % 5 AS VARCHAR) "
                "FROM UNNEST(sequence(0,199)) a(x) CROSS JOIN UNNEST(sequence(0,99)) b(y) "
                "CROSS JOIN LATERAL (SELECT x*100+y AS i)"
            ).fetchall()
        finally:
            cur.close()
    submitted = capture_submissions(monkeypatch)
    from collections import OrderedDict

    from trino.dbapi import must_use_legacy_prepared_statements

    monkeypatch.setattr(must_use_legacy_prepared_statements, "cache", OrderedDict())
    wire_statements: list[str] = []
    original_post = TrinoRequest.post

    def post(request, sql, additional_http_headers=None):
        wire_statements.append(sql)
        return original_post(request, sql, additional_http_headers)

    monkeypatch.setattr(TrinoRequest, "post", post)
    pages: Counter[str] = Counter()
    stats: dict[str, object] = {}
    original = TrinoRequest.process

    def process(request, response):
        result = original(request, response)
        if result.rows:
            pages[result.id] += 1
        stats[result.id] = result.stats
        return result

    monkeypatch.setattr(TrinoRequest, "process", process)
    registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "economics")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    grouped = sources.observe(REVENUE).with_dimensions(CHANNEL).aggregate()
    frame = grouped.rank(grouped.fields.metric(REVENUE)).limit(2).execute().to_pandas()
    expected = [(206000.0, "4"), (202000.0, "3")]
    assert list(zip(frame.revenue, frame.channel, strict=True)) == expected
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.transferred_rows == 2
    assert len([sql for role, sql in runtime.statistics.statements if role == "primary"]) == 1
    receipt = {
        "backend": "trino",
        "source_rows": 20000,
        "expected": expected,
        "primary_queries": runtime.statistics.primary_queries,
        "transferred_rows": runtime.statistics.transferred_rows,
        "transferred_bytes": runtime.statistics.transferred_bytes,
        "statements_by_role": runtime.statistics.statements,
        "driver_submissions": list(submitted),
        "http_post_statements": list(wire_statements),
        "server_stats": dict(stats),
        "wire_bytes": None,
    }
    assert "EXECUTE IMMEDIATE 'SELECT 1'" in wire_statements
    primary_sql = next(sql for role, sql in runtime.statistics.statements if role == "primary")
    assert wire_statements.count(primary_sql) == 1
    assert not any(
        sql.startswith(("CREATE", "INSERT", "UPDATE", "DROP")) for sql in wire_statements
    )
    # Wide unique labels force multiple actual data-bearing HTTP pages, independently
    # of the Arrow batch size. The source still executes as an ordinary Dataset.
    with trino.connection(admin=True) as con:
        cur = con.cursor()
        try:
            cur.execute(
                f"UPDATE {source_table} SET channel = concat(CAST(id AS VARCHAR), rpad('x', 2000, 'x'))"
            ).fetchall()
        finally:
            cur.close()
    pages.clear()
    frame = sources.observe(REVENUE).with_dimensions(CHANNEL).execute().to_pandas()
    assert len(frame) == 20000
    assert all(len(value) >= 2001 for value in frame.channel)
    assert max(pages.values()) > 1
    receipt["data_pages_by_query"] = dict(pages)
    receipt["multi_page_rows"] = len(frame)
    directory = os.environ.get("MARIVO_SLICE5_RECEIPTS")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / "trino.json").write_text(json.dumps(receipt, indent=2, default=str) + "\n")


def numeric_registry(
    tmp_path: Path, source_table: str, logical: str
) -> tuple[Registry, CompiledExpressionSidecar]:
    """Reuse explicit numeric source declarations across boundary and overflow probes."""
    from dataclasses import replace

    registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=source_table)
    entities = dict(registry.entities)
    entity = entities["sales.orders"]
    entities["sales.orders"] = replace(
        entity,
        source=replace(
            entity.source,
            columns=tuple(
                (name, replace(binding, data_type=logical) if name == "amount" else binding)
                for name, binding in entity.source.columns
            ),
        ),
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    return registry, sidecar


@pytest.mark.parametrize(
    "logical,physical,values,expected",
    [
        (
            "decimal(38,6)",
            "DECIMAL(38,6)",
            "DECIMAL '123456789012345678901234567890.123456'",
            "123456789012345678901234567890.123456",
        ),
        ("int64", "BIGINT", "9223372036854775807", "9223372036854775807"),
        ("int32", "INTEGER", "2147483647", "2147483647"),
        ("float32", "REAL", "CAST(1.25 AS REAL)", "1.25"),
        ("float64", "DOUBLE", "1.1e308", "1.1e+308"),
    ],
)
def test_exact_numeric_types(
    tmp_path: Path, source_table: str, logical: str, physical: str, values: str, expected: str
) -> None:
    with trino.connection(admin=True) as con:
        cur = con.cursor()
        try:
            cur.execute(f"DELETE FROM {source_table}").fetchall()
            cur.execute(f"ALTER TABLE {source_table} DROP COLUMN amount").fetchall()
            cur.execute(f"ALTER TABLE {source_table} ADD COLUMN amount {physical}").fetchall()
            cur.execute(
                f"INSERT INTO {source_table}(id,amount) VALUES (9223372036854775807,{values})"
            ).fetchall()
        finally:
            cur.close()
    registry, sidecar = numeric_registry(tmp_path, source_table, logical)
    runtime = DatasetRuntime.create(tmp_path, "types")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    observed = sources.observe(REVENUE).execute().to_pandas()
    assert observed.entity_identity.tolist() == [(2**63 - 1,)]
    result = sources.observe(REVENUE).aggregate().execute().to_pandas()
    assert str(result.revenue.iloc[0]) == expected


def test_aggregate_overflow_not_published(tmp_path: Path, source_table: str) -> None:
    with trino.connection(admin=True) as con:
        cur = con.cursor()
        try:
            cur.execute(f"DELETE FROM {source_table}").fetchall()
            cur.execute(
                f"INSERT INTO {source_table}(id,amount) VALUES (1,1e308),(2,1e308)"
            ).fetchall()
        finally:
            cur.close()
    registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "overflow")
    with pytest.raises(MaterializationError, match="non-finite"):
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(
            REVENUE
        ).aggregate().execute()
    assert counts(runtime)["dataset_artifacts"] == 0


def test_between_query_update_needs_no_snapshot(
    tmp_path: Path, source_table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.trino_execution import TrinoExecutionAdapter

    original = TrinoExecutionAdapter.batches
    changed = False

    def batches(adapter, value, **kwargs):
        nonlocal changed
        if kwargs.get("role") == "primary" and not changed:
            changed = True
            with trino.connection(admin=True) as con:
                cur = con.cursor()
                try:
                    cur.execute(f"UPDATE {source_table} SET amount=1").fetchall()
                finally:
                    cur.close()
        return original(adapter, value, **kwargs)

    monkeypatch.setattr(TrinoExecutionAdapter, "batches", batches)
    registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "concurrent")
    result = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(REVENUE)
        .aggregate()
        .execute()
    )
    assert changed
    assert result.to_pandas().revenue.tolist() == [6.0]
    assert runtime.statistics.primary_queries == 1


@pytest.mark.parametrize(
    "fault", ["malformed_page", "disconnect", "cancel_ack", "slow_fetch", "finish_ack"]
)
def test_real_driver_faults_and_safe_next_run(
    tmp_path: Path, source_table: str, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    import json
    from time import sleep

    from trino.client import TrinoRequest

    from marivo.analysis.materialization.trino_execution import TrinoCursor, TrinoExecutionAdapter

    registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, fault)
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    batches = TrinoExecutionAdapter.batches
    process = TrinoRequest.process
    get_page = TrinoRequest.get
    fetch = TrinoCursor.fetchmany
    close = TrinoCursor.close
    disconnect = TrinoExecutionAdapter.disconnect
    armed = False
    injected = False
    original_error = OSError("injected connection loss during real primary transfer")

    def primary(adapter, value, **kwargs):
        nonlocal armed
        armed = kwargs.get("role") == "primary"
        return batches(adapter, value, **kwargs)

    def page(request, response):
        nonlocal injected
        if armed and not injected and fault == "malformed_page":
            payload = response.json()
            if payload.get("data"):
                injected = True
                response._content = b'{"malformed":'
        return process(request, response)

    def get(request, url):
        nonlocal injected
        if armed and not injected and fault == "disconnect":
            injected = True
            raise original_error
        return get_page(request, url)

    def read(cursor, size):
        nonlocal injected
        rows = fetch(cursor, size)
        if (
            armed
            and not injected
            and rows
            and fault not in {"malformed_page", "finish_ack", "disconnect"}
        ):
            injected = True
            if fault == "slow_fetch":
                sleep(0.05)
            else:
                raise original_error
        return rows

    def lost_close(cursor):
        close(cursor)
        if armed and fault == "cancel_ack":
            raise OSError("lost cancel acknowledgement")

    def failed_finish(adapter):
        nonlocal injected
        disconnect(adapter)
        if armed and fault == "finish_ack":
            injected = True
            raise original_error

    with monkeypatch.context() as patch:
        patch.setattr(TrinoExecutionAdapter, "disconnect", failed_finish)
        patch.setattr(TrinoExecutionAdapter, "batches", primary)
        patch.setattr(TrinoRequest, "process", page)
        patch.setattr(TrinoRequest, "get", get)
        patch.setattr(TrinoCursor, "fetchmany", read)
        patch.setattr(TrinoCursor, "close", lost_close)
        if fault == "slow_fetch":
            assert target.execute().to_pandas().revenue.tolist() == [1058.5]
        else:
            with pytest.raises(
                json.JSONDecodeError if fault == "malformed_page" else OSError
            ) as caught:
                target.execute()
            if fault not in {"malformed_page", "finish_ack", "disconnect"}:
                assert caught.value is original_error
            assert counts(runtime)["dataset_artifacts"] == 0
            assert runtime.statistics.events["remote_read_status_unknown"] == 1
    if fault == "finish_ack":
        assert runtime.statistics.events["close_failed"] == 1
    assert injected
    assert runtime.store.resources(runtime.session_ref) == ()
    assert target.execute().to_pandas().revenue.tolist() == [1058.5]


def test_trino_native_transfer_composite_identity_and_large_cell() -> None:
    from datetime import date
    from decimal import Decimal

    import ibis
    import pyarrow as pa

    from marivo.analysis.materialization.trino_execution import TrinoExecutionAdapter

    backend = ibis.trino.connect(
        host="127.0.0.1",
        port=18080,
        user="analysis_reader",
        database="iceberg",
        schema="analysis",
        timezone="UTC",
    )
    adapter = TrinoExecutionAdapter(backend)
    previous = ibis.options.sql.default_limit
    ibis.options.sql.default_limit = 1
    try:
        # Typed VALUES is driver-side read-only SQL, independent from Dataset acceptance.
        table = backend.sql(
            "SELECT * FROM (VALUES (9223372036854775807,CAST('12345678901234567890.123456' AS DECIMAL(26,6)),DATE '0001-01-01', 'é'), (-9223372036854775808,CAST('-0.000001' AS DECIMAL(26,6)),DATE '9999-12-31','é')) t(id,amount,day,label)"
        )
        expression = table.select(
            entity_identity=ibis.struct({"id": table.id, "day": table.day, "label": table.label}),
            amount=table.amount,
        )
        stream = adapter.batches(expression, chunk_size=1)
        batches = list(stream)
        assert [batch.num_rows for batch in batches] == [1, 1]
        result = pa.Table.from_batches(batches).to_pylist()
        assert result[0]["entity_identity"] == {"id": 2**63 - 1, "day": date(1, 1, 1), "label": "é"}
        assert result[1]["entity_identity"]["id"] == -(2**63)
        assert result[1]["entity_identity"]["day"] == date(9999, 12, 31)
        assert result[0]["amount"] == Decimal("12345678901234567890.123456")
        assert result[1]["amount"] == Decimal("-0.000001")
        large = adapter.read_scalar(adapter.statement("SELECT rpad('x', 2000000, 'x')"))
        assert isinstance(large, str) and len(large) == 2000000
        assert not adapter._cursors and not adapter._streams
    finally:
        ibis.options.sql.default_limit = previous
        adapter.disconnect()


def test_view_source_journey(tmp_path: Path, source_table: str) -> None:
    view = source_table + "_view"
    with trino.connection(admin=True) as con:
        cur = con.cursor()
        try:
            cur.execute(f"CREATE VIEW {view} AS SELECT * FROM {source_table}").fetchall()
            registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=view)
            runtime = DatasetRuntime.create(tmp_path, "view")
            frame = (
                runtime.sources(semantic_registry=registry, sidecar=sidecar)
                .observe(REVENUE)
                .aggregate()
                .execute()
                .to_pandas()
            )
            assert frame.revenue.tolist() == [1058.5]
            assert runtime.statistics.primary_queries == 1
        finally:
            cur.execute(f"DROP VIEW IF EXISTS {view}").fetchall()
            cur.close()


def test_catalog_relation_schema_admits_varchar_columns() -> None:
    """system.metadata.catalogs is an ordinary relation: varchar columns validate."""
    import ibis

    from marivo.analysis.materialization.trino_execution import TrinoExecutionAdapter

    backend = ibis.trino.connect(
        host="127.0.0.1",
        port=18080,
        user="analysis_reader",
        database="iceberg",
        schema="analysis",
        timezone="UTC",
    )
    adapter = TrinoExecutionAdapter(backend)
    try:
        schema = adapter.get_schema("catalogs", catalog="system", database="metadata")
        assert {
            name: str(dtype) for name, dtype in zip(schema.names, schema.types, strict=True)
        } == {
            "catalog_name": "string",
            "connector_id": "string",
            "connector_name": "string",
            "state": "string",
        }
    finally:
        adapter.finish()


def test_partial_live_stream_cancel_targets_owned_query(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    import ibis
    import pyarrow as pa
    from trino.client import TrinoRequest

    from marivo.analysis.materialization.trino_execution import TrinoExecutionAdapter

    backend = ibis.trino.connect(
        host="127.0.0.1",
        port=18080,
        user="analysis_reader",
        database="iceberg",
        schema="analysis",
        timezone="UTC",
    )
    adapter = TrinoExecutionAdapter(backend)
    deleted: list[str] = []
    original = TrinoRequest.delete

    def delete(request, url):
        deleted.append(url)
        return original(request, url)

    monkeypatch.setattr(TrinoRequest, "delete", delete)
    statement = replace(
        adapter.statement(
            "SELECT CAST(x AS BIGINT) AS id, rpad(CAST(x AS VARCHAR),2000,'x') AS label FROM UNNEST(sequence(1,5000)) t(x)"
        ),
        schema=pa.schema([("id", pa.int64()), ("label", pa.string())]),
    )
    try:
        stream = adapter.batches(statement, chunk_size=1)
        iterator = iter(stream)
        assert next(iterator).num_rows == 1
        adapter.interrupt()
        assert deleted and len(set(deleted)) == 1
        assert not adapter._cursors and not adapter._streams
        with pytest.raises(MaterializationError):
            next(iterator)
    finally:
        adapter.finish()


@pytest.mark.parametrize(
    "logical,physical,value",
    [
        ("decimal(38,6)", "DECIMAL(38,6)", "DECIMAL '99999999999999999999999999999999.999999'"),
        ("decimal(38,6)", "DECIMAL(38,6)", "DECIMAL '-99999999999999999999999999999999.999999'"),
        ("int64", "BIGINT", "9223372036854775807"),
        ("int64", "BIGINT", "-9223372036854775808"),
    ],
)
def test_exact_aggregate_overflow_never_publishes_and_recovers(
    tmp_path: Path, source_table: str, logical: str, physical: str, value: str
) -> None:
    from trino.exceptions import TrinoUserError

    with trino.connection(admin=True) as con:
        cur = con.cursor()
        try:
            cur.execute(f"DELETE FROM {source_table}").fetchall()
            cur.execute(f"ALTER TABLE {source_table} DROP COLUMN amount").fetchall()
            cur.execute(f"ALTER TABLE {source_table} ADD COLUMN amount {physical}").fetchall()
            cur.execute(
                f"INSERT INTO {source_table}(id,amount) VALUES (1,{value}),(2,{value})"
            ).fetchall()
            registry, sidecar = numeric_registry(tmp_path, source_table, logical)
            runtime = DatasetRuntime.create(tmp_path, "exact-overflow")
            target = (
                runtime.sources(semantic_registry=registry, sidecar=sidecar)
                .observe(REVENUE)
                .aggregate()
            )
            with pytest.raises(TrinoUserError) as caught:
                target.execute()
            assert caught.value.error_name == "NUMERIC_VALUE_OUT_OF_RANGE"
            assert counts(runtime)["dataset_artifacts"] == 0
            assert runtime.store.resources(runtime.session_ref) == ()
            assert runtime.last_run_ref is not None
            failed = runtime.store.run(runtime.last_run_ref)
            assert failed is not None and failed.lifecycle == "failed"
            assert failed.output_artifact_ref is None
            assert not list(
                runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet")
            )
            # Correct the fixture explicitly, then retry the same logical action.
            cur.execute(f"DELETE FROM {source_table} WHERE id=2").fetchall()
            result = target.execute().to_pandas()
            from decimal import Decimal

            expected = Decimal(value.split("'")[1]) if logical.startswith("decimal") else int(value)
            assert result.revenue.tolist() == [expected]
            assert counts(runtime)["dataset_artifacts"] == 1
        finally:
            cur.close()
