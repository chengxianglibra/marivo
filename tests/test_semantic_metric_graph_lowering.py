"""Catalog lowering tests for the shared recursive metric graph."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest

from marivo.semantic.ir import LinearComposition, LinearTerm, RatioComposition
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CumulativeNodeV1,
    LinearNodeV1,
    RatioNodeV1,
    WeightedMeanAggregateNodeV1,
)
from marivo.semantic.metric_graph_canonical import (
    MetricGraphContractError,
    canonical_bytes,
    fingerprint,
    metric_graph_from_bytes,
)
from marivo.semantic.metric_graph_lowering import (
    MetricGraphLoweringError,
    lower_catalog_metric,
    lower_catalog_metrics,
)
from marivo.semantic.validator import Registry

_CATALOG_SOURCE = """\
import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("wh")
orders = ms.entity(name="orders", datasource=wh, source=md.table("orders"))
amount = ms.measure_column(
    name="amount", entity=orders, column="amount", additivity="additive", unit="CNY"
)
unit_price = ms.measure_column(
    name="unit_price", entity=orders, column="amount", additivity="non_additive", unit="CNY"
)
event_time = ms.time_dimension_column(
    name="event_time", entity=orders, column="event_time", granularity="day"
)
state = ms.dimension_column(name="state", entity=orders, column="state")
revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
revenue_alias = ms.aggregate(name="revenue_alias", measure=amount, agg="sum")
failed_revenue = ms.aggregate(
    name="failed_revenue", measure=amount, agg="sum", filter=ms.where(state="FAILED")
)
order_count = ms.count(name="order_count", entity=orders)
inner = ms.ratio(name="inner", numerator=revenue, denominator=order_count)
outer = ms.ratio(name="outer", numerator=inner, denominator=revenue)
share = ms.ratio(
    name="share", numerator=revenue, denominator=revenue, unit="%"
)
weighted = ms.weighted_mean(name="weighted", value=unit_price, weight=amount)
net = ms.linear(name="net", add=[revenue, revenue_alias])
mtd_revenue = ms.cumulative(
    name="mtd_revenue",
    base=revenue,
    over=event_time,
    anchor=ms.grain_to_date(grain="month"),
)
"""


@pytest.fixture
def catalog_registry() -> Iterator[Registry]:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_CATALOG_SOURCE) as result:
        assert result.registry is not None
        yield result.registry


def _root_node(lowered):
    root_id = lowered.graph.roots[0]
    return next(record.node for record in lowered.graph.nodes if record.node_id == root_id)


def test_equivalent_catalog_aggregates_share_value_graph_not_authority_digest(
    catalog_registry: Registry,
) -> None:
    revenue = lower_catalog_metric(catalog_registry, "test.revenue")
    alias = lower_catalog_metric(catalog_registry, "test.revenue_alias")

    assert fingerprint(revenue.graph) == fingerprint(alias.graph)
    assert revenue.graph.roots == alias.graph.roots
    assert revenue.dependency_digest.digest != alias.dependency_digest.digest
    assert revenue.identities[0].metric_ref.path == "test.revenue"
    root = _root_node(revenue)
    assert isinstance(root, AggregateNodeV1)
    assert root.unit_override is None


def test_catalog_aggregate_filter_and_explicit_unit_override_are_value_inputs(
    catalog_registry: Registry,
) -> None:
    failed = _root_node(lower_catalog_metric(catalog_registry, "test.failed_revenue"))
    share = _root_node(lower_catalog_metric(catalog_registry, "test.share"))

    assert isinstance(failed, AggregateNodeV1)
    assert len(failed.filter) == 1
    assert failed.filter[0].dimension_ref.path == "test.orders.state"
    assert failed.filter[0].value == "FAILED"
    assert isinstance(share, RatioNodeV1)
    assert share.unit_override == "%"


def test_nested_catalog_ratio_lowers_recursively_without_wrapper_leaf(
    catalog_registry: Registry,
) -> None:
    lowered = lower_catalog_metric(catalog_registry, "test.outer")
    root = _root_node(lowered)

    assert isinstance(root, RatioNodeV1)
    numerator = next(
        record.node for record in lowered.graph.nodes if record.node_id == root.numerator_id
    )
    assert isinstance(numerator, RatioNodeV1)
    assert tuple(occurrence.path for occurrence in lowered.graph.occurrences[:3]) == (
        "root[0]",
        "root[0].numerator",
        "root[0].numerator.numerator",
    )


@pytest.mark.parametrize(
    ("metric_id", "node_type"),
    [
        ("test.weighted", WeightedMeanAggregateNodeV1),
        ("test.net", LinearNodeV1),
        ("test.mtd_revenue", CumulativeNodeV1),
    ],
)
def test_catalog_lowerer_covers_registered_internal_node_kinds(
    catalog_registry: Registry,
    metric_id: str,
    node_type: type,
) -> None:
    lowered = lower_catalog_metric(catalog_registry, metric_id)

    assert isinstance(_root_node(lowered), node_type)
    assert metric_graph_from_bytes(canonical_bytes(lowered.graph)) == lowered.graph


def test_cumulative_anchor_and_axis_dependency_are_canonical(
    catalog_registry: Registry,
) -> None:
    root = _root_node(lower_catalog_metric(catalog_registry, "test.mtd_revenue"))

    assert isinstance(root, CumulativeNodeV1)
    assert root.time_dimension_ref is not None
    assert root.time_dimension_ref.path == "test.orders.event_time"
    assert root.anchor == ("grain_to_date", "month")
    assert len(root.dependency_fingerprint) == 64


def test_dependency_digest_excludes_name_context_and_source_location(
    catalog_registry: Registry,
) -> None:
    original = lower_catalog_metric(catalog_registry, "test.outer")
    metric = catalog_registry.metrics["test.outer"]
    changed_metric = dataclasses.replace(
        metric,
        name="presentation_only",
        ai_context=dataclasses.replace(
            metric.ai_context,
            business_definition="Presentation-only text",
        ),
        location=dataclasses.replace(metric.location, file="elsewhere.py", line=999),
    )
    changed_registry = dataclasses.replace(
        catalog_registry,
        metrics={**catalog_registry.metrics, "test.outer": changed_metric},
    )
    changed = lower_catalog_metric(changed_registry, "test.outer")

    assert original.dependency_digest == changed.dependency_digest
    assert original.graph == changed.graph


def test_measure_definition_digest_changes_aggregate_graph_identity(
    catalog_registry: Registry,
) -> None:
    original = lower_catalog_metric(catalog_registry, "test.revenue")
    measure = catalog_registry.measures["test.orders.amount"]
    changed_registry = dataclasses.replace(
        catalog_registry,
        measures={
            **catalog_registry.measures,
            "test.orders.amount": dataclasses.replace(
                measure,
                body_ast_hash="reauthored-measure",
            ),
        },
    )
    changed = lower_catalog_metric(changed_registry, "test.revenue")

    assert original.dependency_digest.digest != changed.dependency_digest.digest
    assert original.graph.roots != changed.graph.roots


def test_catalog_metric_cycle_reports_responsible_occurrence_path(
    catalog_registry: Registry,
) -> None:
    metric = catalog_registry.metrics["test.outer"]
    cyclic = dataclasses.replace(
        metric,
        composition=RatioComposition(
            numerator="test.outer",
            denominator="test.revenue",
        ),
    )
    registry = dataclasses.replace(
        catalog_registry,
        metrics={**catalog_registry.metrics, "test.outer": cyclic},
    )

    with pytest.raises(MetricGraphLoweringError, match=r"root\[0\]\.numerator") as exc_info:
        lower_catalog_metric(registry, "test.outer")
    assert exc_info.value.kind == "metric_graph_cycle"


def test_catalog_lowering_enforces_depth_10_and_rejects_11(
    catalog_registry: Registry,
) -> None:
    metrics = dict(catalog_registry.metrics)
    template = metrics["test.outer"]
    previous = "test.revenue"
    for depth in range(1, 11):
        metric_id = f"test.depth_{depth}"
        metrics[metric_id] = dataclasses.replace(
            template,
            semantic_id=metric_id,
            name=f"depth_{depth}",
            composition=RatioComposition(
                numerator=previous,
                denominator="test.revenue",
            ),
            body_ast_hash=f"depth-{depth}",
        )
        previous = metric_id
    registry = dataclasses.replace(catalog_registry, metrics=metrics)

    lower_catalog_metric(registry, "test.depth_9")
    with pytest.raises(MetricGraphContractError, match="depth limit exceeded"):
        lower_catalog_metric(registry, "test.depth_10")


def test_catalog_lowering_counts_pre_cse_occurrences(
    catalog_registry: Registry,
) -> None:
    template = catalog_registry.metrics["test.net"]
    too_wide = dataclasses.replace(
        template,
        semantic_id="test.too_wide",
        name="too_wide",
        composition=LinearComposition(
            terms=tuple(LinearTerm("+", "test.revenue") for _ in range(256))
        ),
        body_ast_hash="too-wide",
    )
    registry = dataclasses.replace(
        catalog_registry,
        metrics={**catalog_registry.metrics, "test.too_wide": too_wide},
    )

    with pytest.raises(MetricGraphContractError, match="occurrence limit exceeded"):
        lower_catalog_metric(registry, "test.too_wide")


def test_ordered_forest_keeps_root_order_and_shares_nodes(catalog_registry: Registry) -> None:
    lowered = lower_catalog_metrics(
        catalog_registry,
        ("test.revenue_alias", "test.revenue"),
    )

    assert tuple(identity.metric_ref.path for identity in lowered.identities) == (
        "test.revenue_alias",
        "test.revenue",
    )
    assert lowered.graph.roots[0] == lowered.graph.roots[1]
    assert len(lowered.graph.nodes) == 1
