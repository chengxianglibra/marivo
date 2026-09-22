"""Independent no-I/O checks for the relational scalar admission boundary."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis import runtime_metric as rm
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.placement import place, source_binding
from marivo.analysis.operators.mysql_support import unsupported_reason as mysql_reason
from marivo.analysis.operators.scalar_support import supports_scalar_type, unsupported_reason
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_observation_fixtures import NoIoActionPort


def _sources() -> LazySources:
    registry, sidecar = make_execution_registry(Path("must-not-open.duckdb"))
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="group-b-admission",
        store_id="group-b-admission",
    )


@pytest.mark.parametrize("metric", ["revenue", "mean_amount", "weighted_amount", "conversion_rate"])
def test_composed_scalar_closure(metric: str) -> None:
    observed = _sources().observe(ref.metric("sales." + metric))
    assert unsupported_reason(observed, supports_scalar_type) is None
    assert unsupported_reason(observed.aggregate(), supports_scalar_type) is None


def test_mysql_expanded_attribution_reports_resource_rejection_without_source_io() -> None:
    metric = _sources().observe(ref.metric("sales.revenue")).aggregate()
    expanded = metric.compare(metric).attribute(axes=(ref.dimension("sales.orders.channel"),))
    assert (
        mysql_reason(expanded) == "expanded Attribution exceeds the qualified MySQL resource limit"
    )


def test_relationships_are_independently_qualified() -> None:
    observed = _sources().observe(ref.metric("sales.revenue"))
    related = observed.with_dimensions(ref.dimension("sales.customers.region")).aggregate()
    assert "relation" in (unsupported_reason(related, supports_scalar_type) or "")
    assert unsupported_reason(related, supports_scalar_type, relationships=True) is None


@pytest.mark.parametrize("unit", ["day", "week", "month", "quarter", "year"])
def test_native_civil_buckets_require_qualification(
    unit: Literal["day", "week", "month", "quarter", "year"],
) -> None:
    observed = (
        _sources()
        .observe(ref.metric("sales.revenue"))
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain(unit))
        .aggregate()
    )
    assert "temporal" in (unsupported_reason(observed, supports_scalar_type) or "")
    assert unsupported_reason(observed, supports_scalar_type, date_buckets=True) is None


def test_versions_are_independently_qualified() -> None:
    population = _sources().population(
        ref.entity("sales.snapshots"), time_scope=time_scope(start="2026-02-01", end="2026-03-01")
    )
    assert "version" in (unsupported_reason(population, supports_scalar_type) or "")


def test_projected_graph_still_checks_all_source_types() -> None:
    observed = (
        _sources()
        .observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")])
        .metric(ref.metric("sales.revenue"))
    )
    assert unsupported_reason(observed, lambda kind: kind != "float64") is not None


def test_linear_graph_remains_unqualified_even_after_projection() -> None:
    revenue = ref.metric("sales.revenue")
    linear = rm.linear(add=(revenue,), subtract=(ref.metric("sales.order_count"),), label="net")
    observed = _sources().observe((revenue, linear)).metric(revenue)
    assert "unqualified computation" in (unsupported_reason(observed, supports_scalar_type) or "")


def test_timestamp_reference_cannot_bypass_native_date_admission() -> None:
    registry, sidecar = make_execution_registry(Path("must-not-open.duckdb"))
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    registry = replace(
        registry,
        entities={
            **registry.entities,
            "sales.orders": replace(
                entity,
                source=replace(
                    entity.source,
                    columns=tuple(
                        (
                            name,
                            replace(binding, data_type="timestamp") if name == "day" else binding,
                        )
                        for name, binding in entity.source.columns
                    ),
                ),
            ),
        },
        dimensions={
            **registry.dimensions,
            "sales.orders.order_time": replace(
                registry.dimensions["sales.orders.order_time"], parse=None
            ),
        },
    )
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="timestamp-rejection",
        store_id="timestamp-rejection",
    )
    observed = sources.observe(
        ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-03-01")
    )
    assert unsupported_reason(observed, lambda kind: True, date_buckets=True) is not None


@pytest.mark.parametrize("backend", ["postgres", "mysql", "sqlite", "trino", "clickhouse"])
def test_relationship_stage_requires_one_exact_datasource_binding(backend: str) -> None:
    registry, sidecar = make_execution_registry(Path("must-not-open.duckdb"))
    customer = registry.entities["sales.customers"]
    original = registry.datasources[customer.datasource]
    other_id = original.semantic_id + "_other"
    registry = replace(
        registry,
        entities={**registry.entities, "sales.customers": replace(customer, datasource=other_id)},
        datasources={
            **{
                key: replace(value, backend_type=backend)
                for key, value in registry.datasources.items()
            },
            other_id: replace(
                original, semantic_id=other_id, name=original.name + "_other", backend_type=backend
            ),
        },
    )
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="mixed-source",
        store_id="mixed-source",
    )
    related = (
        sources.observe(ref.metric("sales.revenue"))
        .with_dimensions(ref.dimension("sales.customers.region"))
        .aggregate()
    )
    # Identical engine and connection fields do not merge distinct declaration authority.
    with pytest.raises(DatasetCompilationError, match="mixed sources"):
        source_binding(related)
    with pytest.raises(DatasetCompilationError, match="mixed sources"):
        place(related)
