"""Opt-in real SELECT-only MySQL Group A Dataset acceptance."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from marivo.analysis import time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.predicates import gt
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from tests.lazy_acceptance_capture import counts
from tests.lazy_scalar_source_fixtures import (
    capture_receipt,
    capture_submissions,
    duckdb_grouped_totals,
    registry_for,
)
from tests.multisource_environment import mysql_analysis as mysql

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_MYSQL_ANALYSIS_TEST") != "1", reason="opt-in MySQL service"
    ),
]
REVENUE = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")


@pytest.fixture
def source_table(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("MARIVO_TEST_MYSQL_PASSWORD", mysql.password())
    table = "dataset_" + uuid4().hex
    with mysql.connection(admin=True) as con, con.cursor() as cur:
        cur.execute(
            f"CREATE TABLE {table}(id BIGINT, tenant TEXT, customer_id BIGINT, order_id BIGINT, amount DOUBLE, weight DOUBLE, region TEXT, channel TEXT, day DATE, start DATE, `end` DATE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin"
        )
        cur.executemany(
            f"INSERT INTO {table}(id,amount,channel,day) VALUES (%s,%s,%s,%s)",
            [
                (1, 10.25, "a", "2026-02-02"),
                (2, 20.5, "a", "2026-02-03"),
                (3, 30.75, "b", "2026-02-04"),
                (4, None, "b", "2026-02-05"),
                (5, -2.0, "c", "2026-02-06"),
                (6, 999.0, "outside", "2026-03-01"),
            ],
        )
        try:
            yield table
        finally:
            cur.execute(f"DROP TABLE IF EXISTS {table}")


@pytest.mark.parametrize(
    "kind", ["grouped", "population", "entity", "reducers", "decimal", "integer", "float32"]
)
def test_group_a(tmp_path: Path, source_table: str, kind: str) -> None:
    registry, sidecar = registry_for(tmp_path / "unused", engine="mysql", table=source_table)
    if kind in ("decimal", "integer", "float32"):
        sql_type, logical_type = {
            "decimal": ("DECIMAL(18,2)", "decimal(18,2)"),
            "integer": ("BIGINT", "int64"),
            "float32": ("FLOAT", "float32"),
        }[kind]
        with mysql.connection(admin=True) as con, con.cursor() as cur:
            if kind == "integer":
                cur.execute(f"UPDATE {source_table} SET amount=FLOOR(amount)")
            cur.execute(f"ALTER TABLE {source_table} MODIFY amount {sql_type}")
        entities = dict(registry.entities)
        entity = entities["sales.orders"]
        assert isinstance(entity.source, TableSourceIR)
        entities["sales.orders"] = replace(
            entity,
            source=replace(
                entity.source,
                columns=tuple(
                    (
                        name,
                        replace(binding, data_type=logical_type) if name == "amount" else binding,
                    )
                    for name, binding in entity.source.columns
                ),
            ),
        )
        registry = replace(registry, entities=entities)
        registry.freeze()
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
        expected = (
            [30, 30]
            if kind == "integer"
            else [Decimal("30.75")] * 2
            if kind == "decimal"
            else [30.75] * 2
        )
        assert frame.revenue.tolist() == expected
        assert frame.channel.tolist() == ["a", "b"]
        assert runtime.statistics.transferred_rows == 2
    assert runtime.statistics.primary_queries == 1
    before = counts(runtime)
    assert target.execute().state.artifact_ref == result.state.artifact_ref
    assert counts(runtime) == before
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.parametrize("invalid", ["duplicate", "null", "collation", "engine", "unsigned"])
def test_invalid_source(tmp_path: Path, source_table: str, invalid: str) -> None:
    statements = {
        "duplicate": f"INSERT INTO {source_table}(id) VALUES (1)",
        "null": f"INSERT INTO {source_table}(id) VALUES (NULL)",
        "collation": f"ALTER TABLE {source_table} MODIFY channel TEXT COLLATE utf8mb4_0900_ai_ci",
        "engine": f"ALTER TABLE {source_table} ENGINE=MyISAM",
        "unsigned": f"ALTER TABLE {source_table} MODIFY id BIGINT UNSIGNED",
    }
    with mysql.connection(admin=True) as con, con.cursor() as cur:
        cur.execute(statements[invalid])
    registry, sidecar = registry_for(tmp_path / "unused", engine="mysql", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, invalid)
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(REVENUE)
        .with_dimensions(CHANNEL)
        .where(gt(REVENUE, 1e9))
        .aggregate()
    )
    with pytest.raises(MaterializationError):
        target.execute()
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()


def test_large_source_collation_and_small_output(
    tmp_path: Path, source_table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted = capture_submissions(monkeypatch)
    labels = ["a", "A", "a ", "é", "e\u0301"]
    rows = [(i, float(i % 100), labels[i % 5]) for i in range(20000)]
    with mysql.connection(admin=True) as con, con.cursor() as cur:
        cur.execute(f"DELETE FROM {source_table}")
        cur.executemany(
            f"INSERT INTO {source_table}(id,amount,channel) VALUES (%s,%s,%s)",
            rows,
        )
    registry, sidecar = registry_for(tmp_path / "unused", engine="mysql", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "large")
    grouped = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(REVENUE)
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    target = grouped.rank(grouped.fields.metric(REVENUE)).limit(2)
    frame = target.execute().to_pandas()
    expected = sorted(
        (
            (sum(i % 100 for i in range(20000) if i % 5 == group), label)
            for group, label in enumerate(labels)
        ),
        reverse=True,
    )[:2]
    assert list(zip(frame.revenue, frame.channel, strict=True)) == expected
    assert duckdb_grouped_totals(rows) == expected
    assert any(item["parameters"] for item in submitted)
    assert runtime.statistics.validation_queries == 3
    assert not any(role == "engine_check.mysql_dates" for role, _ in runtime.statistics.statements)
    capture_receipt("mysql", runtime, expected, source_rows=len(rows), submitted=submitted)
    assert runtime.statistics.transferred_rows == 2
    primary = [sql for role, sql in runtime.statistics.statements if role == "primary"]
    assert len(primary) == 1 and "GROUP BY" in primary[0] and "LIMIT 2" in primary[0]


@pytest.mark.parametrize(
    "values,expected",
    [
        ([1e308, 1e308], None),
        ([1e308, 1e308, -1e308], None),
        ([1e308, 1e307], 1.1e308),
        ([1e16, 1.0, -1e16], 0.0),
    ],
)
def test_float_sum_preserves_finite_results_and_raises_on_overflow(
    tmp_path: Path, source_table: str, values: list[float], expected: float | None
) -> None:
    import MySQLdb

    with mysql.connection(admin=True) as con, con.cursor() as cur:
        cur.execute(f"DELETE FROM {source_table}")
        cur.executemany(
            f"INSERT INTO {source_table}(id,amount) VALUES (%s,%s)", list(enumerate(values, 1))
        )
    registry, sidecar = registry_for(tmp_path / "unused", engine="mysql", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "float-boundary")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    if expected is None:
        with pytest.raises(MySQLdb.OperationalError, match="out of range"):
            target.execute()
        assert counts(runtime)["dataset_artifacts"] == 0
    else:
        assert target.execute().to_pandas().revenue.tolist() == [expected]


@pytest.mark.parametrize("value", ["0000-00-00", "2026-00-01", "2026-02-30"])
def test_invalid_mysql_date_rejected_before_publication(
    tmp_path: Path, source_table: str, value: str
) -> None:
    with mysql.connection(admin=True) as con, con.cursor() as cur:
        cur.execute("SET SESSION sql_mode='ALLOW_INVALID_DATES'")
        cur.execute(f"UPDATE {source_table} SET day=%s WHERE id=1", (value,))
    registry, sidecar = registry_for(tmp_path / "unused", engine="mysql", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "invalid-date")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(REVENUE, time_scope=time_scope(start="2026-02-01", end="2026-03-01"))
        .aggregate()
    )
    with pytest.raises(MaterializationError, match="MySQL scalar storage contract"):
        target.execute()
    assert counts(runtime)["dataset_artifacts"] == 0


@pytest.mark.parametrize("empty", [False, True])
def test_null_and_empty_source(tmp_path: Path, source_table: str, empty: bool) -> None:
    with mysql.connection(admin=True) as con, con.cursor() as cur:
        cur.execute(
            f"DELETE FROM {source_table}" if empty else f"UPDATE {source_table} SET amount=NULL"
        )
    registry, sidecar = registry_for(tmp_path / "unused", engine="mysql", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "nulls")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe((REVENUE, ref.metric("sales.order_count")))
        .aggregate()
    )
    frame = target.execute().to_pandas()
    assert frame.revenue.isna().all()
    assert frame.empty or frame.order_count.tolist() == [0]


def test_read_only_account_cannot_modify_fixture(source_table: str) -> None:
    import MySQLdb

    with mysql.connection() as con, con.cursor() as cur:
        for sql in (
            f"INSERT INTO {source_table}(id) VALUES (99)",
            f"DELETE FROM {source_table}",
            "CREATE TABLE forbidden(id INT)",
            "CREATE TEMPORARY TABLE forbidden_tmp(id INT)",
        ):
            with pytest.raises(MySQLdb.OperationalError):
                cur.execute(sql)


def test_undeclared_physical_columns_do_not_expand_admission(
    tmp_path: Path, source_table: str
) -> None:
    with mysql.connection(admin=True) as con, con.cursor() as cur:
        cur.execute(f"ALTER TABLE {source_table} ADD COLUMN ungoverned JSON")
        cur.execute(f"UPDATE {source_table} SET ungoverned=JSON_OBJECT('key',1)")
    registry, sidecar = registry_for(tmp_path / "unused", engine="mysql", table=source_table)
    runtime = DatasetRuntime.create(tmp_path, "declared")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    assert target.execute().to_pandas().revenue.tolist() == [1058.5]
