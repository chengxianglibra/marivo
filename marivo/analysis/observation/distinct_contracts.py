"""Closed source-private distinct membership admission and retained part facts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import ibis.expr.datatypes as dt

from marivo.analysis.datasets.descriptors import DatasetRowContract, _canonical_digest
from marivo.analysis.observation.fold_contracts import (
    DistinctMembershipAuthorityV1,
    MetricFoldAuthorityV1,
)
from marivo.refs import SemanticKind
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CumulativeNodeV1,
    SliceNodeV1,
    TargetMetricContract,
    component_node,
)

if TYPE_CHECKING:
    from marivo.semantic._expression_binding import CompiledExpressionSidecar
    from marivo.semantic.validator import Registry

DISTINCT_KEY_COLUMN = "__mv_distinct_key"
DISTINCT_MEMBERSHIP_CONTRACT_IDS = ("metric.distinct_membership", "delta.distinct_membership")

_MISSING_AUTHORITY = "missing exact distinct membership authority"
_INVALID_ROOT = "invalid exact distinct membership root"
_INVALID_COMPONENT = "invalid exact distinct membership component"
_INVALID_SCALAR_KEY = "invalid scalar distinct membership key"
_INVALID_ENTITY_KEY = "invalid governed Entity distinct membership key"
_SAFE_AUTHORITY_REASONS = frozenset(
    (
        _MISSING_AUTHORITY,
        _INVALID_ROOT,
        _INVALID_COMPONENT,
        _INVALID_SCALAR_KEY,
        _INVALID_ENTITY_KEY,
    )
)


def supported_distinct_key_type(logical_type: str) -> bool:
    """Admit scalar keys with an exact source equality and distinct operation."""
    try:
        dtype: dt.DataType = dt.dtype(logical_type)
    except (TypeError, ValueError):
        return False
    return bool(
        dtype.is_boolean()
        or dtype.is_integer()
        or dtype.is_floating()
        or dtype.is_decimal()
        or dtype.is_string()
        or dtype.is_binary()
        or dtype.is_date()
        or dtype.is_time()
        or dtype.is_timestamp()
        or dtype.is_uuid()
    )


def make_distinct_membership(
    metric: TargetMetricContract,
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None,
) -> DistinctMembershipAuthorityV1 | None:
    """Capture one exact identity-chain distinct root from normalized source facts."""
    from marivo.semantic.metric_graph_lowering import _target_measure_type
    from marivo.semantic.validator import normalize_target_entity

    nodes = {item.node_id: item.node for item in metric.graph.nodes}
    node_id = metric.graph.roots[0]
    node = nodes[node_id]
    while isinstance(node, (SliceNodeV1, CumulativeNodeV1)):
        node_id = node.child_id
        node = nodes[node_id]
    if (
        not isinstance(node, AggregateNodeV1)
        or node.agg != "count_distinct"
        or node.fold is not None
        or len(metric.components) != 1
    ):
        return None
    component = metric.components[0]
    if component.time_fold is not None:
        return None
    node = component_node(metric.graph, component.node_id)
    if not isinstance(node, AggregateNodeV1):
        return None
    signature: tuple[tuple[str, str], ...] = ()
    source_column = ""
    target_kind: Literal["measure", "entity"]
    if node.target_ref.kind is SemanticKind.ENTITY:
        target = normalize_target_entity(registry, node.target_ref.path)
        signature = target.identity_signature
        if not signature or any(not supported_distinct_key_type(kind) for _, kind in signature):
            return None
        logical_type = str(dt.Struct.from_tuples(signature))
        target_kind = "entity"
    elif node.target_ref.kind is SemanticKind.MEASURE:
        logical_type, _, _ = _target_measure_type(
            registry, node.target_ref.path, sidecar, metric_id=metric.key
        )
        if not supported_distinct_key_type(logical_type):
            return None
        assert sidecar is not None
        from marivo.refs import _create_ref

        body = sidecar.bodies[_create_ref(SemanticKind.MEASURE, node.target_ref.path)]
        assert body.source_column is not None
        source_column = body.source_column
        logical_type = str(dt.dtype(logical_type))
        target_kind = "measure"
    else:
        return None
    return DistinctMembershipAuthorityV1(
        metric_ref=metric.key,
        aggregate_node_id=component.node_id,
        target_ref=node.target_ref.path,
        target_kind=target_kind,
        computation_root=component.computation_root.path,
        key_logical_type=logical_type,
        source_column=source_column,
        identity_signature=signature,
    )


def validate_membership_authority(authority: MetricFoldAuthorityV1) -> None:
    """Validate the closed key and single component closure without source access."""
    membership = authority.membership
    if membership is None:
        raise ValueError(_MISSING_AUTHORITY)
    nodes = {node.node_id: node for node in authority.nodes}
    root = nodes.get(authority.root_id)
    visited: set[str] = set()
    while root is not None and root.kind == "identity":
        if root.node_id in visited or len(root.children) != 1:
            raise ValueError(_INVALID_ROOT)
        visited.add(root.node_id)
        root = nodes.get(root.children[0])
    if (
        root is None
        or membership.metric_ref != authority.metric_ref
        or membership.aggregate_node_id != root.node_id
        or root.kind != "component"
        or len(authority.components) != 1
        or not membership.target_ref
        or not membership.computation_root
    ):
        raise ValueError(_INVALID_ROOT)
    component = authority.components[0]
    if (
        component.node_id != membership.aggregate_node_id
        or component.kind != "opaque"
        or tuple(state for state, _ in component.state_columns) != ("value", "row_count")
        or component.empty_rule != "zero"
        or component.null_rule != "ignore_null_inputs"
        or component.time_merge != ("last" if component.cumulative else "blocked")
        or component.spatial_merge
        not in (("blocked", "sum") if membership.target_kind == "entity" else ("blocked",))
    ):
        raise ValueError(_INVALID_COMPONENT)
    if membership.target_kind == "measure":
        if (
            not supported_distinct_key_type(membership.key_logical_type)
            or not membership.source_column
            or membership.identity_signature
        ):
            raise ValueError(_INVALID_SCALAR_KEY)
    elif (
        membership.source_column
        or not membership.identity_signature
        or len(dict(membership.identity_signature)) != len(membership.identity_signature)
        or any(
            not name or not supported_distinct_key_type(kind)
            for name, kind in membership.identity_signature
        )
        or membership.key_logical_type != str(dt.Struct.from_tuples(membership.identity_signature))
    ):
        raise ValueError(_INVALID_ENTITY_KEY)


def membership_authority_reason(error: ValueError) -> str:
    """Disclose only fixed owner-issued reasons; arbitrary backend values stay private."""
    reason: object = error.args[0] if len(error.args) == 1 else None
    return (
        reason
        if type(reason) is str and reason in _SAFE_AUTHORITY_REASONS
        else "invalid membership authority"
    )


def metric_membership_role(authority: MetricFoldAuthorityV1) -> str:
    return f"metric_membership.{_canonical_digest(authority.metric_ref)[:20]}"


def membership_part_authorities(
    row: DatasetRowContract,
) -> tuple[tuple[str, MetricFoldAuthorityV1], ...]:
    """Read only retained row authority; no source graph or live catalog is needed."""
    from marivo.analysis.observation.contracts import (
        EntityPresentMetricSemantics,
        EntityReducedMetricSemantics,
    )
    from marivo.analysis.operators.attribution_contracts import delta_part_authorities
    from marivo.analysis.operators.contracts import DeltaSemantics

    semantics = row.family_semantics
    if isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        return tuple(
            (metric_membership_role(authority), authority)
            for authority in semantics.metric_folds
            if authority.membership is not None
        )
    if isinstance(semantics, DeltaSemantics):
        return tuple(
            (role.replace("delta_components.", "delta_membership."), authority)
            for role, authority in delta_part_authorities(row)
            if authority.membership is not None
        )
    return ()


def membership_endpoint_name(row: DatasetRowContract, role: str) -> str:
    """Resolve the exact membership endpoint using only its owning row authority."""
    from marivo.analysis.operators.contracts import DeltaSemantics

    authority = next(
        (item for name, item in membership_part_authorities(row) if name == role), None
    )
    if authority is None:
        raise ValueError("unknown distinct membership role")
    if isinstance(row.family_semantics, DeltaSemantics):
        return "current_value" if role == "delta_membership.current" else "baseline_value"
    field = next(
        (field for field in row.schema.columns if field.field_id.value == authority.field_id), None
    )
    if field is None:
        raise ValueError("missing distinct membership endpoint")
    return field.name
