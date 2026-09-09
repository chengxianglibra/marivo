"""Closed source-private percentile state and method admission."""

from __future__ import annotations

from typing import TYPE_CHECKING

import ibis.expr.datatypes as dt

from marivo.analysis.datasets.descriptors import DatasetRowContract, _canonical_digest
from marivo.analysis.observation.fold_contracts import (
    DistributionAuthorityV1,
    MetricFoldAuthorityV1,
)
from marivo.refs import SemanticKind
from marivo.semantic._quantile import QuantileMethod, QuantileMethodV1
from marivo.semantic.metric_graph import AggregateNodeV1, SliceNodeV1, TargetMetricContract

if TYPE_CHECKING:
    from marivo.semantic._expression_binding import CompiledExpressionSidecar
    from marivo.semantic.validator import Registry

VALUE = "__mv_distribution_value"
FREQUENCY = "__mv_distribution_frequency"
DISTRIBUTION_CONTRACT_IDS = frozenset({"metric.distribution", "delta.distribution"})


def make_distribution(
    metric: TargetMetricContract,
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None,
    method: QuantileMethod = "linear_interpolation@v1",
) -> DistributionAuthorityV1 | None:
    """Capture a governed root percentile without source access or inferred semantics."""
    from marivo.semantic.metric_graph_lowering import _target_measure_type

    nodes = {item.node_id: item.node for item in metric.graph.nodes}
    node = nodes[metric.graph.roots[0]]
    while isinstance(node, SliceNodeV1):
        node = nodes[node.child_id]
    if (
        not isinstance(node, AggregateNodeV1)
        or (node.agg != "median" and not isinstance(node.agg, tuple))
        or node.target_ref.kind is not SemanticKind.MEASURE
        or node.fold is not None
        or metric.cumulative
        or len(metric.components) != 1
        or metric.components[0].time_fold is not None
    ):
        return None
    kind, _, _ = _target_measure_type(
        registry, node.target_ref.path, sidecar, metric_id=metric.ref.path
    )
    if not dt.dtype(kind).is_numeric():
        return None
    from marivo.refs import _create_ref

    assert sidecar is not None
    column = sidecar.bodies[_create_ref(SemanticKind.MEASURE, node.target_ref.path)].source_column
    assert column is not None
    return DistributionAuthorityV1(
        metric_ref=metric.ref.path,
        aggregate_node_id=metric.components[0].node_id,
        target_ref=node.target_ref.path,
        computation_root=metric.components[0].computation_root.path,
        value_logical_type=str(dt.dtype(kind)),
        source_column=column,
        quantile=QuantileMethodV1(
            method=method, q=node.agg[1] if isinstance(node.agg, tuple) else 0.5
        ),
    )


def validate_distribution_authority(authority: MetricFoldAuthorityV1) -> None:
    basis = authority.distribution
    if basis is None or authority.membership is not None:
        raise ValueError("missing or conflicting percentile authority")
    nodes = {node.node_id: node for node in authority.nodes}
    root = nodes[authority.root_id]
    while root.kind == "identity":
        root = nodes[root.children[0]]
    if (
        root.kind != "component"
        or root.node_id != basis.aggregate_node_id
        or basis.metric_ref != authority.metric_ref
        or len(authority.components) != 1
        or not basis.target_ref
        or not basis.computation_root
        or not basis.source_column
        or not dt.dtype(basis.value_logical_type).is_numeric()
    ):
        raise ValueError("invalid percentile component closure")
    component = authority.components[0]
    if (
        component.node_id != root.node_id
        or component.kind != "opaque"
        or component.cumulative
        or component.time_merge != "blocked"
        or component.spatial_merge != "blocked"
        or component.empty_rule != "null"
        or component.null_rule != "ignore_null_inputs"
        or tuple(state for state, _ in component.state_columns)
        != ("value", "non_null_count", "row_count")
    ):
        raise ValueError("invalid percentile retained component semantics")


def distribution_part_authorities(
    row: DatasetRowContract,
) -> tuple[tuple[str, MetricFoldAuthorityV1], ...]:
    from marivo.analysis.observation.contracts import (
        EntityPresentMetricSemantics,
        EntityReducedMetricSemantics,
    )
    from marivo.analysis.operators.attribution_contracts import delta_part_authorities
    from marivo.analysis.operators.contracts import DeltaSemantics

    semantics = row.family_semantics
    if isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        return tuple(
            (f"metric_distribution.{_canonical_digest(item.metric_ref)[:20]}", item)
            for item in semantics.metric_folds
            if item.distribution is not None
        )
    if isinstance(semantics, DeltaSemantics):
        return tuple(
            (role.replace("delta_components.", "delta_distribution."), item)
            for role, item in delta_part_authorities(row)
            if item.distribution is not None
        )
    return ()


def distribution_endpoint_name(row: DatasetRowContract, role: str) -> str:
    authority = next(item for name, item in distribution_part_authorities(row) if name == role)
    if role.startswith("delta_distribution."):
        return role.removeprefix("delta_distribution.") + "_value"
    return next(
        field.name for field in row.schema.columns if field.field_id.value == authority.field_id
    )


def semantic_approximation(payloads: tuple[str, ...]) -> bool:
    from marivo.analysis.observation.fold_contracts import decode_fold_authority

    return any(
        metric.distribution is not None
        and metric.distribution.quantile.method == "duckdb_tdigest@v1"
        for payload in payloads
        for metric in decode_fold_authority(payload).metrics
    )
