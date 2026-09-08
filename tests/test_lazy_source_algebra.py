"""Real-source numerical oracles for Slice 3a Metric algebra."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from marivo._temporal import (
    Grain,
    PeriodCalendarSnapshotV1,
    certify_period_calendar,
    semantic_grain,
)
from marivo.analysis import grain, time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.observation.contracts import metric_definition
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from marivo.semantic.ir import (
    AggKind,
    CumulativeAnchor,
    CumulativeComposition,
    LinearComposition,
    LinearTerm,
    PeriodCalendarIR,
    RatioComposition,
    SemiAdditive,
    TimeFoldIR,
)
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import ExecutionFixture, execution_fixture
from tests.lazy_observation_fixtures import NoIoActionPort

REVENUE = ref.metric("sales.revenue")
DAY = ref.time_dimension("sales.orders.order_time")
WINDOW = time_scope(start="2026-02-01", end="2026-02-05")


def _rebind(
    fixture: ExecutionFixture,
    registry: Registry,
    snapshots: tuple[PeriodCalendarSnapshotV1, ...] = (),
) -> ExecutionFixture:
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=fixture.sidecar,
        action_port=NoIoActionPort(),
        session_id="source-numerical",
        store_id="source-numerical",
        period_calendar_snapshots=snapshots,
    )
    return replace(fixture, registry=registry, sources=sources)


def _with_fiscal_calendar(original: ExecutionFixture) -> tuple[ExecutionFixture, Grain]:
    calendar_ref = ref.period_calendar("sales.fiscal")
    snapshot = certify_period_calendar(
        calendar_ref=calendar_ref,
        boundary_timezone="UTC",
        coverage=(date(2026, 2, 1), date(2026, 2, 5)),
        rows=tuple(
            {"date": date(2026, 2, day), "period": "A" if day < 3 else "B"} for day in range(1, 5)
        ),
        levels={"reporting_period": "period"},
    )
    registry = replace(
        original.registry,
        period_calendars=dict(original.registry.period_calendars),
        metrics=dict(original.registry.metrics),
    )
    template = registry.metrics[REVENUE.path]
    registry.period_calendars[calendar_ref.path] = PeriodCalendarIR(
        calendar_ref.path,
        "sales",
        "fiscal",
        DAY.path,
        "UTC",
        ("2026-02-01", "2026-02-05"),
        (("reporting_period", "sales.orders.channel"),),
        template.ai_context,
        "fiscal",
        template.location,
    )
    reporting_grain = semantic_grain(calendar=calendar_ref, level="reporting_period")
    registry.metrics["sales.running"] = replace(
        registry.metrics["sales.conversion_rate"],
        semantic_id="sales.running",
        name="running",
        composition=CumulativeComposition(
            REVENUE.path, DAY.path, ("grain_to_date", reporting_grain)
        ),
    )
    registry.metrics["sales.history"] = replace(
        registry.metrics["sales.running"],
        semantic_id="sales.history",
        name="history",
        composition=CumulativeComposition(REVENUE.path, DAY.path),
    )
    return _rebind(original, registry, (snapshot,)), reporting_grain


def _rows(fixture: ExecutionFixture, dataset: LogicalMetricDataset) -> list[dict[str, object]]:
    compiled = compile_dataset(dataset, fixture.tables(dataset))
    for validation in compiled.validations:
        assert validation.expression.to_pyarrow()["violations"][0].as_py() == 0, validation.name
    records = compiled.expression.select(*compiled.primary_columns).to_pyarrow().to_pylist()
    return [dict(row) for row in records]


@pytest.mark.parametrize(
    "agg,expected",
    [
        ("sum", 147),
        ("count", 5),
        ("mean", 29.4),
        ("min", 0),
        ("max", 100),
        ("count_distinct", 5),
        ("median", 10),
        (("percentile", 0.9), 72),
    ],
)
def test_source_aggregate_families_match_independent_values(
    tmp_path: Path, agg: AggKind, expected: float
) -> None:
    with execution_fixture(tmp_path) as original:
        registry = replace(original.registry, metrics=dict(original.registry.metrics))
        registry.metrics[REVENUE.path] = replace(registry.metrics[REVENUE.path], aggregation=agg)
        fixture = _rebind(original, registry)
        source = fixture.sources.observe(
            REVENUE, population=fixture.sources.population(ref.entity("sales.customers"))
        )
        assert _rows(fixture, source.aggregate())[0]["revenue"] == pytest.approx(expected)


def test_source_device_peak_recomputes_spatial_sum_before_temporal_max(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as original:
        original.backend.raw_sql("DELETE FROM orders")
        original.backend.raw_sql(
            "INSERT INTO orders (id, customer_id, amount, day) VALUES (1,1,10,DATE '2026-02-01'), (2,1,0,DATE '2026-02-02'), (3,2,0,DATE '2026-02-01'), (4,2,10,DATE '2026-02-02')"
        )
        registry = replace(original.registry, measures=dict(original.registry.measures))
        registry.measures["sales.orders.amount"] = replace(
            registry.measures["sales.orders.amount"],
            additivity=SemiAdditive(DAY.path, TimeFoldIR("max")),
        )
        fixture = _rebind(original, registry)
        source = fixture.sources.observe(
            REVENUE,
            population=fixture.sources.population(ref.entity("sales.customers")),
            time_scope=WINDOW,
        )
        assert [row["revenue"] for row in _rows(fixture, source)][:2] == [10, 10]
        assert _rows(fixture, source.aggregate())[0]["revenue"] == 10
        assert metric_definition(source).metrics[0].required_state == ()


@pytest.mark.parametrize(
    "fold,expected",
    [
        (TimeFoldIR("mean"), 140 / 3),
        (TimeFoldIR("min"), 0),
        (TimeFoldIR("max"), 110),
        (TimeFoldIR("first"), 110),
        (TimeFoldIR("last"), 0),
        (TimeFoldIR("percentile", 0.9), 94),
    ],
)
def test_source_time_folds_apply_after_spatial_aggregation(
    tmp_path: Path, fold: TimeFoldIR, expected: float
) -> None:
    with execution_fixture(tmp_path) as original:
        registry = replace(original.registry, measures=dict(original.registry.measures))
        registry.measures["sales.orders.amount"] = replace(
            registry.measures["sales.orders.amount"], additivity=SemiAdditive(DAY.path, fold)
        )
        fixture = _rebind(original, registry)
        source = fixture.sources.observe(REVENUE, time_scope=WINDOW).aggregate()
        assert _rows(fixture, source)[0]["revenue"] == pytest.approx(expected)


def test_source_weighted_mean_temporal_fold_uses_spatial_pair_state(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as original:
        original.backend.raw_sql("DELETE FROM orders")
        original.backend.raw_sql(
            "INSERT INTO orders (id, customer_id, amount, weight, day) VALUES (1,1,10,1,DATE '2026-02-01'), (2,2,30,3,DATE '2026-02-01'), (3,1,100,1,DATE '2026-02-02'), (4,2,0,1,DATE '2026-02-02')"
        )
        registry = replace(original.registry, measures=dict(original.registry.measures))
        registry.measures["sales.orders.amount"] = replace(
            registry.measures["sales.orders.amount"],
            additivity=SemiAdditive(DAY.path, TimeFoldIR("mean")),
        )
        fixture = _rebind(original, registry)
        source = fixture.sources.observe(ref.metric("sales.weighted_amount"), time_scope=WINDOW)
        daily = source.with_time_axis(DAY, grain=grain("day")).aggregate()
        assert [row["weighted_amount"] for row in _rows(fixture, daily)] == [25, 50]
        assert _rows(fixture, source.aggregate())[0]["weighted_amount"] == 37.5


def test_source_linear_composes_independently_aggregated_components(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as original:
        registry = replace(original.registry, metrics=dict(original.registry.metrics))
        registry.metrics["sales.weight_total"] = replace(
            registry.metrics[REVENUE.path],
            semantic_id="sales.weight_total",
            name="weight_total",
            measure="sales.orders.weight",
            aggregation_target="sales.orders.weight",
        )
        registry.metrics["sales.net"] = replace(
            registry.metrics["sales.conversion_rate"],
            semantic_id="sales.net",
            name="net",
            composition=LinearComposition(
                (LinearTerm("+", REVENUE.path), LinearTerm("-", "sales.weight_total"))
            ),
        )
        fixture = _rebind(original, registry)
        source = fixture.sources.observe(ref.metric("sales.net")).aggregate()
        assert _rows(fixture, source)[0]["net"] == 137


def test_versioned_source_time_axis_uses_observation_versions_after_membership_selection(
    tmp_path: Path,
) -> None:
    with execution_fixture(tmp_path) as original:
        original.backend.raw_sql(
            "UPDATE snapshots SET amount = CASE WHEN day = DATE '2026-02-27' THEN 3 ELSE 7 END"
        )
        registry = replace(
            original.registry,
            metrics=dict(original.registry.metrics),
            measures=dict(original.registry.measures),
        )
        axis = ref.time_dimension("sales.snapshots.snapshot_at")
        registry.measures["sales.snapshots.amount"] = replace(
            registry.measures["sales.snapshots.amount"],
            additivity=SemiAdditive(axis.path, TimeFoldIR("last")),
        )
        registry.metrics["sales.snapshot_value"] = replace(
            registry.metrics[REVENUE.path],
            semantic_id="sales.snapshot_value",
            name="snapshot_value",
            entities=("sales.snapshots",),
            measure="sales.snapshots.amount",
            aggregation_target="sales.snapshots.amount",
        )
        fixture = _rebind(original, registry)
        scope = time_scope(start="2026-02-27", end="2026-03-01")
        population = fixture.sources.population(ref.entity("sales.snapshots"), time_scope=scope)
        observed = fixture.sources.observe(
            ref.metric("sales.snapshot_value"), population=population, time_scope=scope
        )
        timed = observed.with_time_axis(axis, grain=grain("day"))
        rows = _rows(fixture, timed.aggregate())
        assert [row["snapshot_value"] for row in rows] == [3, 14]
        assert metric_definition(timed).coordinate_paths[0].partition == "disjoint"


@pytest.mark.parametrize(
    "anchor,expected",
    [
        ("all_history", [115, 145, 145]),
        (("grain_to_date", "month"), [110, 140, 140]),
        (("trailing", 2, "day"), [110, 140, 30]),
    ],
)
def test_source_cumulative_history_reset_and_trailing_windows(
    tmp_path: Path, anchor: CumulativeAnchor, expected: list[int]
) -> None:
    with execution_fixture(tmp_path) as original:
        original.backend.raw_sql(
            "INSERT INTO orders (id, customer_id, amount, day) VALUES (7,1,5,DATE '2026-01-31')"
        )
        registry = replace(original.registry, metrics=dict(original.registry.metrics))
        registry.metrics["sales.running"] = replace(
            registry.metrics["sales.conversion_rate"],
            semantic_id="sales.running",
            name="running",
            composition=CumulativeComposition(REVENUE.path, DAY.path, anchor),
        )
        fixture = _rebind(original, registry)
        source = (
            fixture.sources.observe(ref.metric("sales.running"), time_scope=WINDOW)
            .with_time_axis(DAY, grain=grain("day"))
            .aggregate()
        )
        assert [row["running"] for row in _rows(fixture, source)] == expected
        assert metric_definition(source).metrics[0].required_state == ()


def test_customer_day_selection_keeps_exact_selected_contributions(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        source = fixture.sources.observe(
            [REVENUE, ref.metric("sales.conversion_rate")],
            population=fixture.sources.population(ref.entity("sales.customers")),
            time_scope=WINDOW,
        )
        selected = source.with_time_axis(DAY, grain=grain("day")).where(gt(REVENUE, 50)).aggregate()
        rows = _rows(fixture, selected)
        assert len(rows) == 1
        assert rows[0]["revenue"] == 100
        assert rows[0]["conversion_rate"] == 100


def test_source_semantic_grain_uses_exact_preloaded_certified_buckets(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as original:
        fixture, reporting_grain = _with_fiscal_calendar(original)
        source = (
            fixture.sources.observe(REVENUE, time_scope=WINDOW)
            .with_time_axis(DAY, grain=reporting_grain)
            .aggregate()
        )
        assert _rows(fixture, source) == [
            {"order_time": date(2026, 2, 1), "revenue": 110},
            {"order_time": date(2026, 2, 3), "revenue": 30},
        ]
        assert metric_definition(source).temporal_snapshot is not None
        cumulative = fixture.sources.observe(ref.metric("sales.running"), time_scope=WINDOW)
        assert _rows(fixture, cumulative.aggregate())[0]["running"] == 30
        by_day = cumulative.with_time_axis(DAY, grain=grain("day")).aggregate()
        assert [row["running"] for row in _rows(fixture, by_day)] == [110, 30, 30]
        by_period = cumulative.with_time_axis(DAY, grain=reporting_grain).aggregate()
        assert [row["running"] for row in _rows(fixture, by_period)] == [110, 30]


@pytest.mark.parametrize("metric_name", ["revenue", "running"])
def test_semantic_calendar_validations_publish_each_required_occurrence(
    tmp_path: Path, metric_name: str
) -> None:
    with execution_fixture(tmp_path) as original:
        fixture, reporting_grain = _with_fiscal_calendar(original)
    runtime = DatasetRuntime.create(tmp_path, "calendar-publication")
    sources = make_lazy_sources(
        semantic_registry=fixture.registry,
        sidecar=fixture.sidecar,
        action_port=runtime,
        session_id=runtime.session_ref,
        store_id=runtime.store.store_id,
        period_calendar_snapshots=fixture.sources._owner.period_calendar_snapshots,
    )
    logical = (
        sources.observe(ref.metric(f"sales.{metric_name}"), time_scope=WINDOW)
        .with_time_axis(DAY, grain=reporting_grain)
        .aggregate()
    )
    materialized = logical.execute()
    assert materialized.to_pandas()[metric_name].tolist() == [110, 30]
    record = runtime.store.artifact(materialized.state.artifact_ref.ref)
    assert record is not None
    validations = record.descriptor.population_authority.validation_results
    names = tuple(name for name, _ in validations)
    assert len(names) == len(set(names))
    assert sum(name.startswith("calendar.coordinate_coverage") for name in names) >= 2
    assert all(violations == 0 for _, violations in validations)
    if metric_name == "running":
        from marivo.analysis.materialization.reads import part_schema

        assert len(record.descriptor.retained_parts) == 1
        part = record.descriptor.retained_parts[0]
        assert part.contract_id == "metric.sufficient_components"
        part_names = part_schema(tmp_path, part).names
        assert any(name.endswith("_sum") for name in part_names)
        assert any(name.endswith("_evaluation_end") for name in part_names)
        assert any(name.endswith("_coverage_complete") for name in part_names)


@pytest.mark.parametrize("end", ["2026-02-01", "2026-02-06"])
def test_semantic_reset_rejects_endpoint_without_certified_period(tmp_path: Path, end: str) -> None:
    with execution_fixture(tmp_path) as original:
        fixture, _ = _with_fiscal_calendar(original)
        with pytest.raises(DatasetConstructionError, match="endpoint outside calendar coverage"):
            fixture.sources.observe(
                ref.metric("sales.running"),
                time_scope=time_scope(start="2026-01-01", end=end),
            )


@pytest.mark.parametrize("metric_name", ["sales.revenue", "sales.running"])
def test_semantic_calendar_rejects_display_scope_outside_coverage(
    tmp_path: Path, metric_name: str
) -> None:
    with execution_fixture(tmp_path) as original:
        fixture, reporting_grain = _with_fiscal_calendar(original)
        source = fixture.sources.observe(
            ref.metric(metric_name),
            time_scope=time_scope(start="2026-01-31", end="2026-02-05"),
        )
        for display_grain in (
            (reporting_grain, grain("day"))
            if metric_name == "sales.running"
            else (reporting_grain,)
        ):
            with pytest.raises(DatasetConstructionError, match="display scope outside"):
                source.with_time_axis(DAY, grain=display_grain)


def test_calendar_cumulative_history_is_not_limited_by_display_coverage(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as original:
        fixture, reporting_grain = _with_fiscal_calendar(original)
        original.backend.raw_sql(
            "INSERT INTO orders (id, customer_id, amount, day) VALUES (99,1,5,DATE '2026-01-31')"
        )
        history = (
            fixture.sources.observe(ref.metric("sales.history"), time_scope=WINDOW)
            .with_time_axis(DAY, grain=reporting_grain)
            .aggregate()
        )
        assert [row["history"] for row in _rows(fixture, history)] == [115, 145]
        reset = fixture.sources.observe(
            ref.metric("sales.running"),
            time_scope=time_scope(start="2026-01-01", end="2026-02-05"),
        ).aggregate()
        assert _rows(fixture, reset)[0]["running"] == 30


def test_source_selected_rate_uses_only_selected_denominator(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as original:
        original.backend.raw_sql("DELETE FROM orders")
        original.backend.raw_sql(
            "INSERT INTO orders (id, customer_id, amount, weight) VALUES (1,1,8,10), (2,2,1,10)"
        )
        registry = replace(original.registry, metrics=dict(original.registry.metrics))
        registry.metrics["sales.denominator"] = replace(
            registry.metrics[REVENUE.path],
            semantic_id="sales.denominator",
            name="denominator",
            measure="sales.orders.weight",
            aggregation_target="sales.orders.weight",
        )
        registry.metrics["sales.conversion_rate"] = replace(
            registry.metrics["sales.conversion_rate"],
            composition=RatioComposition(REVENUE.path, "sales.denominator"),
        )
        fixture = _rebind(original, registry)
        source = fixture.sources.observe(
            [REVENUE, ref.metric("sales.conversion_rate")],
            population=fixture.sources.population(ref.entity("sales.customers")),
        )
        selected = source.where(gt(REVENUE, 5)).aggregate()
        assert _rows(fixture, selected)[0]["conversion_rate"] == 0.8


def test_cumulative_weighted_base_accumulates_named_components(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as original:
        registry = replace(original.registry, metrics=dict(original.registry.metrics))
        registry.metrics["sales.running"] = replace(
            registry.metrics["sales.conversion_rate"],
            semantic_id="sales.running",
            name="running",
            composition=CumulativeComposition("sales.weighted_amount", DAY.path),
        )
        fixture = _rebind(original, registry)
        source = (
            fixture.sources.observe(ref.metric("sales.running"), time_scope=WINDOW)
            .with_time_axis(DAY, grain=grain("day"))
            .aggregate()
        )
        assert [row["running"] for row in _rows(fixture, source)] == [70, 50, 50]


def test_cumulative_distinct_base_counts_identity_once_across_buckets(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as original:
        original.backend.raw_sql("DELETE FROM orders")
        original.backend.raw_sql(
            "INSERT INTO orders (id, customer_id, amount, day) VALUES (1,1,10,DATE '2026-02-01'), (2,1,10,DATE '2026-02-02'), (3,2,20,DATE '2026-02-02')"
        )
        registry = replace(original.registry, metrics=dict(original.registry.metrics))
        registry.metrics[REVENUE.path] = replace(
            registry.metrics[REVENUE.path], aggregation="count_distinct"
        )
        registry.metrics["sales.running"] = replace(
            registry.metrics["sales.conversion_rate"],
            semantic_id="sales.running",
            name="running",
            composition=CumulativeComposition(REVENUE.path, DAY.path),
        )
        fixture = _rebind(original, registry)
        source = (
            fixture.sources.observe(ref.metric("sales.running"), time_scope=WINDOW)
            .with_time_axis(DAY, grain=grain("day"))
            .aggregate()
        )
        assert [row["running"] for row in _rows(fixture, source)] == [1, 2]


@pytest.mark.parametrize("filtered", [False, True])
def test_overlapping_tags_preserve_per_tag_values_and_root_total(
    tmp_path: Path, filtered: bool
) -> None:
    with execution_fixture(tmp_path) as original:
        original.backend.raw_sql("DELETE FROM orders")
        original.backend.raw_sql("DELETE FROM lines")
        original.backend.raw_sql("INSERT INTO orders (id, customer_id, amount) VALUES (1,1,100)")
        original.backend.raw_sql(
            "INSERT INTO lines (id, order_id, channel) VALUES (1,1,'a'), (2,1,'b')"
        )
        registry = replace(
            original.registry,
            metrics=dict(original.registry.metrics),
            dimensions=dict(original.registry.dimensions),
        )
        registry.metrics[REVENUE.path] = replace(
            registry.metrics[REVENUE.path],
            fanout_policy="aggregate_then_join",
            filter=(("sales.lines.tag", ("a", "b")),) if filtered else None,
        )
        registry.dimensions["sales.lines.tag"] = replace(
            registry.dimensions["sales.orders.channel"],
            semantic_id="sales.lines.tag",
            entity="sales.lines",
            name="tag",
        )
        fixture = _rebind(original, registry)
        source = fixture.sources.observe(REVENUE)
        tagged = source.with_dimensions(ref.dimension("sales.lines.tag")).aggregate()
        assert _rows(fixture, tagged) == [
            {"tag": "a", "revenue": 100},
            {"tag": "b", "revenue": 100},
        ]
        assert _rows(fixture, source.aggregate())[0]["revenue"] == 100
        assert (
            metric_definition(tagged).aggregation_contracts[0].materialized_fold
            == "source_required"
        )
