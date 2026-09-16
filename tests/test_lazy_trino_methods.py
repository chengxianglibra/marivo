"""Real read-only Trino composed Metric and local suffix acceptance."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.operators.forecast_contracts import naive, periods
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_acceptance_capture import counts
from tests.lazy_scalar_source_fixtures import registry_for
from tests.multisource_environment import trino_analysis as trino

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1", reason="opt-in Trino service"
    ),
]
CHANNEL = ref.dimension("sales.orders.channel")
TIME = ref.time_dimension("sales.orders.order_time")


def _method_registry(
    table: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[Registry, CompiledExpressionSidecar]:
    return registry_for(Path("unused"), engine="trino", table=table)


class _Rows(Protocol):
    def fetchall(self) -> object: ...


class _AdminCursor(Protocol):
    def execute(self, query: str) -> _Rows: ...
    def close(self) -> None: ...


@contextmanager
def _admin() -> Iterator[_AdminCursor]:
    with trino.connection(admin=True) as con:
        cur: _AdminCursor = con.cursor()
        try:
            yield cur
        finally:
            cur.close()


@pytest.fixture
def method_table() -> Iterator[str]:
    name = "methods_" + uuid4().hex
    with _admin() as admin:
        admin.execute(
            f'CREATE TABLE {name} (id BIGINT, tenant VARCHAR, customer_id BIGINT, order_id BIGINT, amount DOUBLE PRECISION, weight DOUBLE PRECISION, region VARCHAR, channel VARCHAR, day DATE, start DATE, "end" DATE)'
        ).fetchall()
        admin.execute(
            f"INSERT INTO {name} (id, amount, weight, channel, day) VALUES (1,10,1,'a',DATE '2026-02-01'),(2,20,3,'a',DATE '2026-02-02'),(3,30,2,'b',DATE '2026-02-03'),(4,NULL,4,'b',DATE '2026-02-04'),(5,40,0,'b',DATE '2026-02-04')"
        ).fetchall()
        try:
            yield name
        finally:
            admin.execute(f"DROP TABLE IF EXISTS {name}").fetchall()


@pytest.mark.parametrize(
    ("name", "expected", "total"),
    [
        ("mean_amount", [15.0, 35.0], 25.0),
        ("weighted_amount", [17.5, 30.0], 130.0 / 6.0),
        ("conversion_rate", [15.0, 35.0], 25.0),
    ],
)
def test_composed_metrics_preserve_sufficient_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
    name: str,
    expected: list[float],
    total: float,
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "trino-composed")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    metric = ref.metric(f"sales.{name}")
    logical = sources.observe(metric).with_dimensions(CHANNEL).aggregate()
    result = logical.execute()
    frame = result.to_pandas().sort_values("channel")
    assert frame[name].astype(float).tolist() == pytest.approx(expected)
    primary = [statement for role, statement in runtime.statistics.statements if role == "primary"]
    assert len(primary) == 1 and "GROUP BY" in primary[0]
    assert runtime.statistics.transferred_rows == 2
    assert runtime.statistics.transferred_bytes > 0
    before = counts(runtime)
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert counts(runtime) == before
    folded = result.rollup(drop_dimensions=(CHANNEL,)).execute().to_pandas()
    assert float(folded[name].iloc[0]) == pytest.approx(total)


def test_date_series_forecast_receives_complete_reduced_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_table: str
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "trino-forecast")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(
            ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-02-05")
        )
        .with_time_axis(TIME, grain=grain("day"))
        .aggregate()
        .forecast(horizon=periods(2), model=naive())
    )
    frame = logical.execute().to_pandas()
    assert frame.forecast_value.tolist() == [40.0, 40.0]
    assert frame.training_row_count.tolist() == [4, 4]
    assert runtime.statistics.transferred_rows == 4
    primary = [statement for role, statement in runtime.statistics.statements if role == "primary"]
    assert len(primary) == 1 and "GROUP BY" in primary[0]


def test_grouped_kendall_uses_complete_source_reduction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_table: str
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "trino-kendall")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")])
        .with_time_axis(TIME, grain=grain("day"))
        .aggregate()
        .correlate(method="kendall")
    )
    frame = logical.execute().to_pandas()
    assert frame.coefficient.tolist() == pytest.approx([1.0])
    assert runtime.statistics.transferred_rows == 4


def test_grouped_compare_and_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_table: str
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "trino-attribution")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.observe(ref.metric("sales.mean_amount")).with_dimensions(CHANNEL).aggregate()
    result = logical.compare(logical).attribute(axes=(CHANNEL,)).execute()
    frame = result.to_pandas()
    assert frame.contribution.tolist() == pytest.approx([0.0, 0.0])
    assert runtime.statistics.primary_queries == 1


def test_relationship_dimension_reads_same_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_table: str
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    entities = dict(registry.entities)
    customer = entities["sales.customers"]
    assert isinstance(customer.source, TableSourceIR)
    entities["sales.customers"] = replace(
        customer,
        source=replace(
            customer.source,
            table=method_table,
            database="analysis",
            columns=customer.source.columns,
        ),
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    with _admin() as admin:
        admin.execute(f"UPDATE {method_table} SET customer_id = id, region = channel").fetchall()
    runtime = DatasetRuntime.create(tmp_path, "trino-relations")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(ref.metric("sales.mean_amount"))
        .with_dimensions(ref.dimension("sales.customers.region"))
        .aggregate()
    )
    frame = logical.execute().to_pandas().sort_values("region")
    assert frame.mean_amount.tolist() == pytest.approx([15.0, 35.0])
    assert any(
        (
            "JOIN" in statement
            for role, statement in runtime.statistics.statements
            if role == "primary"
        )
    )


@pytest.mark.parametrize(
    "entity_name,case",
    [
        ("snapshots", "valid"),
        ("snapshots", "empty"),
        ("snapshots", "overlap"),
        ("validity", "valid"),
        ("validity", "empty"),
        ("validity", "overlap"),
        ("validity", "reversed"),
    ],
)
def test_version_selection_preserves_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_table: str, entity_name: str, case: str
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError

    registry, sidecar = _method_registry(method_table, monkeypatch)
    entities = dict(registry.entities)
    entity = entities[f"sales.{entity_name}"]
    assert isinstance(entity.source, TableSourceIR)
    entities[f"sales.{entity_name}"] = replace(
        entity,
        source=replace(
            entity.source, table=method_table, database="analysis", columns=entity.source.columns
        ),
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    with _admin() as admin:
        admin.execute(
            f"UPDATE {method_table} SET day = DATE '2026-02-28', start = DATE '2026-02-01', \"end\" = NULL"
        ).fetchall()
        if case == "empty":
            admin.execute(
                f"UPDATE {method_table} SET day = DATE '2026-03-01', start = DATE '2026-03-01'"
            ).fetchall()
        elif case == "overlap":
            admin.execute(f"UPDATE {method_table} SET id = 1").fetchall()
        elif case == "reversed":
            admin.execute(
                f"UPDATE {method_table} SET start = DATE '2026-03-02', \"end\" = DATE '2026-03-01'"
            ).fetchall()
    runtime = DatasetRuntime.create(tmp_path, "trino-versions")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.population(
        ref.entity(f"sales.{entity_name}"),
        time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
    )
    if case in {"overlap", "reversed"} or (entity_name == "snapshots" and case == "empty"):
        with pytest.raises(MaterializationError):
            logical.execute()
        assert runtime.statistics.primary_queries == 0
        return
    result = logical.execute()
    assert sorted(result.to_pandas().entity_identity.tolist()) == (
        [] if case == "empty" else [(1,), (2,), (3,), (4,), (5,)]
    )
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert (
        record is not None and record.descriptor.population_authority.version_selection is not None
    )


@pytest.mark.parametrize("unit", ["day", "week", "month", "quarter", "year"])
def test_civil_date_bucket_reduction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_table: str,
    unit: Literal["day", "week", "month", "quarter", "year"],
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "trino-date-bucket")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(ref.metric("sales.revenue"))
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


def test_entity_mean_preserves_identity_and_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_table: str
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "trino-entity-mean")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    frame = sources.observe(ref.metric("sales.mean_amount")).execute().to_pandas()
    assert sorted(frame.entity_identity.tolist()) == [(1,), (2,), (3,), (4,), (5,)]
    assert frame.mean_amount.dropna().sort_values().tolist() == [10.0, 20.0, 30.0, 40.0]
    assert frame.mean_amount.isna().sum() == 1


def test_time_discovery_consumes_complete_grouped_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_table: str
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "trino-discovery")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(
            ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-02-05")
        )
        .with_time_axis(TIME, grain=grain("day"))
        .aggregate()
        .discover.point_anomalies()
    )
    result = logical.execute()
    assert runtime.statistics.transferred_rows == 4
    assert result.to_pandas() is not None


@pytest.mark.parametrize("weight", [-1.0, 0.0])
def test_signed_and_zero_weight_groups_follow_null_denominator_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_table: str, weight: float
) -> None:
    with _admin() as admin:
        admin.execute(
            f"UPDATE {method_table} SET weight = %s".replace("%s", str(weight))
        ).fetchall()
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "trino-invalid-weights")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    frame = sources.observe(ref.metric("sales.weighted_amount")).aggregate().execute().to_pandas()
    if weight == 0:
        assert frame.weighted_amount.isna().all()
    else:
        assert frame.weighted_amount.tolist() == [25.0]


def test_empty_and_null_composed_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_table: str
) -> None:
    with _admin() as admin:
        admin.execute(f"UPDATE {method_table} SET amount = NULL").fetchall()
    registry, sidecar = _method_registry(method_table, monkeypatch)
    runtime = DatasetRuntime.create(tmp_path, "trino-null-composed")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    frame = (
        sources.observe([ref.metric("sales.mean_amount"), ref.metric("sales.conversion_rate")])
        .aggregate()
        .execute()
        .to_pandas()
    )
    assert frame.mean_amount.isna().all() and frame.conversion_rate.isna().all()


def test_one_to_many_cross_root_ratio_keeps_contribution_grain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_table: str
) -> None:
    registry, sidecar = _method_registry(method_table, monkeypatch)
    entities = dict(registry.entities)
    entity = entities["sales.lines"]
    assert isinstance(entity.source, TableSourceIR)
    entities["sales.lines"] = replace(
        entity, source=replace(entity.source, table=method_table, database="analysis")
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    with _admin() as admin:
        admin.execute(f"UPDATE {method_table} SET order_id = 1").fetchall()
    runtime = DatasetRuntime.create(tmp_path, "trino-contribution")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    result = (
        sources.observe(
            ref.metric("sales.cross_root_ratio"),
            population=sources.population(ref.entity("sales.orders")),
        )
        .aggregate()
        .execute()
    )
    assert result.to_pandas().cross_root_ratio.tolist() == pytest.approx([1.0])


@pytest.mark.parametrize("bad", ["duplicate", "missing"])
def test_relation_target_integrity_is_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_table: str, bad: str
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError

    registry, sidecar = _method_registry(method_table, monkeypatch)
    entities = dict(registry.entities)
    entity = entities["sales.customers"]
    assert isinstance(entity.source, TableSourceIR)
    entities["sales.customers"] = replace(
        entity, source=replace(entity.source, table=method_table, database="analysis")
    )
    registry = replace(registry, entities=entities)
    registry.freeze()
    with _admin() as admin:
        admin.execute(f"UPDATE {method_table} SET customer_id = 999, region = channel").fetchall()
        if bad == "duplicate":
            admin.execute(f"UPDATE {method_table} SET id = 1").fetchall()
    runtime = DatasetRuntime.create(tmp_path, "trino-relation-invalid")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(ref.metric("sales.revenue"))
        .with_dimensions(ref.dimension("sales.customers.region"))
        .aggregate()
    )
    if bad == "duplicate":
        with pytest.raises(MaterializationError):
            logical.execute()
        assert runtime.statistics.primary_queries == 0
    else:
        frame = logical.execute().to_pandas()
        assert frame.region.isna().all()
        assert frame.revenue.tolist() == [100.0]


def test_retained_mean_in_fresh_process(tmp_path: Path, method_table: str) -> None:
    import subprocess
    import sys

    project = tmp_path / "project"
    producer = """
import sys
from pathlib import Path
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.refs import ref
from tests.lazy_scalar_source_fixtures import registry_for
project = Path(sys.argv[1])
table = sys.argv[2]
registry, sidecar = registry_for(Path("unused"), engine="trino", table=table)
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
assert not any(role == "source_schema" for role, _ in runtime.statistics.statements)
"""
    subprocess.run(
        [sys.executable, "-c", producer, str(project), method_table],
        check=True,
        capture_output=False,
        text=True,
        timeout=60,
    )
    with _admin() as admin:
        admin.execute(f"DROP TABLE {method_table}").fetchall()
    subprocess.run(
        [sys.executable, "-c", consumer, str(project)],
        check=True,
        capture_output=False,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("variant", ["closed_closed", "sentinel", "unresolved_decimal"])
def test_unqualified_source_shape_rejected_before_connect(tmp_path: Path, variant: str) -> None:
    from marivo.analysis.compiler.errors import DatasetCompilationError
    from marivo.semantic.ir import ValidityVersioningIR

    registry, sidecar = registry_for(tmp_path / "unused", engine="trino")
    entities = dict(registry.entities)
    name = "sales.orders" if variant == "unresolved_decimal" else "sales.validity"
    entity = entities[name]
    assert isinstance(entity.source, TableSourceIR)
    if variant == "unresolved_decimal":
        entity = replace(
            entity,
            source=replace(
                entity.source,
                columns=tuple(
                    (key, replace(value, data_type="decimal") if key == "amount" else value)
                    for key, value in entity.source.columns
                ),
            ),
        )
    else:
        assert isinstance(entity.versioning, ValidityVersioningIR)
        version = (
            replace(entity.versioning, interval="closed_closed")
            if variant == "closed_closed"
            else replace(entity.versioning, open_end=("9999-12-31",))
        )
        entity = replace(entity, versioning=version)
    entities[name] = entity
    registry = replace(registry, entities=entities)
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path, "unqualified")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = (
        sources.observe(ref.metric("sales.revenue")).aggregate()
        if variant == "unresolved_decimal"
        else sources.population(
            ref.entity(name), time_scope=time_scope(start="2026-02-01", end="2026-03-01")
        )
    )
    with pytest.raises(DatasetCompilationError, match=r"Decimal|validity"):
        logical.execute()
    assert runtime.statistics.events.get("backend_connect", 0) == 0
    assert runtime.statistics.primary_queries == 0


def test_retained_axis_attribution_without_source(tmp_path: Path, method_table: str) -> None:
    with _admin() as admin:
        admin.execute(f"UPDATE {method_table} SET channel = 'a'").fetchall()
    registry, sidecar = registry_for(tmp_path / "unused", engine="trino", table=method_table)
    runtime = DatasetRuntime.create(tmp_path, "retained-attribution")
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
    with _admin() as admin:
        admin.execute(f"DROP TABLE {method_table}").fetchall()
    queries = runtime.statistics.primary_queries
    frame = after.compare(before).attribute(axes=(CHANNEL,)).execute().to_pandas()
    assert frame.contribution.tolist() == [20.0]
    assert frame.overall_delta.tolist() == [20.0]
    assert runtime.statistics.primary_queries == queries
