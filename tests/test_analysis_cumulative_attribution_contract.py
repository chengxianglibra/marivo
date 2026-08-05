"""Compact cumulative attribution contract and admission behavior."""

from __future__ import annotations

from dataclasses import replace

import pytest

from marivo.analysis._semantic_persistence import AxisBindingV1
from marivo.analysis.cumulative_attribution import (
    AvailableCumulativeBridgeV1,
    BlockedCumulativeBridgeV1,
    CumulativeAttributionContractV1,
    CumulativeBridgeGrainV1,
    DirectCumulativeAttributionV1,
    RatioCumulativeAttributionV1,
    WeightedCumulativeAttributionV1,
    build_cumulative_attribution_contract,
    classify_cumulative_attribution_route,
    cumulative_attribution_capability,
    cumulative_attribution_method,
    derive_cumulative_bridge,
    project_cumulative_attribution_structure,
    select_cumulative_attribution_route,
)
from marivo.analysis.windows.grain import Grain
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory
from marivo.semantic.ir import AggKind, AggregateFoldInput
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CumulativeNodeV1,
    ExpressionOccurrenceV1,
    MetricExpressionGraphV1,
    MetricGraphNodeRecordV1,
    MetricGraphNodeV1,
    RatioNodeV1,
    WeightedMeanAggregateNodeV1,
)
from marivo.semantic.metric_graph_canonical import node_fingerprint


def _time(path: str = "sales.orders.created_at") -> RefPayloadV1:
    return RefPayloadV1.from_ref(ref_factory.time_dimension(path))


def _dimension(path: str = "sales.orders.region") -> RefPayloadV1:
    return RefPayloadV1.from_ref(ref_factory.dimension(path))


def _record(node: MetricGraphNodeV1) -> MetricGraphNodeRecordV1:
    return MetricGraphNodeRecordV1(node_id=node_fingerprint(node), node=node)


def _direct_graph(
    *,
    aggregation: AggKind = "sum",
    over: str = "sales.orders.created_at",
    target: str = "sales.orders.amount",
    fold: AggregateFoldInput = None,
    unit_override: str | None = None,
) -> MetricExpressionGraphV1:
    aggregate = _record(
        AggregateNodeV1(
            kind="aggregate",
            target_ref=RefPayloadV1.from_ref(ref_factory.measure(target)),
            dependency_fingerprint=f"sha256:{target}",
            agg=aggregation,
            fold=fold,
            unit_override=unit_override,
        )
    )
    cumulative = _record(
        CumulativeNodeV1(
            kind="cumulative",
            child_id=aggregate.node_id,
            time_dimension_ref=_time(over),
            anchor="all_history",
            dependency_fingerprint="sha256:cumulative",
        )
    )
    return MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(cumulative.node_id,),
        nodes=tuple(sorted((aggregate, cumulative), key=lambda item: item.node_id)),
        occurrences=(
            ExpressionOccurrenceV1(
                path="root[0]",
                node_id=cumulative.node_id,
                child_paths=("root[0].base",),
            ),
            ExpressionOccurrenceV1(path="root[0].base", node_id=aggregate.node_id),
        ),
    )


def _ratio_graph() -> MetricExpressionGraphV1:
    numerator_graph = _direct_graph(target="sales.orders.amount")
    denominator_graph = _direct_graph(aggregation="count", target="sales.orders.order_id")
    numerator = numerator_graph.nodes
    denominator = denominator_graph.nodes
    numerator_root = numerator_graph.roots[0]
    denominator_root = denominator_graph.roots[0]
    ratio = _record(
        RatioNodeV1(
            kind="ratio",
            numerator_id=numerator_root,
            denominator_id=denominator_root,
            zero_division="null",
        )
    )
    return MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(ratio.node_id,),
        nodes=tuple(sorted((*numerator, *denominator, ratio), key=lambda item: item.node_id)),
        occurrences=(
            ExpressionOccurrenceV1(
                path="root[0]",
                node_id=ratio.node_id,
                child_paths=("root[0].numerator", "root[0].denominator"),
            ),
            ExpressionOccurrenceV1(
                path="root[0].numerator",
                node_id=numerator_root,
                child_paths=("root[0].numerator.base",),
            ),
            ExpressionOccurrenceV1(
                path="root[0].numerator.base",
                node_id=next(
                    record.node.child_id
                    for record in numerator
                    if isinstance(record.node, CumulativeNodeV1)
                ),
            ),
            ExpressionOccurrenceV1(
                path="root[0].denominator",
                node_id=denominator_root,
                child_paths=("root[0].denominator.base",),
            ),
            ExpressionOccurrenceV1(
                path="root[0].denominator.base",
                node_id=next(
                    record.node.child_id
                    for record in denominator
                    if isinstance(record.node, CumulativeNodeV1)
                ),
            ),
        ),
    )


def _weighted_graph(*, unit_override: str | None = None) -> MetricExpressionGraphV1:
    aggregate = _record(
        WeightedMeanAggregateNodeV1(
            kind="weighted_mean",
            value_ref=RefPayloadV1.from_ref(ref_factory.measure("sales.orders.price")),
            weight_ref=RefPayloadV1.from_ref(ref_factory.measure("sales.orders.quantity")),
            value_dependency_fingerprint="sha256:value",
            weight_dependency_fingerprint="sha256:weight",
            unit_override=unit_override,
        )
    )
    cumulative = _record(
        CumulativeNodeV1(
            kind="cumulative",
            child_id=aggregate.node_id,
            time_dimension_ref=_time(),
            anchor="all_history",
            dependency_fingerprint="sha256:cumulative",
        )
    )
    return MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(cumulative.node_id,),
        nodes=tuple(sorted((aggregate, cumulative), key=lambda item: item.node_id)),
        occurrences=(
            ExpressionOccurrenceV1(
                path="root[0]",
                node_id=cumulative.node_id,
                child_paths=("root[0].base",),
            ),
            ExpressionOccurrenceV1(path="root[0].base", node_id=aggregate.node_id),
        ),
    )


def _marker(*, over: str = "sales.orders.created_at") -> dict[str, object]:
    return {
        "kind": "cumulative",
        "base": "sales.gmv",
        "over": over,
        "anchor": "all_history",
        "components": None,
    }


def _bridge() -> AvailableCumulativeBridgeV1:
    return AvailableCumulativeBridgeV1(
        value=CumulativeBridgeGrainV1(
            grain=Grain(unit="day"),
            report_timezone="Asia/Shanghai",
            origin="observation_query_grain",
        )
    )


def _contract(
    graph: MetricExpressionGraphV1,
    *,
    marker: dict[str, object] | None = None,
) -> CumulativeAttributionContractV1:
    cumulative = marker or _marker()
    return build_cumulative_attribution_contract(
        current_graph=graph,
        baseline_graph=graph,
        current_cumulative=cumulative,
        baseline_cumulative=cumulative,
        bridge=_bridge(),
    )


def test_direct_sum_contract_supports_both_routes() -> None:
    contract = _contract(_direct_graph())

    assert isinstance(contract.structure, DirectCumulativeAttributionV1)
    assert contract.structure.base.aggregation == "sum"
    assert cumulative_attribution_method(contract.structure) == "sum"
    capability = cumulative_attribution_capability(contract)
    assert capability.business_axes.status == "supported"
    assert capability.business_axes.path == "cumulative_level_decomposition"
    assert capability.accumulation_time.status == "supported"
    assert capability.accumulation_time.path == "accumulation_time_bridge"


def test_count_distinct_is_blocked_without_falling_through_to_sum() -> None:
    contract = _contract(_direct_graph(aggregation="count_distinct"))

    capability = cumulative_attribution_capability(contract)
    assert capability.business_axes.status == "blocked"
    assert capability.business_axes.blocker == "base_non_additive"
    assert capability.accumulation_time.status == "blocked"
    assert capability.accumulation_time.blocker == "base_non_additive"


def test_ratio_and_weighted_mean_keep_component_methods_and_block_time_bridge() -> None:
    component = _marker()
    ratio_marker = {
        "kind": "derived_contains_cumulative",
        "anchor": "all_history",
        "compare_blocker": None,
        "components": {"numerator": component, "denominator": component},
    }
    ratio = _contract(_ratio_graph(), marker=ratio_marker)
    weighted = _contract(_weighted_graph())

    assert isinstance(ratio.structure, RatioCumulativeAttributionV1)
    assert cumulative_attribution_method(ratio.structure) == "ratio_mix"
    assert ratio.structure.numerator.aggregation == "sum"
    assert ratio.structure.denominator.aggregation == "count"
    assert cumulative_attribution_capability(ratio).business_axes.status == "supported"
    assert (
        cumulative_attribution_capability(ratio).accumulation_time.blocker
        == "component_time_bridge_unsupported"
    )

    assert isinstance(weighted.structure, WeightedCumulativeAttributionV1)
    assert cumulative_attribution_method(weighted.structure) == "weighted_mix"
    assert weighted.structure.numerator.aggregation == "sum"
    assert weighted.structure.weight.aggregation == "sum"


def test_exact_over_ref_selects_time_route_and_mixed_axes_fail_closed() -> None:
    contract = _contract(_direct_graph())

    assert classify_cumulative_attribution_route(contract, (_dimension(),)) == "business_axes"
    assert classify_cumulative_attribution_route(contract, (_time(),)) == "accumulation_time"
    route, admission = select_cumulative_attribution_route(contract, (_dimension(), _time()))
    assert route == "mixed_axes"
    assert admission.status == "blocked"
    assert admission.blocker == "over_plus_business_axis_unsupported"


def test_bridge_uses_query_grain_and_retains_mismatch() -> None:
    binding = AxisBindingV1(
        ref=_time(),
        column="bucket_start",
        role="time_dimension",
        grain="day",
    )
    available = derive_cumulative_bridge(
        current_semantic_kind="time_series",
        baseline_semantic_kind="time_series",
        current_axis_bindings=(binding,),
        baseline_axis_bindings=(binding,),
        over_ref=_time(),
        current_declared_over_grain=None,
        baseline_declared_over_grain=None,
        current_report_timezone="Asia/Shanghai",
        baseline_report_timezone="Asia/Shanghai",
    )
    assert isinstance(available, AvailableCumulativeBridgeV1)
    assert available.value.grain == Grain(unit="day")
    assert available.value.origin == "observation_query_grain"

    mismatch = derive_cumulative_bridge(
        current_semantic_kind="time_series",
        baseline_semantic_kind="time_series",
        current_axis_bindings=(binding,),
        baseline_axis_bindings=(replace(binding, grain="week"),),
        over_ref=_time(),
        current_declared_over_grain=None,
        baseline_declared_over_grain=None,
        current_report_timezone="Asia/Shanghai",
        baseline_report_timezone="UTC",
    )
    assert isinstance(mismatch, BlockedCumulativeBridgeV1)
    assert mismatch.current_grain == Grain(unit="day")
    assert mismatch.baseline_grain == Grain(unit="week")
    assert mismatch.current_report_timezone == "Asia/Shanghai"
    assert mismatch.baseline_report_timezone == "UTC"


def test_scalar_bridge_uses_declared_granularity_or_fixed_day_default() -> None:
    declared = derive_cumulative_bridge(
        current_semantic_kind="scalar",
        baseline_semantic_kind="segmented",
        current_axis_bindings=(),
        baseline_axis_bindings=(),
        over_ref=_time(),
        current_declared_over_grain="hour",
        baseline_declared_over_grain="hour",
        current_report_timezone="UTC",
        baseline_report_timezone="UTC",
    )
    assert isinstance(declared, AvailableCumulativeBridgeV1)
    assert declared.value.grain == Grain(unit="hour")
    assert declared.value.origin == "over_declared_granularity"

    defaulted = derive_cumulative_bridge(
        current_semantic_kind="scalar",
        baseline_semantic_kind="scalar",
        current_axis_bindings=(),
        baseline_axis_bindings=(),
        over_ref=_time(),
        current_declared_over_grain=None,
        baseline_declared_over_grain=None,
        current_report_timezone="UTC",
        baseline_report_timezone="UTC",
    )
    assert isinstance(defaulted, AvailableCumulativeBridgeV1)
    assert defaulted.value.grain == Grain(unit="day")
    assert defaulted.value.origin == "executor_day_default"


def test_contract_rejects_structure_or_over_ref_drift() -> None:
    with pytest.raises(ValueError, match="structure projections differ"):
        build_cumulative_attribution_contract(
            current_graph=_direct_graph(aggregation="sum"),
            baseline_graph=_direct_graph(aggregation="count"),
            current_cumulative=_marker(),
            baseline_cumulative=_marker(),
            bridge=_bridge(),
        )

    with pytest.raises(ValueError, match="different over refs"):
        build_cumulative_attribution_contract(
            current_graph=_direct_graph(),
            baseline_graph=_direct_graph(over="sales.orders.paid_at"),
            current_cumulative=_marker(),
            baseline_cumulative=_marker(over="sales.orders.paid_at"),
            bridge=_bridge(),
        )

    with pytest.raises(ValueError, match="graph over refs do not match"):
        build_cumulative_attribution_contract(
            current_graph=_direct_graph(over="sales.orders.paid_at"),
            baseline_graph=_direct_graph(over="sales.orders.paid_at"),
            current_cumulative=_marker(),
            baseline_cumulative=_marker(),
            bridge=_bridge(),
        )


def test_weighted_projection_covers_every_aggregate_semantic_field() -> None:
    mean = project_cumulative_attribution_structure(_direct_graph(aggregation="mean"))
    mean_with_fold = project_cumulative_attribution_structure(
        _direct_graph(aggregation="mean", fold="mean")
    )
    mean_with_unit = project_cumulative_attribution_structure(
        _direct_graph(aggregation="mean", unit_override="CNY")
    )
    weighted = project_cumulative_attribution_structure(_weighted_graph())
    weighted_with_unit = project_cumulative_attribution_structure(
        _weighted_graph(unit_override="CNY")
    )

    assert mean != mean_with_fold
    assert mean != mean_with_unit
    assert weighted != weighted_with_unit


def test_projection_rejects_unsupported_cumulative_base_aggregation() -> None:
    with pytest.raises(ValueError, match="sum, count, or count_distinct"):
        project_cumulative_attribution_structure(_direct_graph(aggregation="max"))
