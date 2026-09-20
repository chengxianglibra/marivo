"""Real SQLite scalar Dataset execution and failure boundaries."""

import sqlite3
from pathlib import Path

import pytest

from marivo.analysis import time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_scalar_source_fixtures import (
    capture_receipt,
    capture_submissions,
    duckdb_grouped_totals,
    registry_for,
)

pytestmark = pytest.mark.runtime
REVENUE = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")


@pytest.fixture
def source_database(tmp_path: Path) -> Path:
    path = tmp_path / "source.sqlite"
    with sqlite3.connect(path) as con:
        con.execute(
            'CREATE TABLE orders (id INTEGER, tenant TEXT, customer_id INTEGER, order_id INTEGER, amount REAL, weight REAL, region TEXT, channel TEXT, day DATE, start DATE, "end" DATE)'
        )
        con.executemany(
            "INSERT INTO orders(id,amount,channel,day) VALUES (?,?,?,?)",
            [
                (1, 10.25, "a", "2026-02-02"),
                (2, 20.5, "a", "2026-02-03"),
                (3, 30.75, "b", "2026-02-04"),
                (4, None, "b", "2026-02-05"),
                (5, -2.0, "c", "2026-02-06"),
                (6, 999.0, "outside", "2026-03-01"),
            ],
        )
    return path


def test_scalar_group_a(tmp_path: Path, source_database: Path) -> None:
    registry, sidecar = registry_for(source_database)
    runtime = DatasetRuntime.create(tmp_path / "project", "sqlite-group-a")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    grouped = (
        sources.observe(REVENUE, time_scope=time_scope(start="2026-02-01", end="2026-03-01"))
        .with_dimensions(CHANNEL)
        .aggregate()
        .where(gt(REVENUE, 0))
    )
    target = grouped.rank(grouped.fields.metric(REVENUE)).limit(2).metric(REVENUE)
    result = target.execute()
    frame = result.to_pandas()
    assert frame["revenue"].tolist() == [30.75, 30.75]
    assert frame["channel"].tolist() == ["a", "b"]
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.transferred_rows == 2
    assert target.execute().state.artifact_ref == result.state.artifact_ref
    assert result.rollup(drop_dimensions=(CHANNEL,)).execute().to_pandas()["revenue"].tolist() == [
        61.5
    ]


@pytest.mark.parametrize("target_kind", ["population", "entity", "reducers"])
def test_identity_and_reducers(tmp_path: Path, source_database: Path, target_kind: str) -> None:
    from dataclasses import replace

    registry, sidecar = registry_for(source_database)
    entities = dict(registry.entities)
    entities["sales.orders"] = replace(entities["sales.orders"], primary_key=("channel", "id"))
    registry = replace(registry, entities=entities)
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path / "identity", target_kind)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    if target_kind == "population":
        target = sources.population(ref.entity("sales.orders"))
    elif target_kind == "entity":
        target = sources.observe(REVENUE)
    else:
        target = sources.observe(
            (
                REVENUE,
                ref.metric("sales.order_count"),
                ref.metric("sales.min_amount"),
                ref.metric("sales.max_amount"),
            )
        ).aggregate()
    result = target.execute().to_pandas()
    if target_kind != "reducers":
        assert len(result) == 6
        assert sorted(result.entity_identity) == [
            ("a", 1),
            ("a", 2),
            ("b", 3),
            ("b", 4),
            ("c", 5),
            ("outside", 6),
        ]
    else:
        assert result["revenue"].tolist() == [1058.5]
        assert result["order_count"].tolist() == [5]
        assert result["min_amount"].tolist() == [-2.0]
        assert result["max_amount"].tolist() == [999.0]


@pytest.mark.parametrize("corruption", ["mixed", "date", "zero_year", "duplicate", "null"])
def test_invalid_source_never_publishes_and_recovers(
    tmp_path: Path, source_database: Path, corruption: str
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from tests.lazy_acceptance_capture import counts

    with sqlite3.connect(source_database) as con:
        if corruption == "mixed":
            con.execute("UPDATE orders SET amount='not numeric' WHERE id=1")
        elif corruption == "date":
            con.execute("UPDATE orders SET day='2026-02-30' WHERE id=1")
        elif corruption == "zero_year":
            con.execute("UPDATE orders SET day='0000-01-01' WHERE id=1")
        elif corruption == "duplicate":
            con.execute("INSERT INTO orders SELECT * FROM orders WHERE id=1")
        else:
            con.execute("UPDATE orders SET id=NULL WHERE id=1")
    registry, sidecar = registry_for(source_database)
    runtime = DatasetRuntime.create(tmp_path / "invalid", corruption)
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(
            REVENUE,
            time_scope=time_scope(start="2026-02-01", end="2026-03-01")
            if corruption in {"date", "zero_year"}
            else None,
        )
        .where(gt(REVENUE, 1e9))
        .aggregate()
    )
    with pytest.raises(MaterializationError):
        target.execute()
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    with sqlite3.connect(source_database) as con:
        con.execute("DELETE FROM orders")
        con.execute("INSERT INTO orders(id,amount,day) VALUES (1,2,'2026-02-01')")
    target.execute()


def test_large_source_small_result(
    tmp_path: Path, source_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted = capture_submissions(monkeypatch)
    rows = [(i, float(i % 100), str(i % 10)) for i in range(20000)]
    with sqlite3.connect(source_database) as con:
        con.execute("DELETE FROM orders")
        con.executemany(
            "INSERT INTO orders(id,amount,channel) VALUES (?,?,?)",
            rows,
        )
    registry, sidecar = registry_for(source_database)
    runtime = DatasetRuntime.create(tmp_path / "large", "large")
    grouped = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(REVENUE)
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    frame = grouped.rank(grouped.fields.metric(REVENUE)).limit(2).execute().to_pandas()
    expected = sorted(
        (
            (sum(i % 100 for i in range(20000) if i % 10 == group), str(group))
            for group in range(10)
        ),
        reverse=True,
    )[:2]
    assert list(zip(frame.revenue, frame.channel, strict=True)) == expected
    assert duckdb_grouped_totals(rows) == expected
    assert any(item["parameters"] for item in submitted)
    assert runtime.statistics.validation_queries == 4
    capture_receipt("sqlite", runtime, expected, source_rows=len(rows), submitted=submitted)
    assert runtime.statistics.transferred_rows == 2
    sql = [sql for role, sql in runtime.statistics.statements if role == "primary"]
    assert len(sql) == 1 and "GROUP BY" in sql[0] and "LIMIT 2" in sql[0]


def test_integer_sum_overflow_has_no_publication(tmp_path: Path, source_database: Path) -> None:
    from dataclasses import replace

    from tests.lazy_acceptance_capture import counts

    with sqlite3.connect(source_database) as con:
        con.execute("DELETE FROM orders")
        con.execute("ALTER TABLE orders DROP COLUMN amount")
        con.execute("ALTER TABLE orders ADD COLUMN amount INTEGER")
        con.executemany("INSERT INTO orders(id,amount) VALUES (?,?)", [(1, 2**63 - 1), (2, 1)])
    registry, sidecar = registry_for(source_database)
    entities = dict(registry.entities)
    entity = entities["sales.orders"]
    entities["sales.orders"] = replace(
        entity,
        source=replace(
            entity.source,
            columns=tuple(
                (name, replace(binding, data_type="int64") if name == "amount" else binding)
                for name, binding in entity.source.columns
            ),
        ),
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path / "overflow", "overflow")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    with pytest.raises(sqlite3.OperationalError, match="integer overflow"):
        target.execute()
    assert counts(runtime)["dataset_artifacts"] == 0


@pytest.mark.parametrize("empty", [False, True])
def test_null_and_empty_source(tmp_path: Path, source_database: Path, empty: bool) -> None:
    with sqlite3.connect(source_database) as con:
        con.execute("DELETE FROM orders" if empty else "UPDATE orders SET amount=NULL")
    registry, sidecar = registry_for(source_database)
    runtime = DatasetRuntime.create(tmp_path / "nulls", "nulls")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe((REVENUE, ref.metric("sales.order_count")))
        .aggregate()
    )
    frame = target.execute().to_pandas()
    assert frame.revenue.isna().all()
    assert frame.empty or frame.order_count.tolist() == [0]


def test_undeclared_physical_columns_do_not_expand_admission(
    tmp_path: Path, source_database: Path
) -> None:
    with sqlite3.connect(source_database) as con:
        con.execute("ALTER TABLE orders ADD COLUMN ungoverned BLOB")
        con.execute("UPDATE orders SET ungoverned=x'ABCD'")
    registry, sidecar = registry_for(source_database)
    runtime = DatasetRuntime.create(tmp_path / "declared", "declared")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    assert target.execute().to_pandas().revenue.tolist() == [1058.5]


def _registry_pointed_at_view(
    source_database: Path, view: str
) -> tuple[Registry, CompiledExpressionSidecar]:
    from dataclasses import replace

    from marivo.datasource.ir import TableSourceIR

    registry, sidecar = registry_for(source_database)
    entities = dict(registry.entities)
    entity = entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    entities["sales.orders"] = replace(entity, source=replace(entity.source, table=view))
    registry = replace(registry, entities=entities)
    registry.freeze()
    return registry, sidecar


def test_view_source_journey(tmp_path: Path, source_database: Path) -> None:
    with sqlite3.connect(source_database) as con:
        con.execute("CREATE VIEW orders_view AS SELECT * FROM orders")
    registry, sidecar = _registry_pointed_at_view(source_database, "orders_view")
    runtime = DatasetRuntime.create(tmp_path / "view", "view")
    grouped = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(REVENUE, time_scope=time_scope(start="2026-02-01", end="2026-03-01"))
        .with_dimensions(CHANNEL)
        .aggregate()
        .where(gt(REVENUE, 0))
    )
    frame = (
        grouped.rank(grouped.fields.metric(REVENUE)).limit(2).metric(REVENUE).execute().to_pandas()
    )
    assert frame["revenue"].tolist() == [30.75, 30.75]
    assert frame["channel"].tolist() == ["a", "b"]
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.transferred_rows == 2


def test_expression_view_column_rejected_as_unsupported_physical_type(
    tmp_path: Path, source_database: Path
) -> None:
    from marivo.analysis.materialization.errors import SourceSchemaError
    from tests.lazy_acceptance_capture import counts

    with sqlite3.connect(source_database) as con:
        con.execute(
            "CREATE VIEW expr_view AS SELECT id, amount+1 AS amount, channel, day FROM orders"
        )
    registry, sidecar = _registry_pointed_at_view(source_database, "expr_view")
    runtime = DatasetRuntime.create(tmp_path / "expr-view", "expr-view")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    with pytest.raises(SourceSchemaError) as caught:
        target.execute()
    assert caught.value.reason == "unsupported_physical_type"
    assert caught.value.physical_column == "amount"
    # pragma_table_info reports '' for an expression column; the error renders it as <missing>.
    assert caught.value.actual_type == ""
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
