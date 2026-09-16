"""PostgreSQL registration examines the entire frozen Group A dependency closure."""

from dataclasses import replace

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis import runtime_metric as rm
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators.postgres_support import supported_type
from marivo.analysis.operators.registry import backend_execution, implementation
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.ir import CsvSourceIR, TableColumnBindingIR, TableSourceIR
from marivo.refs import ref
from marivo.semantic.ir import AggKind, StrptimeParse
from marivo.semantic.validator import Registry
from tests.lazy_observation_fixtures import NoIoActionPort, make_semantic_registry


def _sources(
    *, amount_type: str = "float64", aggregation: AggKind = "sum", parsed_time: bool = False
) -> LazySources:
    original, sidecar = make_semantic_registry()
    registry = Registry(
        domains=dict(original.domains),
        datasources=dict(original.datasources),
        entities=dict(original.entities),
        dimensions=dict(original.dimensions),
        measures=dict(original.measures),
        metrics=dict(original.metrics),
        relationships=dict(original.relationships),
    )
    for name, entity in tuple(registry.entities.items()):
        if isinstance(entity.source, CsvSourceIR):
            registry.entities[name] = replace(
                entity,
                source=TableSourceIR(
                    table=entity.name,
                    columns=tuple(
                        (
                            column,
                            TableColumnBindingIR(
                                column, amount_type if column == "amount" else kind
                            ),
                        )
                        for column, kind in entity.source.schema
                    ),
                ),
            )
    registry.metrics["sales.revenue"] = replace(
        registry.metrics["sales.revenue"], aggregation=aggregation
    )
    if parsed_time:
        registry.dimensions["sales.orders.order_time"] = replace(
            registry.dimensions["sales.orders.order_time"], parse=StrptimeParse("%Y-%m-%d")
        )
    registry.freeze()
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="postgres-admission",
        store_id="postgres-admission",
    )


@pytest.mark.parametrize("metric", ["revenue", "order_count"])
def test_group_a_registers_source_without_retained_import(metric: str) -> None:
    sources = _sources()
    observed = sources.observe(
        ref.metric(f"sales.{metric}"),
        time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
    )
    for dataset in (
        observed,
        observed.aggregate(),
        observed.with_dimensions(ref.dimension("sales.orders.channel")).aggregate(),
    ):
        registration = implementation(dataset).for_backend("postgres")
        assert registration is not None and registration.source
    execution = backend_execution("postgres")
    assert execution is not None and not execution.retained_import
    assert backend_execution("mysql") is None


@pytest.mark.parametrize("metric", ["mean_amount", "conversion_rate", "weighted_amount"])
def test_composed_and_nonscalar_metrics_are_not_registered(metric: str) -> None:
    sources = _sources()
    dataset = sources.observe(ref.metric(f"sales.{metric}"))
    assert implementation(dataset).for_backend("postgres") is None
    assert implementation(dataset).for_backend("duckdb") is not None


def test_relationship_and_time_axis_dependencies_are_not_registered() -> None:
    sources = _sources()
    base = sources.observe(ref.metric("sales.revenue"))
    related = base.with_dimensions(ref.dimension("sales.customers.region"))
    temporal = base.with_time_axis(
        ref.time_dimension("sales.orders.order_time"), grain=grain("day")
    )
    for dataset in (related, temporal):
        assert implementation(dataset).for_backend("postgres") is None


def test_unsupported_declared_source_type_is_not_registered() -> None:
    dataset = _sources(amount_type="uint64").observe(ref.metric("sales.revenue"))
    assert implementation(dataset).for_backend("postgres") is None


def test_filter_projection_rank_and_limit_keep_full_closure() -> None:
    sources = _sources()
    revenue = ref.metric("sales.revenue")
    base = sources.observe([revenue, ref.metric("sales.order_count")])
    selected = base.where(gt(base.fields.metric(revenue), 0)).metric(revenue)
    ranked = selected.rank(selected.fields.metric(revenue)).limit(3)
    assert implementation(ranked).for_backend("postgres") is not None
    mixed = sources.observe([revenue, ref.metric("sales.mean_amount")]).metric(revenue)
    assert implementation(mixed).for_backend("postgres") is None


def test_versioned_population_is_not_registered() -> None:
    population = _sources().population(
        ref.entity("sales.snapshots"),
        time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
    )
    assert implementation(population).for_backend("postgres") is None


@pytest.mark.parametrize("aggregation", ["sum", "count", "min", "max"])
def test_exact_scalar_aggregates_are_registered(aggregation: AggKind) -> None:
    dataset = _sources(aggregation=aggregation).observe(ref.metric("sales.revenue")).aggregate()
    assert implementation(dataset).for_backend("postgres") is not None


@pytest.mark.parametrize("kind", ["decimal(39,2)", "decimal(18,19)", "uint64", "array<int64>"])
def test_unsupported_scalar_type_boundaries(kind: str) -> None:
    assert not supported_type(kind)


def test_decimal_128_bounds_are_supported() -> None:
    assert supported_type("decimal(18,2)")
    assert supported_type("decimal(38, 38)")


def test_metric_slice_cannot_hide_unsupported_time_parse() -> None:
    sources = _sources(parsed_time=True)
    metric = rm.slice(
        ref.metric("sales.revenue"),
        by={ref.time_dimension("sales.orders.order_time"): "2026-02-01"},
        label="selected_revenue",
    )
    dataset = sources.observe(metric).aggregate()
    assert implementation(dataset).for_backend("postgres") is None


def test_unsupported_mean_reports_postgres_placement_without_source_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path

    from marivo.analysis.compiler.errors import DatasetCompilationError
    from marivo.analysis.compiler.placement import place
    from tests.lazy_execution_fixtures import make_execution_registry

    original, sidecar = make_execution_registry(Path("unused.duckdb"))
    registry = replace(
        original,
        datasources={
            name: replace(datasource, backend_type="postgres")
            for name, datasource in original.datasources.items()
        },
    )
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="postgres-error",
        store_id="postgres-error",
    )
    attempts: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        attempts.append("source")
        raise AssertionError("Unsupported placement touched source I/O")

    monkeypatch.setattr(
        "marivo.analysis.materialization.admission._build_backend_from_effective", forbidden
    )
    dataset = sources.observe(ref.metric("sales.mean_amount"))
    with pytest.raises(DatasetCompilationError) as raised:
        place(dataset)
    error = raised.value
    assert error.expected
    assert error.received is not None
    assert "backend=postgres" in error.received
    assert "session.observe" in error.received
    assert "shape=metric/entity@v1" in error.received
    assert error.repair is not None and "duckdb" in error.repair.action
    assert attempts == []
