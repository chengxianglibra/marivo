"""Independent Slice 3a source coordinate and aggregation admission cases."""

from dataclasses import replace
from datetime import date

import pytest

from marivo._temporal import (
    PeriodCalendarSnapshotV1,
    builtin_grain,
    certify_period_calendar,
    semantic_grain,
)
from marivo.analysis import time_scope
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.observation.contracts import metric_definition, semantic_dependency_digest
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.ir import CsvSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import (
    AggKind,
    CumulativeAnchor,
    CumulativeComposition,
    LinearComposition,
    LinearTerm,
    PeriodCalendarIR,
    RatioComposition,
    SampleIntervalIR,
    SemiAdditive,
    TimeFoldIR,
    TimestampParse,
)
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from marivo.semantic.validator import Registry
from tests.lazy_observation_fixtures import NoIoActionPort, make_semantic_registry

REVENUE = ref.metric("sales.revenue")
DAY = ref.time_dimension("sales.orders.order_time")
WINDOW = time_scope(start="2026-02-01", end="2026-03-01")


def _registry() -> tuple[Registry, CompiledExpressionSidecar]:
    source, sidecar = make_semantic_registry()
    return Registry(
        domains=dict(source.domains),
        datasources=dict(source.datasources),
        entities=dict(source.entities),
        dimensions=dict(source.dimensions),
        measures=dict(source.measures),
        metrics=dict(source.metrics),
        relationships=dict(source.relationships),
    ), sidecar


def _sources(
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
    snapshots: tuple[PeriodCalendarSnapshotV1, ...] = (),
) -> LazySources:
    registry.freeze()
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="source-algebra",
        store_id="source-algebra",
        period_calendar_snapshots=snapshots,
    )


@pytest.mark.parametrize(
    "agg,state,source_required",
    [
        ("sum", "sum", False),
        ("count", "count", False),
        ("mean", "sum", False),
        ("min", "min", False),
        ("max", "max", False),
        ("count_distinct", "value", True),
        ("median", "value", True),
        (("percentile", 0.9), "value", True),
    ],
)
def test_every_governed_aggregate_has_exact_source_and_retained_state_contract(
    agg: AggKind, state: str, source_required: bool
) -> None:
    registry, sidecar = _registry()
    registry.metrics[REVENUE.path] = replace(registry.metrics[REVENUE.path], aggregation=agg)
    metric = normalize_target_metric(registry, REVENUE.path, sidecar=sidecar)
    assert metric.components[0].required_state[0] == state
    assert metric.requires_source_recompute is source_required
    assert bool(metric.required_state) is not source_required
    result = _sources(registry, sidecar).observe(REVENUE, time_scope=WINDOW).aggregate()
    assert result.row_set_contract.cardinality.kind == "singleton"
    admission = metric_definition(result).aggregation_contracts[0]
    assert admission.logical_recompute_mode == "exact_source"
    assert admission.materialized_fold == (
        "source_required" if source_required else "exact_components"
    )


@pytest.mark.parametrize("fold", ["mean", "min", "max", "first", "last", "percentile"])
def test_status_folds_have_source_order_and_no_projected_retained_fold(fold: str) -> None:
    registry, sidecar = _registry()
    folds = {
        "mean": TimeFoldIR("mean"),
        "min": TimeFoldIR("min"),
        "max": TimeFoldIR("max"),
        "first": TimeFoldIR("first"),
        "last": TimeFoldIR("last"),
        "percentile": TimeFoldIR("percentile", 0.9),
    }
    registry.measures["sales.orders.amount"] = replace(
        registry.measures["sales.orders.amount"],
        additivity=SemiAdditive(DAY.path, folds[fold]),
    )
    dataset = _sources(registry, sidecar).observe(REVENUE, time_scope=WINDOW)
    result = dataset.with_time_axis(DAY, grain=builtin_grain("month")).aggregate()
    definition = metric_definition(result)
    assert definition.grain == builtin_grain("month")
    assert definition.metrics[0].components[0].required_state[0] == "value"
    assert definition.metrics[0].required_state == ()
    contract = definition.aggregation_contracts[0]
    assert contract.ordered_aggregation_and_temporal_fold == ("space", "time", "compose")
    assert contract.required_time_axes == (DAY.path,)
    with pytest.raises(DatasetConstructionError, match="precision"):
        dataset.with_time_axis(DAY, grain=builtin_grain("hour"))


def _cumulative(registry: Registry, anchor: CumulativeAnchor) -> None:
    registry.metrics["sales.running"] = replace(
        registry.metrics["sales.conversion_rate"],
        semantic_id="sales.running",
        name="running",
        composition=CumulativeComposition(REVENUE.path, DAY.path, anchor),
    )


@pytest.mark.parametrize(
    "anchor", ["all_history", ("grain_to_date", "month"), ("trailing", 7, "day")]
)
def test_cumulative_scalar_endpoint_and_time_axis_contract(anchor: CumulativeAnchor) -> None:
    registry, sidecar = _registry()
    _cumulative(registry, anchor)
    sources = _sources(registry, sidecar)
    with pytest.raises(DatasetConstructionError, match="endpoint"):
        sources.observe(ref.metric("sales.running"))
    result = sources.observe(ref.metric("sales.running"), time_scope=WINDOW)
    definition = metric_definition(result.aggregate())
    assert definition.metrics[0].required_state == ()
    assert definition.metrics[0].cumulative[0].over_ref.path == DAY.path
    assert definition.metrics[0].cumulative[0].anchor == anchor
    assert result.with_time_axis(DAY, grain=builtin_grain("day")).aggregate().kind == "metric"


def test_cumulative_reset_and_trailing_do_not_accept_ambiguous_grains() -> None:
    registry, sidecar = _registry()
    _cumulative(registry, ("grain_to_date", "month"))
    source = _sources(registry, sidecar).observe(ref.metric("sales.running"), time_scope=WINDOW)
    with pytest.raises(DatasetConstructionError, match="reset period"):
        source.with_time_axis(DAY, grain=builtin_grain("week"))
    registry, sidecar = _registry()
    _cumulative(registry, ("trailing", 7, "day"))
    source = _sources(registry, sidecar).observe(ref.metric("sales.running"), time_scope=WINDOW)
    with pytest.raises(DatasetConstructionError, match="divisible"):
        source.with_time_axis(DAY, grain=builtin_grain("month"))


def test_mixed_cumulative_component_graph_fails_at_named_parent() -> None:
    registry, sidecar = _registry()
    _cumulative(registry, "all_history")
    registry.metrics["sales.conversion_rate"] = replace(
        registry.metrics["sales.conversion_rate"],
        composition=RatioComposition("sales.running", "sales.order_count"),
    )
    with pytest.raises(DatasetConstructionError, match=r"sales.conversion_rate.*mixed"):
        _sources(registry, sidecar).observe(ref.metric("sales.conversion_rate"), time_scope=WINDOW)


def test_recursive_linear_components_are_normalized_before_composition() -> None:
    registry, sidecar = _registry()
    registry.metrics["sales.linear"] = replace(
        registry.metrics["sales.conversion_rate"],
        semantic_id="sales.linear",
        name="linear",
        composition=LinearComposition(
            (LinearTerm("+", REVENUE.path), LinearTerm("-", REVENUE.path))
        ),
    )
    metric = normalize_target_metric(registry, "sales.linear", sidecar=sidecar)
    assert tuple(item.role for item in metric.components) == ("value.term[0]", "value.term[1]")
    assert metric.evaluation_order == ("space", "time", "compose")


def test_customer_population_time_coordinates_use_one_governed_fanout_spine() -> None:
    registry, sidecar = _registry()
    sources = _sources(registry, sidecar)
    population = sources.population(ref.entity("sales.customers"))
    source = sources.observe(REVENUE, population=population, time_scope=WINDOW)
    timed = source.with_time_axis(DAY, grain=builtin_grain("day"))
    definition = metric_definition(timed)
    path = definition.coordinate_paths[0]
    assert path.spine_path == ("sales.order_customer",)
    assert path.component_paths == (("sales.orders", ()),)
    assert path.partition == "disjoint"
    with pytest.raises(DatasetConstructionError, match="Entity-unique"):
        sources.observe(REVENUE, population=timed)


def test_grain_changes_coordinate_identity_without_changing_population() -> None:
    registry, sidecar = _registry()
    source = _sources(registry, sidecar).observe(REVENUE, time_scope=WINDOW)
    day = source.with_time_axis(DAY, grain=builtin_grain("day"))
    month = source.with_time_axis(DAY, grain=builtin_grain("month"))
    assert day.schema.columns[1].field_id != month.schema.columns[1].field_id
    assert (
        metric_definition(day).population_definition
        == metric_definition(month).population_definition
    )
    with pytest.raises(DatasetConstructionError, match="certified"):
        source.with_time_axis(
            DAY, grain=semantic_grain(calendar=ref.period_calendar("sales.fiscal"), level="month")
        )


def test_requested_grain_respects_physical_sample_interval() -> None:
    registry, sidecar = _registry()
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, CsvSourceIR)
    registry.entities[entity.semantic_id] = replace(
        entity,
        source=replace(
            entity.source,
            schema=tuple(
                (name, "timestamp" if name == "day" else kind)
                for name, kind in entity.source.schema
            ),
        ),
    )
    registry.dimensions[DAY.path] = replace(
        registry.dimensions[DAY.path],
        granularity="minute",
        parse=TimestampParse(timezone="UTC", sample_interval=SampleIntervalIR(5, "minute")),
    )
    source = _sources(registry, sidecar).observe(REVENUE)
    with pytest.raises(DatasetConstructionError, match="sample interval"):
        source.with_time_axis(DAY, grain=builtin_grain("minute"))
    assert source.with_time_axis(DAY, grain=builtin_grain("minute", count=5)).kind == "metric"


def test_semantic_time_coordinate_identity_binds_exact_snapshot_digest() -> None:
    calendar = ref.period_calendar("sales.fiscal")
    field_ids = []
    dependencies = []
    for split in (3, 3, 4):
        registry, sidecar = _registry()
        snapshot = certify_period_calendar(
            calendar_ref=calendar,
            boundary_timezone="UTC",
            coverage=(date(2026, 2, 1), date(2026, 2, 5)),
            rows=tuple(
                {"date": date(2026, 2, day), "period": "A" if day < split else "B"}
                for day in range(1, 5)
            ),
            levels={"reporting_period": "period"},
        )
        template = registry.metrics[REVENUE.path]
        registry.period_calendars[calendar.path] = PeriodCalendarIR(
            calendar.path,
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
        dataset = (
            _sources(registry, sidecar, (snapshot,))
            .observe(REVENUE, time_scope=time_scope(start="2026-02-01", end="2026-02-05"))
            .with_time_axis(DAY, grain=semantic_grain(calendar=calendar, level="reporting_period"))
        )
        field_ids.append(dataset.schema.columns[1].field_id)
        dependencies.append(semantic_dependency_digest(dataset))
    assert field_ids[0] == field_ids[1]
    assert dependencies[0] == dependencies[1]
    assert field_ids[0] != field_ids[2]
    assert dependencies[0] != dependencies[2]
