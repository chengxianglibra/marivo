"""Independent Runtime oracles for admitted folds and adjacent unsafe reductions."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import duckdb
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.reads import part_schema, read_part_batches
from marivo.analysis.observation.fold_contracts import coverage_columns
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import all_of, any_of, eq, gt, not_eq
from marivo.refs import ref
from marivo.semantic.ir import (
    CumulativeComposition,
    LinearComposition,
    LinearTerm,
    SemiAdditive,
    TimeFoldIR,
)
from tests.lazy_retained_fixtures import setup_retained

REVENUE = ref.metric("sales.revenue")
MEAN = ref.metric("sales.mean_amount")
WEIGHTED = ref.metric("sales.weighted_amount")
CUSTOMERS = ref.entity("sales.customers")
REGION = ref.dimension("sales.customers.region")
DAY = ref.time_dimension("sales.orders.order_time")
WINDOW = time_scope(start="2026-02-02", end="2026-02-05")


def _coverage(runtime: DatasetRuntime, value: MaterializedMetricDataset) -> dict[str, object]:
    record = runtime.store.artifact(value.state.artifact_ref.ref)
    assert record is not None and len(record.descriptor.retained_parts) == 1
    part = record.descriptor.retained_parts[0]
    schema = part_schema(runtime.store.project_root, part)
    batches = tuple(read_part_batches(runtime.store.project_root, part, expected_schema=schema))
    assert sum(batch.num_rows for batch in batches) == 1
    return next(batch.to_pylist()[0] for batch in batches if batch.num_rows)


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_complete_aggregate_matrix_matches_source_and_retained_runtime(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    original = fixture.sources._owner.semantic_registry
    metrics = dict(original.metrics)
    for name, operation in (("minimum", "min"), ("maximum", "max")):
        metrics[f"sales.{name}"] = replace(
            metrics[REVENUE.path],
            semantic_id=f"sales.{name}",
            name=name,
            aggregation=operation,
        )
    metrics["sales.weight_total"] = replace(
        metrics[REVENUE.path],
        semantic_id="sales.weight_total",
        name="weight_total",
        measure="sales.orders.weight",
        aggregation_target="sales.orders.weight",
    )
    metrics["sales.net"] = replace(
        metrics["sales.conversion_rate"],
        semantic_id="sales.net",
        name="net",
        composition=LinearComposition(
            (LinearTerm("+", REVENUE.path), LinearTerm("-", "sales.weight_total"))
        ),
    )
    registry = replace(original, metrics=metrics)
    registry.freeze()
    sources = fixture.runtime.sources(
        semantic_registry=registry, sidecar=fixture.sources._owner.sidecar
    )
    expected = {
        "revenue": 147,
        "order_count": 5,
        "minimum": 0,
        "maximum": 100,
        "mean_amount": 147 / 5,
        "weighted_amount": 50,
        "cross_root_ratio": 65 / 147,
        "net": 137,
    }
    logical = sources.observe(
        tuple(ref.metric(f"sales.{name}") for name in expected),
        population=sources.population(CUSTOMERS),
    )
    source_total = logical.aggregate().execute().to_pandas().iloc[0].to_dict()
    checkpoint = logical.execute()
    fixture.database.rename(tmp_path / "origin-offline.duckdb")
    materialized = checkpoint.aggregate().execute()
    retained_total = materialized.to_pandas().iloc[0].to_dict()
    assert source_total == pytest.approx(expected)
    assert retained_total == pytest.approx(expected)
    assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    assert fixture.runtime.statistics.events.get("credential_resolution", 0) == 0
    assert (fixture.runtime.statistics.worker_pid is not None) is (kind == "local")
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_time_dimension_rollup_and_drop_time_have_identical_retained_meaning(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    daily = (
        fixture.sources.observe((REVENUE, MEAN, WEIGHTED), time_scope=WINDOW)
        .with_dimensions(REGION)
        .with_time_axis(DAY, grain=grain("day"))
        .aggregate()
    )
    combined = daily.rollup(drop_dimensions=(REGION,), grain=grain("month"))
    chained = daily.rollup(grain=grain("month")).rollup(drop_dimensions=(REGION,))
    assert combined.definition_fingerprint == chained.definition_fingerprint
    source_rows = combined.execute().to_pandas().to_dict("records")
    checkpoint = daily.execute()
    fixture.database.rename(tmp_path / "origin-offline.duckdb")
    retained_combined = checkpoint.rollup(drop_dimensions=(REGION,), grain=grain("month"))
    retained_chain = checkpoint.rollup(grain=grain("month")).rollup(drop_dimensions=(REGION,))
    assert retained_combined.definition_fingerprint == retained_chain.definition_fingerprint
    monthly = retained_combined.execute()
    assert monthly.to_pandas().to_dict("records") == source_rows
    row = monthly.to_pandas().iloc[0]
    assert row["order_time"] == date(2026, 2, 1)
    assert row["revenue"] == 140 and row["mean_amount"] == 35 and row["weighted_amount"] == 50
    total = checkpoint.rollup(drop_dimensions=(REGION,), drop_time=True).execute().to_pandas()
    assert total.iloc[0].to_dict() == {"revenue": 140, "mean_amount": 35, "weighted_amount": 50}
    for invalid in (grain("day"), grain("hour")):
        with pytest.raises(DatasetConstructionError, match="strictly coarser"):
            checkpoint.rollup(grain=invalid)
    with pytest.raises(DatasetConstructionError):
        checkpoint.rollup(grain=grain("month"), drop_time=True)
    with pytest.raises(DatasetConstructionError):
        checkpoint.rollup(drop_time=1)
    with pytest.raises(DatasetConstructionError, match="strictly coarser"):
        checkpoint.rollup(grain=grain("week")).rollup(grain=grain("month"))
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_cumulative_runtime_preserves_exact_endpoint_and_partial_selection(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    original = fixture.sources._owner.semantic_registry
    running = ref.metric("sales.running")
    metrics = {
        **original.metrics,
        running.path: replace(
            original.metrics["sales.conversion_rate"],
            semantic_id=running.path,
            name="running",
            composition=CumulativeComposition(REVENUE.path, DAY.path, "all_history"),
        ),
    }
    registry = replace(original, metrics=metrics)
    registry.freeze()
    sources = fixture.runtime.sources(
        semantic_registry=registry, sidecar=fixture.sources._owner.sidecar
    )
    daily = (
        sources.observe(running, time_scope=WINDOW)
        .with_time_axis(DAY, grain=grain("day"))
        .aggregate()
    )
    source_month = daily.rollup(grain=grain("month")).execute()
    assert source_month.to_pandas()["running"].tolist() == [140]
    checkpoint = daily.execute()
    fixture.database.rename(tmp_path / "origin-offline.duckdb")
    endpoint, coverage_start, coverage_end, seconds, complete = coverage_columns(
        checkpoint.row_contract.family_semantics.metric_folds[0]
    )
    for logical, expected_seconds, expected_complete in (
        (checkpoint.rollup(drop_time=True), 3 * 86400, True),
        (checkpoint.where(gt(running, 120)).rollup(drop_time=True), 2 * 86400, False),
        (checkpoint.rollup(grain=grain("month")), 3 * 86400, False),
    ):
        result = logical.execute()
        assert result.to_pandas()["running"].tolist() == [140]
        state = _coverage(fixture.runtime, result)
        assert state[endpoint] == datetime(2026, 2, 5)
        assert state[seconds] == expected_seconds
        assert state[complete] is expected_complete
    empty = checkpoint.where(gt(running, 1000)).rollup(drop_time=True).execute()
    assert empty.to_pandas().shape[0] == 1 and empty.to_pandas()["running"].isna().all()
    state = _coverage(fixture.runtime, empty)
    assert all(state[name] is None for name in (endpoint, coverage_start, coverage_end))
    assert state[seconds] == 0 and state[complete] is False
    assert all(value == 0 for name, value in state.items() if name.endswith("count"))
    assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("kind", ["local", "engine"])
@pytest.mark.parametrize("fold", ["max", "last", "mean"])
def test_projected_device_temporal_folds_never_become_spatial_sums(
    tmp_path: Path, kind: Literal["local", "engine"], fold: Literal["max", "last", "mean"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    with duckdb.connect(str(fixture.database)) as database:
        database.execute("DELETE FROM orders")
        database.execute(
            "INSERT INTO orders (id, customer_id, amount, day) VALUES (1,1,10,DATE '2026-02-02'), (2,1,0,DATE '2026-02-03'), (3,2,0,DATE '2026-02-02'), (4,2,10,DATE '2026-02-03')"
        )
        if fold != "max":
            database.execute("DELETE FROM orders WHERE id=2")
    original = fixture.sources._owner.semantic_registry
    measures = {
        **original.measures,
        "sales.orders.amount": replace(
            original.measures["sales.orders.amount"],
            additivity=SemiAdditive(DAY.path, TimeFoldIR(fold)),
        ),
    }
    registry = replace(original, measures=measures)
    registry.freeze()
    sources = fixture.runtime.sources(
        semantic_registry=registry, sidecar=fixture.sources._owner.sidecar
    )
    logical = sources.observe(REVENUE, population=sources.population(CUSTOMERS), time_scope=WINDOW)
    assert logical.aggregate().execute().to_pandas()["revenue"].tolist() == [10]
    checkpoint = logical.execute()
    if fold == "max":
        assert checkpoint.to_pandas()["revenue"].dropna().tolist() == [10, 10]
    with pytest.raises(DatasetConstructionError, match="unsupported entity fold"):
        checkpoint.aggregate()
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_overlapping_tags_cannot_fold_one_order_into_twice_its_total(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    with duckdb.connect(str(fixture.database)) as database:
        database.execute("DELETE FROM orders")
        database.execute("DELETE FROM lines")
        database.execute("INSERT INTO orders (id, customer_id, amount) VALUES (1,1,100)")
        database.execute("INSERT INTO lines (id, order_id, channel) VALUES (1,1,'a'), (2,1,'b')")
    original = fixture.sources._owner.semantic_registry
    tag = ref.dimension("sales.lines.tag")
    registry = replace(
        original,
        metrics={
            **original.metrics,
            REVENUE.path: replace(
                original.metrics[REVENUE.path], fanout_policy="aggregate_then_join"
            ),
        },
        dimensions={
            **original.dimensions,
            tag.path: replace(
                original.dimensions["sales.orders.channel"],
                semantic_id=tag.path,
                entity="sales.lines",
                name="tag",
            ),
        },
    )
    registry.freeze()
    sources = fixture.runtime.sources(
        semantic_registry=registry, sidecar=fixture.sources._owner.sidecar
    )
    observed = sources.observe(REVENUE)
    assert observed.aggregate().execute().to_pandas()["revenue"].tolist() == [100]
    logical = observed.with_dimensions(tag).aggregate()
    checkpoint = logical.execute()
    assert checkpoint.to_pandas()["revenue"].tolist() == [100, 100]
    for receiver in (logical, checkpoint):
        with pytest.raises(DatasetConstructionError, match="overlapping"):
            receiver.rollup(drop_dimensions=(tag,))
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("case", ["median", "percentile", "cumulative"])
def test_nonmergeable_entity_reduction_rejects_all_metrics_atomically(
    tmp_path: Path, case: str
) -> None:
    fixture = setup_retained(tmp_path)
    original = fixture.sources._owner.semantic_registry
    unsupported = ref.metric("sales.unsupported")
    changed = replace(
        original.metrics[REVENUE.path],
        semantic_id=unsupported.path,
        name="unsupported",
        aggregation="median" if case == "median" else ("percentile", 0.9),
    )
    if case == "cumulative":
        changed = replace(
            original.metrics["sales.conversion_rate"],
            semantic_id=unsupported.path,
            name="unsupported",
            composition=CumulativeComposition(REVENUE.path, DAY.path, "all_history"),
        )
    registry = replace(original, metrics={**original.metrics, unsupported.path: changed})
    registry.freeze()
    sources = fixture.runtime.sources(
        semantic_registry=registry, sidecar=fixture.sources._owner.sidecar
    )
    checkpoint = sources.observe(
        (REVENUE, unsupported), population=sources.population(CUSTOMERS), time_scope=WINDOW
    ).execute()
    with pytest.raises(DatasetConstructionError, match="unsupported entity fold"):
        checkpoint.aggregate()
    assert len(checkpoint.schema.columns) == 3
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_selected_rate_retains_eight_of_ten_instead_of_original_denominator(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    with duckdb.connect(str(fixture.database)) as database:
        database.execute("DELETE FROM orders")
        database.execute(
            "INSERT INTO orders (id, customer_id, amount) SELECT i, CASE WHEN i<=10 THEN 1 ELSE 2 END, CASE WHEN i<=8 OR i=11 THEN 1 ELSE 0 END FROM generate_series(1,20) AS source(i)"
        )
    rate = ref.metric("sales.conversion_rate")
    logical = fixture.sources.observe(rate, population=fixture.sources.population(CUSTOMERS))
    source_selected = logical.where(gt(rate, 0.5)).aggregate().execute()
    assert source_selected.to_pandas()["conversion_rate"].tolist() == [0.8]
    checkpoint = logical.execute()
    fixture.database.rename(tmp_path / "origin-offline.duckdb")
    selected = checkpoint.where(gt(rate, 0.5)).execute()
    assert selected.to_pandas()["entity_identity"].tolist() == [(1,)]
    output = selected.aggregate().execute()
    assert output.to_pandas()["conversion_rate"].tolist() == [0.8]
    assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_cumulative_dimension_fold_requires_contiguous_aligned_coverage(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    with duckdb.connect(str(fixture.database)) as database:
        database.execute("DELETE FROM orders")
        database.execute("UPDATE customers SET region='US' WHERE id=2")
        database.execute(
            "INSERT INTO orders (id,customer_id,amount,day) SELECT i, CASE WHEN i<=4 THEN 1 ELSE 2 END, 1, DATE '2026-02-02' + CAST((i-1)%4 AS INTEGER) FROM generate_series(1,8) AS source(i)"
        )
    original = fixture.sources._owner.semantic_registry
    running = ref.metric("sales.running")
    registry = replace(
        original,
        metrics={
            **original.metrics,
            running.path: replace(
                original.metrics["sales.conversion_rate"],
                semantic_id=running.path,
                name="running",
                composition=CumulativeComposition(REVENUE.path, DAY.path, "all_history"),
            ),
        },
    )
    registry.freeze()
    sources = fixture.runtime.sources(
        semantic_registry=registry, sidecar=fixture.sources._owner.sidecar
    )
    daily = (
        sources.observe(running, time_scope=time_scope(start="2026-02-02", end="2026-02-06"))
        .with_dimensions(REGION)
        .with_time_axis(DAY, grain=grain("day"))
        .aggregate()
    )
    selected = any_of(
        all_of(eq(REGION, "EU"), not_eq(DAY, date(2026, 2, 4))),
        all_of(eq(REGION, "US"), not_eq(DAY, date(2026, 2, 3))),
    )
    assert daily.rollup(grain=grain("month"), drop_dimensions=(REGION,)).execute().to_pandas()[
        "running"
    ].tolist() == [8]
    with pytest.raises(MaterializationError):
        daily.where(selected).rollup(grain=grain("month"), drop_dimensions=(REGION,)).execute()
    checkpoint = daily.execute()
    fixture.database.rename(tmp_path / "origin-offline.duckdb")
    clipped = checkpoint.rollup(grain=grain("month"), drop_dimensions=(REGION,)).execute()
    assert clipped.to_pandas()["running"].tolist() == [8]
    endpoint, start, end, seconds, complete = coverage_columns(
        checkpoint.row_contract.family_semantics.metric_folds[0]
    )
    clipped_state = _coverage(fixture.runtime, clipped)
    assert clipped_state[start] == datetime(2026, 2, 2) and clipped_state[end] == datetime(
        2026, 2, 6
    )
    assert clipped_state[seconds] == 4 * 86400 and clipped_state[complete] is False
    noncontiguous = checkpoint.where(selected).rollup(grain=grain("month")).execute()
    assert noncontiguous.to_pandas()["running"].tolist() == [4, 4]
    record = fixture.runtime.store.artifact(noncontiguous.state.artifact_ref.ref)
    assert record is not None
    part = record.descriptor.retained_parts[0]
    schema = part_schema(tmp_path, part)
    rows = [
        row
        for batch in read_part_batches(tmp_path, part, expected_schema=schema)
        for row in batch.to_pylist()
    ]
    assert len(rows) == 2
    assert [
        tuple(row[name] for name in (endpoint, start, end, seconds, complete)) for row in rows
    ] == [
        (datetime(2026, 2, 6), datetime(2026, 2, 2), datetime(2026, 2, 6), 3 * 86400, False),
        (datetime(2026, 2, 6), datetime(2026, 2, 2), datetime(2026, 2, 6), 3 * 86400, False),
    ]
    with pytest.raises(MaterializationError):
        noncontiguous.rollup(drop_dimensions=(REGION,)).execute()
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_entity_key_distinct_has_exact_runtime_fold_without_origin(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    original = fixture.sources._owner.semantic_registry
    distinct = ref.metric("sales.distinct_orders")
    generic = ref.metric("sales.distinct_amounts")
    registry = replace(
        original,
        metrics={
            **original.metrics,
            distinct.path: replace(
                original.metrics[REVENUE.path],
                semantic_id=distinct.path,
                name="distinct_orders",
                aggregation="count_distinct",
                measure=None,
                aggregation_target="sales.orders",
                aggregation_target_kind="entity",
            ),
            generic.path: replace(
                original.metrics[REVENUE.path],
                semantic_id=generic.path,
                name="distinct_amounts",
                aggregation="count_distinct",
            ),
        },
    )
    registry.freeze()
    sources = fixture.runtime.sources(
        semantic_registry=registry, sidecar=fixture.sources._owner.sidecar
    )
    source = sources.observe(distinct, population=sources.population(ref.entity("sales.orders")))
    assert source.aggregate().execute().to_pandas()["distinct_orders"].tolist() == [6]
    checkpoint = source.execute()
    assert checkpoint.to_pandas()["distinct_orders"].tolist() == [1] * 6
    generic_checkpoint = sources.observe(generic).execute()
    with pytest.raises(DatasetConstructionError, match="component fold"):
        generic_checkpoint.aggregate()
    fixture.database.rename(tmp_path / "origin-offline.duckdb")
    result = checkpoint.aggregate().execute()
    assert result.to_pandas()["distinct_orders"].tolist() == [6]
    assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    assert fixture.runtime.statistics.events.get("credential_resolution", 0) == 0
    assert (fixture.runtime.statistics.worker_pid is not None) is (kind == "local")
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()
