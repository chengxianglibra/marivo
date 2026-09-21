"""Membership and distribution admission parameters with unchanged default behavior.

The static guard proves the empty-parameter shape keeps every historical
rejection verbatim while non-empty qualification sets admit exactly the
authority shapes they name and keep every other shape rejected. Aggregate-node
and fold admission boundaries stay pinned on both sides of the parameters.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.placement import place
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.observation.distinct_contracts import membership_part_authorities
from marivo.analysis.observation.distribution_contracts import distribution_part_authorities
from marivo.analysis.operators.registry import implementation, source_unsupported_reason
from marivo.analysis.operators.scalar_support import supports_scalar_type
from marivo.analysis.operators.scalar_support import unsupported_reason as scalar_reason
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic._quantile import quantile_metric
from marivo.semantic.ir import AggKind
from marivo.semantic.validator import Registry
from tests.lazy_distinct_fixtures import make_distinct_registry
from tests.lazy_distribution_fixtures import make_distribution_registry
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_observation_fixtures import NoIoActionPort

ENGINES = ("postgres", "mysql", "sqlite", "trino", "clickhouse")


def _sources(registry: Registry, sidecar: CompiledExpressionSidecar) -> LazySources:
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="state-admission",
        store_id="state-admission",
    )


def _remote_sources() -> LazySources:
    """Declare the distinct fixture on PostgreSQL with no source I/O available."""
    registry, sidecar = make_distinct_registry()
    registry = replace(
        registry,
        datasources={
            name: replace(value, backend_type="postgres")
            for name, value in registry.datasources.items()
        },
    )
    registry.freeze()
    return _sources(registry, sidecar)


def _aggregation_registry(aggregation: AggKind) -> tuple[Registry, CompiledExpressionSidecar]:
    registry, sidecar = make_execution_registry(Path("state-admission.duckdb"))
    metrics = dict(registry.metrics)
    metrics["sales.revenue"] = replace(metrics["sales.revenue"], aggregation=aggregation)
    registry = replace(registry, metrics=metrics)
    registry.freeze()
    return registry, sidecar


def _scalar_reason(dataset: LogicalDataset) -> str | None:
    return scalar_reason(dataset, supports_scalar_type)


def test_empty_set_keeps_the_membership_rejection_verbatim() -> None:
    """An empty qualification set keeps the historical membership rejection verbatim."""
    registry, sidecar = make_distinct_registry(Path("state-admission.duckdb"))
    observed = _sources(registry, sidecar).observe(ref.metric("sales.distinct_buyers"))
    assert membership_part_authorities(observed.row_contract)
    assert _scalar_reason(observed) == (
        "distinct membership state requires an unqualified source-private implementation"
    )


def test_empty_set_keeps_the_distribution_rejection_verbatim() -> None:
    """An empty qualification set keeps the historical distribution rejection verbatim."""
    registry, sidecar = make_distribution_registry(Path("state-admission.duckdb"))
    observed = _sources(registry, sidecar).observe(ref.metric("sales.revenue"))
    assert distribution_part_authorities(observed.row_contract)
    assert _scalar_reason(observed) == (
        "distribution state requires an unqualified source-private implementation"
    )


def test_qualified_measure_membership_admits_only_the_measure_shape() -> None:
    """The membership gate judges the authority's target_kind against the set."""
    registry, sidecar = make_distinct_registry(Path("state-admission.duckdb"))
    observed = _sources(registry, sidecar).observe(ref.metric("sales.distinct_buyers"))
    assert (
        scalar_reason(observed, supports_scalar_type, distinct_memberships=frozenset({"measure"}))
        is None
    )
    assert (
        scalar_reason(observed, supports_scalar_type, distinct_memberships=frozenset({"entity"}))
        == "distinct membership state requires an unqualified source-private implementation"
    )


def test_qualified_entity_membership_admits_entity_keys() -> None:
    registry, sidecar = make_distinct_registry(Path("state-admission.duckdb"))
    observed = _sources(registry, sidecar).observe(ref.metric("sales.distinct_orders"))
    assert (
        scalar_reason(observed, supports_scalar_type, distinct_memberships=frozenset({"entity"}))
        is None
    )


def test_qualified_distribution_admits_linear_interpolation_only() -> None:
    """The distribution gate judges the quantile method against the set."""
    registry, sidecar = make_distribution_registry(Path("state-admission.duckdb"))
    observed = _sources(registry, sidecar).observe(
        quantile_metric(ref.metric("sales.revenue"), method="linear_interpolation@v1")
    )
    assert (
        scalar_reason(
            observed, supports_scalar_type, distributions=frozenset({"linear_interpolation"})
        )
        is None
    )


@pytest.mark.parametrize("backend", ENGINES)
def test_empty_parameters_keep_every_state_registration_rejected(backend: str) -> None:
    """The five remote backends receive empty sets, so state metrics stay rejected."""
    registry, sidecar = make_distinct_registry()
    distinct = (
        _sources(registry, sidecar)
        .observe(ref.metric("sales.distinct_buyers"))
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
    )
    assert source_unsupported_reason(distinct, backend) is not None
    assert implementation(distinct).for_backend(backend) is None

    registry, sidecar = make_distribution_registry(Path("state-admission.duckdb"))
    distribution = (
        _sources(registry, sidecar)
        .observe(ref.metric("sales.revenue"))
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
    )
    assert source_unsupported_reason(distribution, backend) is not None
    assert implementation(distribution).for_backend(backend) is None


@pytest.mark.parametrize("backend", ENGINES)
@pytest.mark.parametrize(
    "aggregation",
    ["count_distinct", "median", ("percentile", 0.9)],
    ids=["count_distinct", "median", "percentile"],
)
def test_unqualified_aggregates_keep_their_rejections(backend: str, aggregation: AggKind) -> None:
    """Node-level qualification opens nothing until a backend owns the parameters."""
    registry, sidecar = _aggregation_registry(aggregation)
    aggregate = (
        _sources(registry, sidecar)
        .observe(ref.metric("sales.revenue"))
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
    )
    assert source_unsupported_reason(aggregate, backend) is not None
    assert implementation(aggregate).for_backend(backend) is None


def test_qualified_parameters_report_the_split_rejections() -> None:
    """Each unqualified state shape carries its own diagnostic, not a combined one."""
    registry, sidecar = make_distinct_registry(Path("state-admission.duckdb"))
    membership = _sources(registry, sidecar).observe(ref.metric("sales.distinct_orders"))
    assert (
        scalar_reason(membership, supports_scalar_type, distinct_memberships=frozenset({"measure"}))
        == "distinct membership state requires an unqualified source-private implementation"
    )
    distribution_registry, distribution_sidecar = make_distribution_registry(
        Path("state-admission.duckdb")
    )
    distribution = _sources(distribution_registry, distribution_sidecar).observe(
        ref.metric("sales.revenue")
    )
    assert scalar_reason(distribution, supports_scalar_type) == (
        "distribution state requires an unqualified source-private implementation"
    )


def test_split_rejection_names_each_unqualified_state_shape() -> None:
    """A qualified sibling shape never masks the other shape's rejection."""
    registry, sidecar = make_distinct_registry(Path("state-admission.duckdb"))
    membership = _sources(registry, sidecar).observe(ref.metric("sales.distinct_buyers"))
    assert scalar_reason(
        membership, supports_scalar_type, distributions=frozenset({"linear_interpolation"})
    ) == ("distinct membership state requires an unqualified source-private implementation")
    distribution_registry, distribution_sidecar = make_distribution_registry(
        Path("state-admission.duckdb")
    )
    distribution = _sources(distribution_registry, distribution_sidecar).observe(
        ref.metric("sales.revenue")
    )
    assert scalar_reason(
        distribution, supports_scalar_type, distinct_memberships=frozenset({"measure"})
    ) == ("distribution state requires an unqualified source-private implementation")


def test_placement_still_rejects_unqualified_state_for_remote_backends() -> None:
    """Placement keeps failing closed while every backend passes empty sets."""
    distinct = (
        _remote_sources()
        .observe(ref.metric("sales.distinct_buyers"))
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
    )
    with pytest.raises(DatasetCompilationError):
        place(distinct)
