"""Catalog lowering tests for the shared recursive metric graph."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from unittest.mock import patch

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.refs import RefPayloadV1
from marivo.semantic.errors import ErrorKind
from marivo.semantic.ir import LinearComposition, LinearTerm, RatioComposition
from marivo.semantic.metric_graph import (
    MAX_EXPRESSION_OCCURRENCES,
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
    node_fingerprint,
)
from marivo.semantic.metric_graph_lowering import (
    MetricGraphLoweringError,
    lower_catalog_metric,
    lower_catalog_metrics,
    normalize_target_metric_inputs,
)
from marivo.semantic.runtime_metric_lowering import lower_metric_inputs
from marivo.semantic.validator import Registry

_CATALOG_SOURCE = """\
import marivo.datasource as md
import marivo.semantic as ms
import marivo.analysis as mv

wh = ms.ref.datasource("wh")
orders = ms.entity(name="orders", datasource=wh, source=md.table("orders"))
amount = ms.measure_column(
    name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY"
)
unit_price = ms.measure_column(
    name="unit_price", entity=orders, column="amount", additivity=ms.non_additive(), unit="CNY"
)
event_time = ms.time_dimension_column(
    name="event_time", entity=orders, column="event_time", granularity="day"
)
sample_time = ms.time_dimension_column(
    name="sample_time",
    entity=orders,
    column="sample_time",
    granularity="minute",
    parse=ms.datetime(timezone="UTC", sample_interval=(5, "minute")),
)
state = ms.dimension_column(name="state", entity=orders, column="state")
type = ms.dimension_column(name="type", entity=orders, column="type")
sample_value = ms.measure_column(
    name="sample_value",
    entity=orders,
    column="sample_value",
    additivity=ms.additive_all(except_=(sample_time,)),
    status_time_dimension=sample_time,
    status_time_fold=("percentile", 0.95),
)
revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
revenue_alias = ms.aggregate(name="revenue_alias", measure=amount, agg="sum")
failed_revenue = ms.aggregate(
    name="failed_revenue", measure=amount, agg="sum", filter=ms.where(state="FAILED")
)
terminal_orders = ms.count(
    name="terminal_orders", entity=orders, filter=ms.where(type=(2, 4))
)
order_count = ms.count(name="order_count", entity=orders)
inner = ms.ratio(name="inner", numerator=revenue, denominator=order_count)
outer = ms.ratio(name="outer", numerator=inner, denominator=revenue)
share = ms.ratio(
    name="share", numerator=revenue, denominator=revenue, unit="%"
)
weighted = ms.weighted_mean(name="weighted", value=unit_price, weight=amount)
inherited_folded_mean = ms.aggregate(
    name="inherited_folded_mean", measure=sample_value, agg="mean"
)
explicit_folded_mean = ms.aggregate(
    name="explicit_folded_mean",
    measure=sample_value,
    agg="mean",
    fold=("percentile", 0.5),
)
net = ms.linear(name="net", add=[revenue, revenue_alias])
mtd_revenue = ms.cumulative(
    name="mtd_revenue",
    base=revenue,
    over=event_time,
    anchor=ms.grain_to_date(grain=mv.grain("month")),
)
"""


@pytest.fixture
def catalog_registry() -> Iterator[Registry]:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_CATALOG_SOURCE) as result:
        assert result.registry is not None
        yield result.registry


@pytest.mark.parametrize("metric_id", ("test.revenue", "test.share", "test.net"))
def test_repeated_catalog_roots_preserve_forest_and_authority(
    catalog_registry: Registry, metric_id: str
) -> None:
    ids = (metric_id, "test.revenue_alias", metric_id)
    expected = lower_catalog_metrics(catalog_registry, ids)
    inputs = tuple(ms.ref.metric(item) for item in ids)
    with patch(
        "marivo.semantic.runtime_metric_lowering.lower_catalog_metric",
        wraps=lower_catalog_metric,
    ) as lower:
        actual = lower_metric_inputs(catalog_registry, inputs)
        assert lower.call_count == 2
        assert lower_metric_inputs(catalog_registry, inputs) == actual
        assert lower.call_count == 4

    assert canonical_bytes(actual.graph) == canonical_bytes(expected.graph)
    assert actual.identities == expected.identities
    assert actual.dependency_digest == expected.dependency_digest
    assert actual.presentation == expected.presentation
    assert actual.root_dependency_refs == tuple((RefPayloadV1.from_ref(item),) for item in inputs)


def test_repeated_runtime_values_keep_each_label_and_occurrence(
    catalog_registry: Registry,
) -> None:
    revenue = ms.ref.metric("test.revenue")
    first = mv.runtime_metric.linear(add=(revenue, revenue), label="first")
    second = mv.runtime_metric.linear(add=(revenue, revenue), label="second")
    assert first == second
    with patch(
        "marivo.semantic.runtime_metric_lowering.lower_catalog_metric",
        wraps=lower_catalog_metric,
    ) as lower:
        actual = lower_metric_inputs(catalog_registry, (first, second, first))
        assert lower.call_count == 1

    assert actual.graph.roots == (actual.graph.roots[0],) * 3
    assert len(actual.graph.occurrences) == 9
    assert tuple((item.occurrence_path, item.label) for item in actual.presentation.labels) == (
        ("root[0]", "first"),
        ("root[1]", "second"),
        ("root[2]", "first"),
    )
    assert all(
        dependencies == (RefPayloadV1.from_ref(revenue),)
        for dependencies in actual.root_dependency_refs
    )


def test_reused_catalog_lowering_still_counts_every_occurrence(
    catalog_registry: Registry,
) -> None:
    revenue = ms.ref.metric("test.revenue")
    with patch(
        "marivo.semantic.runtime_metric_lowering.lower_catalog_metric",
        wraps=lower_catalog_metric,
    ) as lower:
        with pytest.raises(MetricGraphContractError) as error:
            lower_metric_inputs(catalog_registry, (revenue,) * (MAX_EXPRESSION_OCCURRENCES + 1))
        assert error.value.kind == "occurrence_limit_exceeded"
        assert error.value.observed_count == MAX_EXPRESSION_OCCURRENCES + 1
        assert lower.call_count == 1
        assert lower_metric_inputs(catalog_registry, (revenue,)).graph.roots
        assert lower.call_count == 2


def test_later_consumption_reinterprets_original_measure_callable() -> None:
    from marivo.semantic.errors import SemanticLoadError
    from tests.shared_fixtures import load_inline_semantic

    source = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(name="orders", datasource=ms.ref.datasource("wh"),
                   source=md.table("orders", columns={"amount": "amount"}))
state = {"calls": 0}
def transform(value):
    state["calls"] += 1
    return value if state["calls"] == 1 else value.sum()
@ms.measure(entity=orders, additivity=ms.additive_all())
def amount(orders):
    return transform(orders.amount)
revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
"""
    with load_inline_semantic(source) as loaded:
        assert loaded.registry is not None
        metric = ms.ref.metric("test.revenue")
        assert normalize_target_metric_inputs(
            loaded.registry, (metric,), sidecar=loaded.expression_sidecar
        )[0].components
        with pytest.raises(SemanticLoadError) as error:
            normalize_target_metric_inputs(
                loaded.registry, (metric,), sidecar=loaded.expression_sidecar
            )
        assert error.value.kind == "invalid_target_metric"


def test_new_load_relowers_changed_weighted_mean_dependency() -> None:
    from tests.shared_fixtures import load_inline_semantic

    metric = ms.ref.metric("test.weighted")
    forests = []
    with patch(
        "marivo.semantic.runtime_metric_lowering.lower_catalog_metric",
        wraps=lower_catalog_metric,
    ) as lower:
        for source in (
            _CATALOG_SOURCE,
            _CATALOG_SOURCE.replace(
                'name="unit_price", entity=orders, column="amount"',
                'name="unit_price", entity=orders, column="other_amount"',
            ),
        ):
            with load_inline_semantic(source) as loaded:
                assert loaded.registry is not None
                forest = lower_metric_inputs(
                    loaded.registry, (metric, metric), sidecar=loaded.expression_sidecar
                )
                expected = lower_catalog_metrics(
                    loaded.registry, (metric.path, metric.path), sidecar=loaded.expression_sidecar
                )
                assert forest.graph == expected.graph
                assert forest.dependency_digest == expected.dependency_digest
                forests.append(forest)
        assert lower.call_count == 2
    assert forests[0].dependency_digest != forests[1].dependency_digest


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


def test_intrinsic_component_state_keeps_method_and_time_order() -> None:
    from marivo.semantic.metric_graph_lowering import normalize_target_metric
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_CATALOG_SOURCE) as loaded:
        assert loaded.registry is not None
        assert loaded.expression_sidecar is not None
        registry = loaded.registry
        sidecar = loaded.expression_sidecar
        revenue = normalize_target_metric(registry, "test.revenue", sidecar=sidecar)
        component = revenue.components[0]
        assert component.numeric_method == "sum@v1"
        assert component.required_state == ("sum", "non_null_count", "row_count")
        assert (component.spatial_merge, component.time_merge) == ("sum", "sum")
        assert component.unit == "CNY"

        weighted = normalize_target_metric(registry, "test.weighted", sidecar=sidecar)
        weighted_component = weighted.components[0]
        assert weighted_component.numeric_method == "weighted_mean@v1"
        assert weighted_component.required_state == (
            "weighted_numerator",
            "weight_sum",
            "non_null_pair_count",
            "row_count",
        )
        assert weighted_component.null_rule == "non_null_pairs"

        folded = normalize_target_metric(registry, "test.inherited_folded_mean", sidecar=sidecar)
        folded_component = folded.components[0]
        assert folded_component.time_fold == ("percentile", 0.95)
        assert folded_component.time_merge == "blocked"
        assert folded_component.requires_source_recompute


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


def test_folded_mean_keeps_non_additive_spatial_semantics_and_effective_graph_fold(
    catalog_registry: Registry,
) -> None:
    inherited_metric = catalog_registry.metrics["test.inherited_folded_mean"]
    explicit_metric = catalog_registry.metrics["test.explicit_folded_mean"]
    inherited = _root_node(lower_catalog_metric(catalog_registry, "test.inherited_folded_mean"))
    explicit = _root_node(lower_catalog_metric(catalog_registry, "test.explicit_folded_mean"))

    assert inherited_metric.additivity == "non_additive"
    assert explicit_metric.additivity == "non_additive"
    assert isinstance(inherited, AggregateNodeV1)
    assert isinstance(explicit, AggregateNodeV1)
    assert inherited.fold == ("percentile", 0.95)
    assert explicit.fold == ("percentile", 0.5)
    assert inherited != explicit
    assert node_fingerprint(inherited) != node_fingerprint(explicit)
    assert node_fingerprint(inherited) != node_fingerprint(
        dataclasses.replace(inherited, fold=None)
    )


def test_fold_override_requires_a_semi_additive_measure() -> None:
    from tests.shared_fixtures import load_inline_semantic

    source = """\
import marivo.datasource as md
import marivo.semantic as ms
events = ms.entity(
    name="events", datasource=ms.ref.datasource("wh"), source=md.table("events")
)
amount = ms.measure_column(
    name="amount", entity=events, column="amount", additivity=ms.additive_all()
)
bad = ms.aggregate(
    name="bad", measure=amount, agg="mean", fold=("percentile", 0.95)
)
"""

    with load_inline_semantic(source, expect_errors=True) as result:
        error = next(
            item
            for item in result.errors
            if item.kind == ErrorKind.TIME_FOLD_REQUIRES_SEMI_ADDITIVE
        )

    assert error.constraint_id == "time_fold_requires_semi_additive"
    assert error.details["measure_additivity"] == "additive"


def test_catalog_membership_filter_uses_existing_canonical_slice_algebra(
    catalog_registry: Registry,
) -> None:
    terminal = _root_node(lower_catalog_metric(catalog_registry, "test.terminal_orders"))

    assert isinstance(terminal, AggregateNodeV1)
    assert terminal.filter[0].dimension_ref.path == "test.orders.type"
    assert terminal.filter[0].value == (
        ("op", "in"),
        ("value", (2, 4)),
    )


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
    assert root.anchor == ("grain_to_date", mv.grain("month"))
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
    assert original.bound_graph_fingerprint == changed.bound_graph_fingerprint


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
    assert original.bound_graph_fingerprint != changed.bound_graph_fingerprint


def test_projected_source_bindings_are_complete_dependency_identity() -> None:
    template = """\
import marivo.datasource as md
import marivo.semantic as ms
events = ms.entity(
    name="events",
    datasource=ms.ref.datasource("wh"),
    source=md.table("events", database="warehouse", columns={{{bindings}}}),
)
score = ms.measure_column(
    name="score",
    entity=events,
    column="score",
    additivity=ms.additive_all(),
)
total = ms.aggregate(name="total", measure=score, agg="sum")
"""
    first_source = template.format(
        bindings=('"score": "generated.score", "event_time": "event.timestamp"')
    )
    reordered_source = template.format(
        bindings=('"event_time": "event.timestamp", "score": "generated.score"')
    )
    changed_source = reordered_source.replace('"generated.score"', '"generated.score.v2"')

    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(first_source) as first_result:
        assert first_result.registry is not None
        first = lower_catalog_metric(first_result.registry, "test.total")
    with load_inline_semantic(reordered_source) as reordered_result:
        assert reordered_result.registry is not None
        reordered = lower_catalog_metric(reordered_result.registry, "test.total")
    with load_inline_semantic(changed_source) as changed_result:
        assert changed_result.registry is not None
        changed = lower_catalog_metric(changed_result.registry, "test.total")

    entity_entry = next(
        entry for entry in first.dependency_digest.entries if entry.ref.path == "test.events"
    )
    source_field = dict(entity_entry.fields)["source"]
    assert source_field == (
        (
            "columns",
            (
                ("event_time", "event.timestamp"),
                ("score", "generated.score"),
            ),
        ),
        ("database", "warehouse"),
        ("kind", "table"),
        ("table", "events"),
    )
    assert first.dependency_digest == reordered.dependency_digest
    assert first.dependency_digest.digest != changed.dependency_digest.digest


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
