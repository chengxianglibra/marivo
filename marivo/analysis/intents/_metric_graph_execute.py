"""Bottom-up execution for planned metric-expression forests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from marivo.analysis._cumulative import CUMULATIVE_CONTRACT_VERSION
from marivo.analysis.executor.bucketing import (
    apply_time_series_bucket,
    ensure_bucket_start_timestamp,
)
from marivo.analysis.executor.runner import execute
from marivo.analysis.executor.windowing import (
    datasource_engine_profile,
    datasource_read_timezone,
    resolve_window_time_field,
)
from marivo.analysis.intents._metric_evaluators import (
    AggregateEvaluationV1,
    MetricEvaluationQualityV1,
    RatioEvaluationV1,
    align_metric_children_v1,
    evaluate_linear_v1,
    evaluate_weighted_average_v1,
)
from marivo.analysis.intents._metric_graph_plan import MetricGraphObservePlanV1
from marivo.analysis.intents._observe_base import (
    _execute_base,
    _is_lowerable_tier1_mean,
)
from marivo.analysis.intents._observe_catalog import (
    _build_entity_adapter,
    _entity_details,
    _field_details,
)
from marivo.analysis.intents._observe_components import _role_to_column_name
from marivo.analysis.intents._observe_cumulative import _execute_cumulative
from marivo.analysis.intents._observe_derived import _merge_component_coverages
from marivo.analysis.intents._observe_inputs import _metric_expr
from marivo.analysis.intents._observe_planner_types import (
    BaseObservePlan,
    CumulativePhysicalLeafPlanV1,
)
from marivo.analysis.intents.observe_planner import _validate_field_expr
from marivo.analysis.windows.grain import ensure_grain_supported
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CatalogBodyLeafV1,
    CumulativeNodeV1,
    LinearNodeV1,
    RatioNodeV1,
    SliceNodeV1,
    WeightedAverageNodeV1,
    node_child_ids,
)
from marivo.semantic.metric_graph_canonical import canonical_value
from marivo.semantic.unit_algebra import (
    MetricUnitStateV2,
    UnknownUnitV2,
    divide_unit_states,
    linear_unit,
    render_unit,
    unit_state,
    weighted_average_unit,
)

type SemanticShapeV1 = Literal["scalar", "time_series", "segmented", "panel"]


@dataclass(frozen=True)
class GraphNodeExecutionV1:
    """Materialized value and recursive semantic facts for one graph node."""

    node_id: str
    frame: Any
    key_columns: tuple[str, ...]
    axes: dict[str, Any]
    semantic_kind: SemanticShapeV1
    unit: str | None
    unit_state: MetricUnitStateV2
    unit_capability_issue: str | None
    additivity: str | None
    fold: Any | None
    coverage_df: Any | None
    quality: MetricEvaluationQualityV1
    aggregate_component_df: Any | None = None


def _record_physical_execution(
    evaluated: dict[str, GraphNodeExecutionV1],
    leaf: Any,
    result: GraphNodeExecutionV1,
) -> None:
    """Retain truthful state for both a pushed slice and its governed child."""

    evaluated[leaf.node_id] = result
    if leaf.value_node_id != leaf.node_id:
        evaluated.setdefault(
            leaf.value_node_id,
            replace(result, node_id=leaf.value_node_id),
        )


@dataclass(frozen=True)
class MetricGraphExecutionV1:
    """All evaluated nodes and the ordered materialized roots."""

    roots: tuple[GraphNodeExecutionV1, ...]
    nodes: dict[str, GraphNodeExecutionV1]
    physical_execution_count: int


def _base_plan_surface(plan: BaseObservePlan) -> tuple[Any, ...]:
    """Return the non-expression facts that must match for query fusion."""

    return (
        plan.datasource_name,
        plan.root_entity,
        tuple((dimension.field.semantic_id, dimension.column) for dimension in plan.dimensions),
        tuple(sorted(plan.lineage_metadata.get("relationships") or ())),
        tuple(sorted(plan.lineage_metadata.get("version_resolutions") or ())),
    )


def _tables_equal(left: Any, right: Any) -> bool:
    equals = getattr(left, "equals", None)
    if callable(equals):
        try:
            return bool(equals(right))
        except Exception:
            return False
    return left is right


def _can_fuse_base(left: Any, right: Any) -> bool:
    if not isinstance(left.plan, BaseObservePlan) or not isinstance(right.plan, BaseObservePlan):
        return False
    if (
        getattr(left.metric_ir, "time_fold", None) is not None
        or getattr(right.metric_ir, "time_fold", None) is not None
    ):
        return False
    if _is_lowerable_tier1_mean(left.metric_ir) or _is_lowerable_tier1_mean(right.metric_ir):
        return False
    return _base_plan_surface(left.plan) == _base_plan_surface(right.plan) and _tables_equal(
        left.plan.table, right.plan.table
    )


def _fused_base_groups(leaves: tuple[Any, ...]) -> list[list[Any]]:
    """Partition physical leaves into conservative single-query groups."""

    groups: list[list[Any]] = []
    for leaf in leaves:
        for group in groups:
            if _can_fuse_base(group[0], leaf):
                group.append(leaf)
                break
        else:
            groups.append([leaf])
    return groups


def _execute_fused_base_group(
    group: list[Any],
    *,
    catalog: Any,
    resolver: Any,
    session: Any,
    resolved_window: Any | None,
) -> tuple[dict[str, Any], dict[str, Any], SemanticShapeV1]:
    """Execute compatible aggregate leaves in one backend query."""

    first = group[0]
    plan = cast("BaseObservePlan", first.plan)
    table = plan.table
    resolved_dimensions = [
        (dimension.field.entity, dimension.field) for dimension in plan.dimensions
    ]
    is_time_series = resolved_window is not None and resolved_window.grain is not None
    axes: dict[str, Any] = {}
    time_dimension_ir = None
    root_adapter = None
    if is_time_series:
        assert resolved_window is not None
        assert resolved_window.grain is not None
        root_adapter = _build_entity_adapter(
            catalog, resolver, _entity_details(catalog, plan.root_entity)
        )
        time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
        base = (
            time_dimension_ir.time_meta.granularity if time_dimension_ir.time_meta else None
        ) or "day"
        ensure_grain_supported(resolved_window.grain, base)
        table = apply_time_series_bucket(
            table,
            field_ir=time_dimension_ir,
            window=resolved_window,
            report_tz=cast("ZoneInfo", session.report_tz),
            datasource_read_tz=datasource_read_timezone(
                session._connection_runtime, plan.datasource_name
            ),
            profile=datasource_engine_profile(session._connection_runtime, plan.datasource_name),
            dataset_ir=root_adapter,
        )
    dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
    if dimension_names:
        table = table.mutate(
            **{
                field_ir.name: _validate_field_expr(
                    resolver.dimension_on(_field_details(catalog, field_ir.semantic_id).ref, table),
                    field_id=field_ir.semantic_id,
                ).name(field_ir.name)
                for _, field_ir in resolved_dimensions
            }
        )
    aggregations: dict[str, Any] = {}
    output_names: dict[str, str] = {}
    for index, leaf in enumerate(group):
        output_name = f"__marivo_metric_{index}"
        output_names[leaf.node_id] = output_name
        datasets = tuple(leaf.metric_ir.entities)
        aggregations[output_name] = _metric_expr(
            catalog,
            resolver,
            leaf.metric_ir.semantic_id,
            datasets,
            dict.fromkeys(datasets, table),
            metric_ir=leaf.metric_ir,
        )
    group_names = (["bucket_start"] if is_time_series else []) + dimension_names
    if group_names:
        expression = (
            table.group_by(group_names)
            .aggregate(**aggregations)
            .order_by(group_names)
            .select(*group_names, *aggregations)
        )
    else:
        expression = table.aggregate(**aggregations)
    result = execute(
        expression,
        datasource_name=plan.datasource_name,
        cache=session._connection_runtime,
        session_id=session.id,
    )
    if is_time_series and "bucket_start" in result.df:
        assert resolved_window is not None
        assert resolved_window.grain is not None
        assert time_dimension_ir is not None
        assert root_adapter is not None
        result.df["bucket_start"] = ensure_bucket_start_timestamp(
            result.df["bucket_start"],
            time_meta=time_dimension_ir.time_meta,
            dataset_ir=root_adapter,
            grain=resolved_window.grain,
            report_tz=cast("ZoneInfo", session.report_tz),
            backend_datetime_decode_policy=result.backend_datetime_decode_policy,
        )
        axes["time"] = {
            "role": "time",
            "column": "bucket_start",
            "grain": resolved_window.grain.to_token(),
            "time_dimension": time_dimension_ir.name,
            "ref": time_dimension_ir.semantic_id,
        }
    axes.update(
        {
            field_ir.name: {
                "role": "dimension",
                "column": field_ir.name,
                "ref": field_ir.semantic_id,
            }
            for _, field_ir in resolved_dimensions
        }
    )
    if is_time_series and dimension_names:
        semantic_kind: SemanticShapeV1 = "panel"
    elif is_time_series:
        semantic_kind = "time_series"
    elif dimension_names:
        semantic_kind = "segmented"
    else:
        semantic_kind = "scalar"
    axis_columns = tuple(group_names)
    per_leaf = {
        leaf.node_id: replace(
            result,
            df=result.df[[*axis_columns, output_names[leaf.node_id]]]
            .rename(columns={output_names[leaf.node_id]: "value"})
            .copy(),
        )
        for leaf in group
    }
    return per_leaf, axes, semantic_kind


def _child_contract(
    left: GraphNodeExecutionV1,
    right: GraphNodeExecutionV1,
    *,
    node_id: str,
) -> tuple[dict[str, Any], SemanticShapeV1]:
    if left.axes != right.axes or left.semantic_kind != right.semantic_kind:
        raise ValueError(f"metric graph node {node_id} has incompatible child observation shapes")
    return left.axes, left.semantic_kind


def _merge_coverage(children: tuple[GraphNodeExecutionV1, ...]) -> Any | None:
    frames = [child.coverage_df for child in children if child.coverage_df is not None]
    if not frames:
        return None
    return _merge_component_coverages(frames, list(children[0].key_columns))


def execute_metric_graph_observe(
    plan: MetricGraphObservePlanV1,
    *,
    catalog: Any,
    resolver: Any,
    session: Any,
    resolved_window: Any | None,
) -> MetricGraphExecutionV1:
    """Execute every physical node once and visit composition nodes bottom-up."""

    graph_nodes = {record.node_id: record.node for record in plan.graph.nodes}
    evaluated: dict[str, GraphNodeExecutionV1] = {}

    # Physical nodes are keyed by expression fingerprint. Repeated catalog
    # refs and repeated graph occurrences therefore share one execution, while
    # compatible distinct aggregate leaves share one backend query.
    physical_execution_count = 0
    for group in _fused_base_groups(plan.leaves):
        if len(group) > 1:
            per_leaf, axes, semantic_kind = _execute_fused_base_group(
                group,
                catalog=catalog,
                resolver=resolver,
                session=session,
                resolved_window=resolved_window,
            )
            physical_execution_count += 1
            for leaf in group:
                aggregate = AggregateEvaluationV1().evaluate(per_leaf[leaf.node_id].df)
                unit = getattr(leaf.metric_ir, "unit", None)
                result = GraphNodeExecutionV1(
                    node_id=leaf.node_id,
                    frame=aggregate.frame,
                    key_columns=aggregate.key_columns,
                    axes=axes,
                    semantic_kind=semantic_kind,
                    unit=unit,
                    unit_state=unit_state(unit),
                    unit_capability_issue=None,
                    additivity=getattr(leaf.metric_ir, "additivity", None),
                    fold=None,
                    coverage_df=None,
                    quality=aggregate.quality,
                )
                _record_physical_execution(evaluated, leaf, result)
            continue

        leaf = group[0]
        if isinstance(leaf.plan, CumulativePhysicalLeafPlanV1):
            result, axes, semantic_kind, coverage_df = _execute_cumulative(
                leaf.plan,
                catalog=catalog,
                resolver=resolver,
                session=session,
                resolved_window=resolved_window,
            )
            mean_component_df = None
        else:
            result, axes, semantic_kind, coverage_df, mean_component_df = _execute_base(
                leaf.plan,
                leaf.metric_ir,
                catalog=catalog,
                resolver=resolver,
                session=session,
                dimensions=None,
                resolved_window=resolved_window,
            )
        physical_execution_count += 1
        aggregate = AggregateEvaluationV1().evaluate(result.df)
        unit = getattr(leaf.metric_ir, "unit", None)
        result = GraphNodeExecutionV1(
            node_id=leaf.node_id,
            frame=aggregate.frame,
            key_columns=aggregate.key_columns,
            axes=axes,
            semantic_kind=semantic_kind,
            unit=unit,
            unit_state=unit_state(unit),
            unit_capability_issue=None,
            additivity=getattr(leaf.metric_ir, "additivity", None),
            fold=(
                leaf.metric_ir.time_fold.label()
                if getattr(leaf.metric_ir, "time_fold", None) is not None
                else None
            ),
            coverage_df=coverage_df,
            quality=aggregate.quality,
            aggregate_component_df=mean_component_df,
        )
        _record_physical_execution(evaluated, leaf, result)

    visiting: set[str] = set()

    def visit(node_id: str) -> GraphNodeExecutionV1:
        existing = evaluated.get(node_id)
        if existing is not None:
            return existing
        if node_id in visiting:
            raise ValueError(f"metric graph cycle reached during execution at {node_id}")
        visiting.add(node_id)
        node = graph_nodes[node_id]
        if isinstance(node, (CatalogBodyLeafV1, AggregateNodeV1, CumulativeNodeV1)):
            raise ValueError(f"metric graph physical node {node_id} was not planned")
        if isinstance(node, SliceNodeV1):
            # Slice canonicalization places predicates at physical leaves. A
            # runtime planner may therefore pre-materialize this node directly;
            # catalog-only graphs reach this fallback only for a semantically
            # redundant wrapper.
            child = visit(node.child_id)
            resolved = GraphNodeExecutionV1(
                node_id=node_id,
                frame=child.frame,
                key_columns=child.key_columns,
                axes=child.axes,
                semantic_kind=child.semantic_kind,
                unit=child.unit,
                unit_state=child.unit_state,
                unit_capability_issue=child.unit_capability_issue,
                additivity=child.additivity,
                fold=child.fold,
                coverage_df=child.coverage_df,
                quality=child.quality,
                aggregate_component_df=child.aggregate_component_df,
            )
        elif isinstance(node, RatioNodeV1):
            numerator = visit(node.numerator_id)
            denominator = visit(node.denominator_id)
            axes, semantic_kind = _child_contract(numerator, denominator, node_id=node_id)
            evaluation = RatioEvaluationV1().evaluate(
                numerator.frame,
                denominator.frame,
                zero_division=node.zero_division,
            )
            resolved_unit_state = (
                unit_state(node.unit_override)
                if node.unit_override is not None
                else divide_unit_states(numerator.unit_state, denominator.unit_state)
            )
            resolved = GraphNodeExecutionV1(
                node_id=node_id,
                frame=evaluation.frame,
                key_columns=evaluation.key_columns,
                axes=axes,
                semantic_kind=semantic_kind,
                unit=render_unit(resolved_unit_state),
                unit_state=resolved_unit_state,
                unit_capability_issue=(
                    "unit_algebra_unsupported"
                    if isinstance(resolved_unit_state, UnknownUnitV2) and node.unit_override is None
                    else numerator.unit_capability_issue or denominator.unit_capability_issue
                ),
                additivity="non_additive",
                fold=None,
                coverage_df=_merge_coverage((numerator, denominator)),
                quality=evaluation.quality,
            )
        elif isinstance(node, WeightedAverageNodeV1):
            value = visit(node.value_id)
            weight = visit(node.weight_id)
            axes, semantic_kind = _child_contract(value, weight, node_id=node_id)
            evaluation = evaluate_weighted_average_v1(value.frame, weight.frame)
            resolved_unit = node.unit_override or weighted_average_unit(value.unit)
            resolved_unit_state = unit_state(resolved_unit)
            resolved = GraphNodeExecutionV1(
                node_id=node_id,
                frame=evaluation.frame,
                key_columns=evaluation.key_columns,
                axes=axes,
                semantic_kind=semantic_kind,
                unit=resolved_unit,
                unit_state=resolved_unit_state,
                unit_capability_issue=(
                    "unit_unknown"
                    if isinstance(resolved_unit_state, UnknownUnitV2)
                    else value.unit_capability_issue
                ),
                additivity="non_additive",
                fold=None,
                coverage_df=_merge_coverage((value, weight)),
                quality=evaluation.quality,
            )
        elif isinstance(node, LinearNodeV1):
            children = tuple(visit(term.child_id) for term in node.terms)
            first = children[0]
            for child in children[1:]:
                _child_contract(first, child, node_id=node_id)
            evaluation = evaluate_linear_v1(
                tuple(
                    (f"term{index}", term.coefficient, child.frame)
                    for index, (term, child) in enumerate(zip(node.terms, children, strict=True))
                )
            )
            resolved_unit = node.unit_override or linear_unit(
                tuple(child.unit for child in children)
            )
            resolved_unit_state = unit_state(resolved_unit)
            resolved = GraphNodeExecutionV1(
                node_id=node_id,
                frame=evaluation.frame,
                key_columns=evaluation.key_columns,
                axes=first.axes,
                semantic_kind=first.semantic_kind,
                unit=resolved_unit,
                unit_state=resolved_unit_state,
                unit_capability_issue=(
                    "unit_unknown"
                    if isinstance(resolved_unit_state, UnknownUnitV2)
                    else next(
                        (
                            child.unit_capability_issue
                            for child in children
                            if child.unit_capability_issue is not None
                        ),
                        None,
                    )
                ),
                additivity=(
                    first.additivity
                    if all(child.additivity == first.additivity for child in children)
                    else "non_additive"
                ),
                fold=None,
                coverage_df=_merge_coverage(children),
                quality=evaluation.quality,
            )
        else:
            raise TypeError(f"unsupported metric graph node {type(node).__name__}")
        visiting.remove(node_id)
        evaluated[node_id] = resolved
        return resolved

    roots = tuple(visit(root_id) for root_id in plan.graph.roots)
    return MetricGraphExecutionV1(
        roots=roots,
        nodes=evaluated,
        physical_execution_count=physical_execution_count,
    )


def root_component_frame_v1(
    execution: MetricGraphExecutionV1,
    plan: MetricGraphObservePlanV1,
    *,
    root_index: int,
    metric_ir: Any,
) -> Any | None:
    """Render the immediate root roles without losing recursive node state."""

    node = {record.node_id: record.node for record in plan.graph.nodes}[
        plan.graph.roots[root_index]
    ]
    children: tuple[tuple[str, str], ...]
    if isinstance(node, RatioNodeV1):
        children = (("numerator", node.numerator_id), ("denominator", node.denominator_id))
    elif isinstance(node, WeightedAverageNodeV1):
        children = (("value", node.value_id), ("weight", node.weight_id))
    elif isinstance(node, LinearNodeV1):
        children = tuple((f"term{index}", term.child_id) for index, term in enumerate(node.terms))
    else:
        return None
    aligned, key_columns, _quality = align_metric_children_v1(
        tuple((role, execution.nodes[child_id].frame) for role, child_id in children)
    )
    component = aligned[list(key_columns)].copy() if key_columns else aligned.iloc[:, 0:0].copy()
    for role, _child_id in children:
        component[_role_to_column_name(metric_ir, role)] = aligned[f"__marivo_value_{role}"]
    root = execution.roots[root_index]
    component[metric_ir.name] = root.frame["value"].to_numpy(copy=True)
    return component


def component_graph_payload_v1(
    execution: MetricGraphExecutionV1,
    plan: MetricGraphObservePlanV1,
    *,
    coverage_refs: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the complete recursive component state persisted with a frame."""

    coverage_refs = coverage_refs or {}
    occurrence_paths: dict[str, list[str]] = {}
    for occurrence in plan.graph.occurrences:
        occurrence_paths.setdefault(occurrence.node_id, []).append(occurrence.path)
    leaf_lineage = {
        str(item["node_id"]): item
        for item in plan.lineage_metadata.get("physical_leaves", ())
        if isinstance(item, dict) and isinstance(item.get("node_id"), str)
    }
    node_by_id = {record.node_id: record.node for record in plan.graph.nodes}
    absorbed_by: dict[str, tuple[str, Any, Any]] = {}
    for leaf in plan.leaves:
        if not isinstance(leaf.plan, CumulativePhysicalLeafPlanV1) or not isinstance(
            leaf.node, CumulativeNodeV1
        ):
            continue
        parent_result = execution.nodes.get(leaf.node_id)
        if parent_result is not None:
            absorbed_by[leaf.node.child_id] = (
                leaf.node_id,
                leaf.plan.base_metric_ir,
                parent_result,
            )

    def child_roles(node: Any) -> tuple[tuple[str, str], ...]:
        if isinstance(node, SliceNodeV1):
            return (("child", node.child_id),)
        if isinstance(node, CumulativeNodeV1):
            return (("base", node.child_id),)
        if isinstance(node, RatioNodeV1):
            return (("numerator", node.numerator_id), ("denominator", node.denominator_id))
        if isinstance(node, WeightedAverageNodeV1):
            return (("value", node.value_id), ("weight", node.weight_id))
        if isinstance(node, LinearNodeV1):
            return tuple((f"term{index}", term.child_id) for index, term in enumerate(node.terms))
        return ()

    def evaluator_contract(node: Any) -> str:
        if isinstance(node, CumulativeNodeV1):
            return f"cumulative-evaluation/v{CUMULATIVE_CONTRACT_VERSION}"
        if isinstance(node, SliceNodeV1):
            return "slice-evaluation/v1"
        if isinstance(node, RatioNodeV1):
            return "ratio-evaluation/v1"
        if isinstance(node, WeightedAverageNodeV1):
            return "weighted-average-evaluation/v1"
        if isinstance(node, LinearNodeV1):
            return "linear-evaluation/v1"
        return "aggregate-evaluation/v1"

    def governed_lineage(node_id: str, active: set[str] | None = None) -> list[dict[str, Any]]:
        direct = leaf_lineage.get(node_id)
        if direct is not None:
            return [direct]
        absorbed = absorbed_by.get(node_id)
        if absorbed is not None:
            parent_lineage = leaf_lineage.get(absorbed[0])
            return [parent_lineage] if parent_lineage is not None else []
        active = set() if active is None else active
        if node_id in active:
            return []
        active.add(node_id)
        resolved: dict[str, dict[str, Any]] = {}
        for child_id in node_child_ids(node_by_id[node_id]):
            for item in governed_lineage(child_id, active):
                resolved[str(item["node_id"])] = item
        active.remove(node_id)
        return [resolved[key] for key in sorted(resolved)]

    records: list[dict[str, Any]] = []
    for record in plan.graph.nodes:
        result = execution.nodes.get(record.node_id)
        absorbed = absorbed_by.get(record.node_id)
        if result is not None:
            semantic_unit = result.unit
            semantic_unit_state = result.unit_state
            unit_capability_issue = result.unit_capability_issue
            semantic_additivity = result.additivity
            semantic_fold = result.fold
            semantic_shape = result.semantic_kind
            semantic_keys = list(result.key_columns)
            quality = canonical_value(result.quality)
            coverage_ref = coverage_refs.get(record.node_id)
        elif absorbed is not None:
            absorber_id, base_metric_ir, absorber_result = absorbed
            semantic_unit = getattr(base_metric_ir, "unit", None)
            semantic_unit_state = unit_state(semantic_unit)
            unit_capability_issue = None
            semantic_additivity = getattr(base_metric_ir, "additivity", None)
            time_fold = getattr(base_metric_ir, "time_fold", None)
            semantic_fold = time_fold.label() if time_fold is not None else None
            semantic_shape = absorber_result.semantic_kind
            semantic_keys = list(absorber_result.key_columns)
            quality = {
                **cast("dict[str, Any]", canonical_value(absorber_result.quality)),
                "materialization": "absorbed_by_parent",
                "absorbing_node_id": absorber_id,
            }
            coverage_ref = coverage_refs.get(absorber_id)
        else:
            raise ValueError(
                f"metric graph node {record.node_id!r} has no materialized or absorbed state"
            )
        ordered_children = child_roles(record.node)
        records.append(
            {
                "node_id": record.node_id,
                "node_fingerprint": record.node_id,
                "node_kind": record.node.kind,
                "evaluator_contract": evaluator_contract(record.node),
                "ordered_children": [
                    {"role": role, "node_id": child_id} for role, child_id in ordered_children
                ],
                "occurrence_paths": sorted(occurrence_paths.get(record.node_id, ())),
                "value_semantics": {
                    "unit": semantic_unit,
                    "unit_state": canonical_value(semantic_unit_state),
                    "unit_capability_issue": unit_capability_issue,
                    "additivity": semantic_additivity,
                    "fold": semantic_fold,
                    "semantic_shape": semantic_shape,
                    "key_columns": semantic_keys,
                },
                "quality": quality,
                "coverage_ref": coverage_ref,
                "governed_leaf_lineage": canonical_value(governed_lineage(record.node_id)),
            }
        )
    return {
        "schema": "metric-component-graph/v1",
        "root_node_ids": list(plan.graph.roots),
        "nodes": records,
        "presentation": canonical_value(plan.forest.presentation),
    }


__all__ = [
    "GraphNodeExecutionV1",
    "MetricGraphExecutionV1",
    "SemanticShapeV1",
    "component_graph_payload_v1",
    "execute_metric_graph_observe",
    "root_component_frame_v1",
]
