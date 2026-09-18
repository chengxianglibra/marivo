"""Real query-only SQLite composed methods and retained state acceptance."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.operators.forecast_contracts import naive, periods
from marivo.refs import ref
from tests.lazy_scalar_source_fixtures import registry_for

pytestmark = pytest.mark.runtime
CHANNEL = ref.dimension("sales.orders.channel")
TIME = ref.time_dimension("sales.orders.order_time")


@pytest.fixture
def method_database(tmp_path: Path) -> Path:
    database = tmp_path / "source.sqlite"
    with sqlite3.connect(database) as connection:
        for name in ("orders", "customers", "lines", "snapshots", "validity"):
            connection.execute(
                f'CREATE TABLE {name} (id INTEGER, tenant TEXT, customer_id INTEGER, order_id INTEGER, amount REAL, weight REAL, region TEXT, channel TEXT, day DATE, start DATE, "end" DATE)'
            )
        connection.executemany(
            "INSERT INTO orders(id,customer_id,amount,weight,channel,day) VALUES (?,?,?,?,?,?)",
            [
                (1, 1, 10, 1, "a", "2026-02-01"),
                (2, 1, 20, 3, "a", "2026-02-02"),
                (3, 2, 30, 2, "b", "2026-02-03"),
                (4, 2, None, 4, "b", "2026-02-04"),
                (5, 2, 40, 0, "b", "2026-02-04"),
            ],
        )
        connection.execute(
            "INSERT INTO lines(id,order_id,amount) VALUES (1,1,2),(2,1,3),(3,2,10),(4,3,20),(5,3,30)"
        )
        connection.execute("INSERT INTO customers(id,region) VALUES (1,'EU'),(2,'US')")
        connection.execute(
            "INSERT INTO snapshots(id,day) VALUES (1,'2026-02-27'),(1,'2026-02-28'),(2,'2026-02-28')"
        )
        connection.execute(
            "INSERT INTO validity(id,start,\"end\") VALUES (1,'2026-01-01','2026-02-10'),(1,'2026-02-10',NULL),(2,'2026-02-15','2026-03-01')"
        )
    return database


@pytest.mark.parametrize(
    ("name", "expected", "total"),
    [
        ("mean_amount", [15.0, 35.0], 25.0),
        ("weighted_amount", [17.5, 30.0], 130.0 / 6.0),
        ("conversion_rate", [15.0, 35.0], 25.0),
    ],
)
def test_composed_state(
    tmp_path: Path, method_database: Path, name: str, expected: list[float], total: float
) -> None:
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", name)
    metric = ref.metric(f"sales.{name}")
    logical = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(metric)
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    result = logical.execute()
    assert result.to_pandas().sort_values("channel")[name].tolist() == pytest.approx(expected)
    assert runtime.statistics.transferred_rows == 2
    assert runtime.statistics.transferred_bytes > 0
    assert any(
        role == "primary" and "GROUP BY" in sql for role, sql in runtime.statistics.statements
    )
    method_database.rename(tmp_path / "offline.sqlite")
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert result.rollup(drop_dimensions=(CHANNEL,)).execute().to_pandas()[
        name
    ].tolist() == pytest.approx([total])


def test_relationship(tmp_path: Path, method_database: Path) -> None:
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", "relation")
    result = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(ref.metric("sales.mean_amount"))
        .with_dimensions(ref.dimension("sales.customers.region"))
        .aggregate()
        .execute()
    )
    frame = result.to_pandas().sort_values("region")
    assert frame.region.tolist() == ["EU", "US"]
    assert frame.mean_amount.tolist() == [15.0, 35.0]


@pytest.mark.parametrize("unit", ["day", "week", "month", "quarter", "year"])
def test_date_buckets(
    tmp_path: Path, method_database: Path, unit: Literal["day", "week", "month", "quarter", "year"]
) -> None:
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", unit)
    logical = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(
            ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-02-05")
        )
        .with_time_axis(TIME, grain=grain(unit))
        .aggregate()
    )
    frame = logical.execute().to_pandas()
    expected = {
        "day": [
            ("2026-02-01", 10.0),
            ("2026-02-02", 20.0),
            ("2026-02-03", 30.0),
            ("2026-02-04", 40.0),
        ],
        "week": [("2026-01-26", 10.0), ("2026-02-02", 90.0)],
        "month": [("2026-02-01", 100.0)],
        "quarter": [("2026-01-01", 100.0)],
        "year": [("2026-01-01", 100.0)],
    }
    frame = frame.sort_values("order_time")
    assert list(zip(frame.order_time.astype(str), frame.revenue, strict=True)) == expected[unit]


@pytest.mark.parametrize("entity", ["snapshots", "validity"])
def test_versions(tmp_path: Path, method_database: Path, entity: str) -> None:
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", entity)
    frame = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .population(
            ref.entity(f"sales.{entity}"),
            time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
        )
        .execute()
        .to_pandas()
    )
    assert sorted(frame.entity_identity) == [(1,), (2,)]


def test_forecast_complete_history(tmp_path: Path, method_database: Path) -> None:
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", "forecast")
    history = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(
            ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-02-05")
        )
        .with_time_axis(TIME, grain=grain("day"))
        .aggregate()
    )
    frame = history.forecast(horizon=periods(2), model=naive()).execute().to_pandas()
    assert frame.forecast_value.tolist() == [40.0, 40.0]
    assert frame.training_row_count.tolist() == [4, 4]
    assert runtime.statistics.transferred_rows == 4


def test_kendall_source_reduction(tmp_path: Path, method_database: Path) -> None:
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", "kendall")
    history = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(
            (ref.metric("sales.revenue"), ref.metric("sales.mean_amount")),
            time_scope=time_scope(start="2026-02-01", end="2026-02-05"),
        )
        .with_time_axis(TIME, grain=grain("day"))
        .aggregate()
    )
    frame = history.correlate(method="kendall").execute().to_pandas()
    assert frame.coefficient.tolist() == [1.0]
    assert runtime.statistics.transferred_rows == 4
    assert runtime.statistics.events.get("local_execution_started", 0) > 0


@pytest.mark.parametrize(
    ("entity", "mutation"),
    [
        ("snapshots", "DELETE FROM snapshots WHERE day='2026-02-28'"),
        ("validity", "INSERT INTO validity(id,start,\"end\") VALUES (1,'2026-02-11','2026-02-20')"),
        ("validity", "INSERT INTO validity(id,start,\"end\") VALUES (3,'2026-02-20','2026-02-01')"),
    ],
)
def test_version_failures_do_not_publish(
    tmp_path: Path, method_database: Path, entity: str, mutation: str
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from tests.lazy_acceptance_capture import counts

    with sqlite3.connect(method_database) as connection:
        connection.execute(mutation)
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", "invalid-version")
    logical = runtime.sources(semantic_registry=registry, sidecar=sidecar).population(
        ref.entity(f"sales.{entity}"), time_scope=time_scope(start="2026-02-01", end="2026-03-01")
    )
    with pytest.raises(MaterializationError):
        logical.execute()
    assert counts(runtime)["dataset_artifacts"] == 0


def test_retained_mean_in_fresh_process(tmp_path: Path, method_database: Path) -> None:
    import subprocess
    import sys

    project = tmp_path / "project"
    producer = """
import sys
from pathlib import Path
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.refs import ref
from tests.lazy_scalar_source_fixtures import registry_for
project, database = map(Path, sys.argv[1:])
registry, sidecar = registry_for(database)
runtime = DatasetRuntime.create(project, "producer")
result = runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(ref.metric("sales.mean_amount")).with_dimensions(ref.dimension("sales.orders.channel")).aggregate().execute()
(project / "binding").write_text(runtime.session_ref + "\\n" + result.state.artifact_ref.ref)
"""
    consumer = """
import sys
from pathlib import Path
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.refs import ref
project = Path(sys.argv[1])
session, artifact = (project / "binding").read_text().splitlines()
runtime = DatasetRuntime.open(project, session)
result = runtime.artifact(artifact)
assert sorted(result.to_pandas().mean_amount) == [15.0,35.0]
assert runtime.statistics.primary_queries == 0
assert result.rollup(drop_dimensions=(ref.dimension("sales.orders.channel"),)).execute().to_pandas().mean_amount.tolist() == [25.0]
assert not any(item.domain == "source" for item in runtime.statistics.submissions)
"""
    subprocess.run(
        [sys.executable, "-c", producer, str(project), str(method_database)],
        check=True,
        capture_output=False,
        text=True,
        timeout=60,
    )
    method_database.rename(tmp_path / "offline.sqlite")
    subprocess.run(
        [sys.executable, "-c", consumer, str(project)],
        check=True,
        capture_output=False,
        text=True,
        timeout=60,
    )


def test_time_discovery(tmp_path: Path, method_database: Path) -> None:
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", "discovery")
    history = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(
            ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-02-05")
        )
        .with_time_axis(TIME, grain=grain("day"))
        .aggregate()
    )
    result = history.discover.point_anomalies(threshold=1.0).execute()
    assert len(result.to_pandas()) == 2
    assert runtime.statistics.transferred_rows == 4


def test_dimension_comparison_alignment(tmp_path: Path, method_database: Path) -> None:
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", "comparison")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    metric = ref.metric("sales.mean_amount")
    before = (
        sources.observe(metric, time_scope=time_scope(start="2026-02-01", end="2026-02-03"))
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    after = (
        sources.observe(metric, time_scope=time_scope(start="2026-02-03", end="2026-02-05"))
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    result = after.compare(before).execute()
    frame = result.to_pandas().sort_values("channel")
    assert frame.channel.tolist() == ["a", "b"]
    assert frame.baseline_value.iloc[0] == 15.0
    assert frame.current_value.iloc[1] == 35.0
    assert frame.delta.isna().all()


def test_cross_root_ratio_keeps_contribution_grain(tmp_path: Path, method_database: Path) -> None:
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", "fanout")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.observe(
        (
            ref.metric("sales.revenue"),
            ref.metric("sales.line_revenue"),
            ref.metric("sales.cross_root_ratio"),
        ),
        population=sources.population(ref.entity("sales.customers")),
    ).aggregate()
    frame = logical.execute().to_pandas()
    assert frame.revenue.tolist() == [100.0]
    assert frame.line_revenue.tolist() == [65.0]
    assert frame.cross_root_ratio.tolist() == [0.65]


@pytest.mark.parametrize("missing", [True, False])
def test_relationship_target_integrity(
    tmp_path: Path, method_database: Path, missing: bool
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from tests.lazy_acceptance_capture import counts

    with sqlite3.connect(method_database) as connection:
        connection.execute(
            "DELETE FROM customers WHERE id=2"
            if missing
            else "INSERT INTO customers(id,region) VALUES (2,'other')"
        )
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", "relation-integrity")
    logical = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(ref.metric("sales.revenue"))
        .with_dimensions(ref.dimension("sales.customers.region"))
        .aggregate()
    )
    if missing:
        frame = logical.execute().to_pandas()
        assert frame.loc[frame.region.isna(), "revenue"].tolist() == [70.0]
        assert frame.loc[frame.region == "EU", "revenue"].tolist() == [30.0]
    else:
        with pytest.raises(MaterializationError):
            logical.execute()
        assert counts(runtime)["dataset_artifacts"] == 0


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("UPDATE orders SET weight=0", None),
        ("UPDATE orders SET weight=NULL", None),
        ("UPDATE orders SET amount=NULL", None),
        ("UPDATE orders SET weight=-1", 25.0),
    ],
)
def test_weight_pairs_and_zero_denominator(
    tmp_path: Path, method_database: Path, mutation: str, expected: float | None
) -> None:
    with sqlite3.connect(method_database) as connection:
        connection.execute(mutation)
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", "weights")
    frame = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(ref.metric("sales.weighted_amount"))
        .aggregate()
        .execute()
        .to_pandas()
    )
    if expected is None:
        assert frame.weighted_amount.isna().all()
    else:
        assert frame.weighted_amount.tolist() == [expected]


def test_retained_axis_attribution(tmp_path: Path, method_database: Path) -> None:
    with sqlite3.connect(method_database) as connection:
        connection.execute("UPDATE orders SET channel='a'")
    registry, sidecar = registry_for(method_database)
    runtime = DatasetRuntime.create(tmp_path / "project", "attribution")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    metric = ref.metric("sales.mean_amount")
    before = (
        sources.observe(metric, time_scope=time_scope(start="2026-02-01", end="2026-02-03"))
        .with_dimensions(CHANNEL)
        .aggregate()
        .execute()
    )
    after = (
        sources.observe(metric, time_scope=time_scope(start="2026-02-03", end="2026-02-05"))
        .with_dimensions(CHANNEL)
        .aggregate()
        .execute()
    )
    method_database.rename(tmp_path / "offline.sqlite")
    frame = after.compare(before).attribute(axes=(CHANNEL,)).execute().to_pandas()
    assert frame.contribution.tolist() == [20.0]
    assert frame.overall_delta.tolist() == [20.0]


@pytest.mark.parametrize("change", ["interval", "sentinel"])
def test_unqualified_validity_is_rejected_before_source(
    tmp_path: Path, method_database: Path, change: str
) -> None:
    from dataclasses import replace

    from marivo.analysis.compiler.errors import DatasetCompilationError
    from marivo.semantic.ir import ValidityVersioningIR

    registry, sidecar = registry_for(method_database)
    entity = registry.entities["sales.validity"]
    assert isinstance(entity.versioning, ValidityVersioningIR)
    version = (
        replace(entity.versioning, interval="closed_closed")
        if change == "interval"
        else replace(entity.versioning, open_end=(None, "9999-12-31"))
    )
    registry = replace(
        registry,
        entities={**registry.entities, "sales.validity": replace(entity, versioning=version)},
    )
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path / "project", "unqualified-version")
    logical = runtime.sources(semantic_registry=registry, sidecar=sidecar).population(
        ref.entity("sales.validity"), time_scope=time_scope(start="2026-02-01", end="2026-03-01")
    )
    with pytest.raises(DatasetCompilationError):
        logical.execute()
    assert not runtime.statistics.statements
