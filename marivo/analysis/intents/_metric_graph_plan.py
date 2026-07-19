"""Graph-native logical and physical planning for metric observation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.intents._observe_planner_base import plan_base_observe
from marivo.analysis.intents._observe_planner_types import (
    BaseObservePlan,
    CumulativePhysicalLeafPlanV1,
)
from marivo.analysis.intents._runtime_metric_lowering import lower_metric_inputs
from marivo.analysis.intents.observe_errors import (
    ObservePlanningError,
    raise_observe_planning_error,
)
from marivo.analysis.runtime_metric import RuntimeMetricExpr
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    SemanticCatalog,
    SemanticKind,
    SimpleMetricDetails,
)
from marivo.semantic.ir import (
    AggregateFoldInput,
    CumulativeComposition,
    SemiAdditive,
    TimeFoldIR,
    additivity_bucket,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CatalogBodyLeafV1,
    CumulativeNodeV1,
    DatasourceCompatibilityDomainV1,
    MetricExpressionGraphV1,
    MetricGraphNodeV1,
    SliceNodeV1,
    node_child_ids,
)
from marivo.semantic.metric_graph_canonical import canonical_value, fingerprint
from marivo.semantic.metric_graph_lowering import (
    MetricExpressionForestV1,
    lower_catalog_metric,
    lower_catalog_metrics,
)
from marivo.semantic.refs import MetricRef
from marivo.semantic.unit_algebra import tier1_unit

type PhysicalLeafPlanV1 = BaseObservePlan | CumulativePhysicalLeafPlanV1


@dataclass(frozen=True)
class ResolvedMetricLeafV1:
    """One physical graph node and the governed metric used to execute it."""

    node_id: str
    value_node_id: str
    occurrence_roles: tuple[str, ...]
    metric_id: str
    metric_ir: Any
    node: MetricGraphNodeV1
    plan: PhysicalLeafPlanV1


@dataclass(frozen=True)
class MetricGraphObservePlanV1:
    """A canonical forest plus one reusable plan per physical value node."""

    forest: MetricExpressionForestV1
    leaves: tuple[ResolvedMetricLeafV1, ...]
    datasource_name: str
    source_domain: DatasourceCompatibilityDomainV1
    lineage_metadata: dict[str, Any]
    warnings: tuple[dict[str, Any], ...]

    @property
    def graph(self) -> MetricExpressionGraphV1:
        return self.forest.graph


@dataclass(frozen=True)
class _RuntimeAggregateMetricAdapter:
    semantic_id: str
    name: str
    domain: str
    root_entity: str
    entities: tuple[str, ...]
    aggregation: Any
    measure: str
    additivity: str
    unit: str | None
    time_fold: TimeFoldIR | None
    status_time_dimension: str | None
    runtime_measure_id: str
    metric_type: str = "simple"
    composition: None = None
    fanout_policy: str = "block"


def _fold_ir(value: AggregateFoldInput) -> TimeFoldIR | None:
    if value is None:
        return None
    if isinstance(value, tuple):
        return TimeFoldIR(kind="percentile", q=value[1])
    return TimeFoldIR(kind=value)


def _runtime_metric_adapter(catalog: SemanticCatalog, node_id: str, node: AggregateNodeV1) -> Any:
    if node.target_kind != "measure":
        raise_observe_planning_error(
            code="runtime-metric-target-kind",
            message="Runtime aggregate nodes require a governed measure target.",
            candidates={"target_kind": node.target_kind, "target_id": node.target_id},
            repair=[],
        )
    registry = catalog._require_index().registry
    measure = registry.measures.get(node.target_id)
    if measure is None:
        raise_observe_planning_error(
            code="runtime-metric-measure-missing",
            message=f"Runtime aggregate measure {node.target_id!r} is not loaded.",
            candidates={"measure": node.target_id},
            repair=[],
        )
    inherited_fold = (
        measure.additivity.fold if isinstance(measure.additivity, SemiAdditive) else None
    )
    effective_fold = _fold_ir(node.fold) or inherited_fold
    return _RuntimeAggregateMetricAdapter(
        semantic_id=f"runtime.{node_id}",
        name="runtime_value",
        domain=measure.domain,
        root_entity=measure.entity,
        entities=(measure.entity,),
        aggregation=node.agg,
        measure=measure.semantic_id,
        additivity=additivity_bucket(measure.additivity),
        unit=node.unit_override
        or tier1_unit(node.agg[0] if isinstance(node.agg, tuple) else node.agg, measure.unit),
        time_fold=effective_fold,
        status_time_dimension=(
            measure.additivity.over if isinstance(measure.additivity, SemiAdditive) else None
        ),
        runtime_measure_id=measure.semantic_id,
    )


def _catalog_metric_adapter(catalog: SemanticCatalog, metric_id: str) -> Any:
    from marivo.analysis.intents._observe_planner_types import _planned_metric

    details = catalog.get(f"{SemanticKind.METRIC.value}.{metric_id}").details()
    if not isinstance(details, (SimpleMetricDetails, DerivedMetricDetails)):
        raise_observe_planning_error(
            code="metric-graph-metric-missing",
            message=f"Metric graph representative {metric_id!r} was not found.",
            candidates={"metric": metric_id},
            repair=[],
        )
    return _planned_metric(details)


def _representative_metrics(
    catalog: SemanticCatalog,
    graph: MetricExpressionGraphV1,
) -> dict[str, str]:
    """Resolve one governed catalog representative for every executable node.

    Node fingerprints deliberately exclude catalog wrapper identity.  Choosing
    a representative is therefore a physical planning concern only; equal
    aggregates use the same node and are executed once.
    """

    registry = catalog._require_index().registry
    node_by_id = {record.node_id: record.node for record in graph.nodes}
    executable_ids: set[str] = set()

    def visit(node_id: str) -> None:
        node = node_by_id[node_id]
        if isinstance(node, (CatalogBodyLeafV1, AggregateNodeV1, CumulativeNodeV1)):
            executable_ids.add(node_id)
            return
        if isinstance(node, SliceNodeV1):
            # Runtime planning replaces a sliced leaf with a scoped physical
            # leaf. Catalog lowering currently retains governed filters on the
            # aggregate node itself, so a catalog-only forest cannot reach this
            # branch.
            visit(node.child_id)
            return
        for child_id in node_child_ids(node):
            visit(child_id)

    for root_id in graph.roots:
        visit(root_id)
    representatives: dict[str, str] = {}
    for metric_id in sorted(registry.metrics):
        metric = registry.metrics[metric_id]
        if (
            metric.metric_type == "derived"
            and getattr(metric.composition, "kind", None) != "cumulative"
        ):
            continue
        lowered = lower_catalog_metric(registry, metric_id)
        root_id = lowered.graph.roots[0]
        if root_id in executable_ids:
            representatives.setdefault(root_id, metric_id)
    return representatives


def _canonical_slice_runtime_value(value: Any) -> Any:
    if isinstance(value, tuple):
        if (
            len(value) == 2
            and all(isinstance(item, tuple) and len(item) == 2 for item in value)
            and value[0][0] == "op"
            and value[1][0] == "value"
        ):
            return {
                "op": value[0][1],
                "value": _canonical_slice_runtime_value(value[1][1]),
            }
        return [_canonical_slice_runtime_value(item) for item in value]
    return value


def _physical_targets(
    graph: MetricExpressionGraphV1,
) -> dict[str, tuple[str, dict[str, Any]]]:
    """Map physical execution ids to underlying value nodes and local slices."""
    nodes = {record.node_id: record.node for record in graph.nodes}
    targets: dict[str, tuple[str, dict[str, Any]]] = {}

    def visit(node_id: str) -> None:
        node = nodes[node_id]
        if isinstance(node, (CatalogBodyLeafV1, AggregateNodeV1, CumulativeNodeV1)):
            targets.setdefault(node_id, (node_id, {}))
            return
        if isinstance(node, SliceNodeV1):
            child = nodes[node.child_id]
            if not isinstance(child, (CatalogBodyLeafV1, AggregateNodeV1, CumulativeNodeV1)):
                raise_observe_planning_error(
                    code="metric-graph-slice-not-leaf",
                    message="Canonical metric slices must be attached directly to physical leaves.",
                    candidates={"node_id": node_id, "child_id": node.child_id},
                    repair=[],
                )
            targets.setdefault(
                node_id,
                (
                    node.child_id,
                    {
                        dimension_id: _canonical_slice_runtime_value(value)
                        for dimension_id, value in node.predicates
                    },
                ),
            )
            return
        for child_id in node_child_ids(node):
            visit(child_id)

    for root_id in graph.roots:
        visit(root_id)
    return targets


def _merge_leaf_where(
    global_where: dict[Any, Any] | None, local_where: dict[str, Any]
) -> dict[Any, Any]:
    merged = dict(global_where or {})
    for dimension_id, value in local_where.items():
        if dimension_id in merged and canonical_value(merged[dimension_id]) != canonical_value(
            value
        ):
            raise_observe_planning_error(
                code="metric-graph-slice-conflict",
                message=f"Global and branch-local slices conflict for {dimension_id!r}.",
                candidates={"dimension": dimension_id},
                repair=[],
            )
        merged[dimension_id] = value
    return merged


def _input_id(value: Any) -> str:
    return str(getattr(value, "id", value))


def _base_plan(leaf: ResolvedMetricLeafV1) -> BaseObservePlan:
    return leaf.plan.base_plan if isinstance(leaf.plan, CumulativePhysicalLeafPlanV1) else leaf.plan


def _raise_unreachable_scope(
    failures: list[tuple[str, ObservePlanningError]],
    leaves: list[ResolvedMetricLeafV1],
    *,
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
) -> None:
    """Turn per-leaf path failures into one graph-level scope blocker."""

    missing_components = [metric_id for metric_id, _exc in failures]
    if dimensions:
        dimension_id = _input_id(dimensions[0])
        column = dimension_id.rsplit(".", 1)[-1]
        resolved_components = [
            {
                "metric": leaf.metric_id,
                "resolved_field_id": planned.field.semantic_id,
            }
            for leaf in leaves
            for planned in _base_plan(leaf).dimensions
            if planned.column == column
        ]
        raise_observe_planning_error(
            code="component-axis-unreachable",
            message=f"Observation dimension {dimension_id!r} cannot be resolved by every leaf.",
            candidates={
                "dimension": dimension_id,
                "missing_components": missing_components,
                "resolved_components": resolved_components,
            },
            repair=[],
        )
    if where:
        raw_key = next(iter(where))
        filter_key = _input_id(raw_key)
        resolved_components = [
            {
                "metric": leaf.metric_id,
                "resolved_field_id": planned.field.semantic_id,
            }
            for leaf in leaves
            for planned in _base_plan(leaf).where
            if planned.original_key == filter_key
        ]
        raise_observe_planning_error(
            code="component-filter-unreachable",
            message=f"Observation filter {filter_key!r} cannot be applied by every leaf.",
            candidates={
                "filter_key": filter_key,
                "missing_components": missing_components,
                "resolved_components": resolved_components,
            },
            repair=[],
        )
    raise failures[0][1]


def _validate_leaf_comparability(
    leaves: list[ResolvedMetricLeafV1],
    *,
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
) -> None:
    """Validate the shared axes, filters, and snapshot resolution of all leaves."""

    for raw_dimension in dimensions or ():
        dimension_id = _input_id(raw_dimension)
        column = dimension_id.rsplit(".", 1)[-1]
        resolved = [
            (leaf.metric_id, planned.field.semantic_id)
            for leaf in leaves
            for planned in _base_plan(leaf).dimensions
            if planned.column == column
        ]
        field_ids = {field_id for _metric_id, field_id in resolved}
        if len(field_ids) > 1:
            raise_observe_planning_error(
                code="component-axis-field-mismatch",
                message=f"Dimension {dimension_id!r} resolves differently across leaves.",
                candidates={
                    "dimension": dimension_id,
                    "components": [
                        {"metric": metric_id, "resolved_field_id": field_id}
                        for metric_id, field_id in resolved
                    ],
                },
                repair=[],
            )
    for raw_key in where or ():
        filter_key = _input_id(raw_key)
        resolved = [
            (leaf.metric_id, planned.field.semantic_id)
            for leaf in leaves
            for planned in _base_plan(leaf).where
            if planned.original_key == filter_key
        ]
        field_ids = {field_id for _metric_id, field_id in resolved}
        if len(field_ids) > 1:
            raise_observe_planning_error(
                code="component-filter-field-mismatch",
                message=f"Filter {filter_key!r} resolves differently across leaves.",
                candidates={
                    "filter_key": filter_key,
                    "components": [
                        {"metric": metric_id, "resolved_field_id": field_id}
                        for metric_id, field_id in resolved
                    ],
                },
                repair=[],
            )
    by_dataset: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for leaf in leaves:
        for resolution in _base_plan(leaf).lineage_metadata.get("version_resolutions", []):
            by_dataset.setdefault(resolution["dataset"], []).append((leaf.metric_id, resolution))
    for dataset_id, entries in by_dataset.items():
        if len(entries) < 2:
            continue
        keys = {
            (
                value["mode"],
                value["anchor_source"],
                value.get("anchor_value"),
                value.get("resolved_partition"),
                value.get("resolved_interval_predicate"),
                value.get("anchor_to_partition_mapping_digest"),
            )
            for _metric_id, value in entries
        }
        if len(keys) > 1:
            raise_observe_planning_error(
                code="component-version-mismatch",
                message=f"Versioned dataset {dataset_id!r} differs across leaves.",
                candidates={
                    "versioned_dataset": dataset_id,
                    "components": [
                        {
                            "metric": metric_id,
                            "mode": value["mode"],
                            "anchor_source": value["anchor_source"],
                            "anchor_value": value.get("anchor_value"),
                            "resolved_partition_or_predicate": (
                                value.get("resolved_partition")
                                or value.get("resolved_interval_predicate")
                            ),
                            "mapping_digest": value.get("anchor_to_partition_mapping_digest"),
                        }
                        for metric_id, value in entries
                    ],
                },
                repair=[],
            )


def _is_cumulative_ir(metric_ir: Any) -> bool:
    return getattr(getattr(metric_ir, "composition", None), "kind", None) == "cumulative"


def _plan_cumulative_physical_leaf(
    *,
    catalog: SemanticCatalog,
    session: Any,
    metric_id: str,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_dimension: str | None,
) -> CumulativePhysicalLeafPlanV1:
    registry = catalog._require_index().registry
    composition = registry.metrics[metric_id].composition
    if not isinstance(composition, CumulativeComposition):
        raise AssertionError(f"metric {metric_id!r} is not cumulative")
    base_ir = _catalog_metric_adapter(catalog, composition.base)
    base_plan = plan_base_observe(
        catalog=catalog,
        session=session,
        metric_ir=base_ir,
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=dimensions,
        where=where,
        resolved_window=resolved_window,
        time_dimension=composition.over or time_dimension,
        allow_unqualified_outside_scope=True,
    )
    return CumulativePhysicalLeafPlanV1(
        metric_ir=metric_ir,
        base_metric_ir=base_ir,
        base_plan=base_plan,
        over=composition.over,
        window=resolved_window,
        composition=composition,
    )


def _plan_metric_expression_forest(
    *,
    catalog: SemanticCatalog,
    session: Any,
    forest: MetricExpressionForestV1,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_dimension: str | None,
) -> MetricGraphObservePlanV1:
    """Recursively plan one already-lowered expression forest."""

    registry = catalog._require_index().registry
    node_by_id = {record.node_id: record.node for record in forest.graph.nodes}
    representatives = _representative_metrics(catalog, forest.graph)
    physical_targets = _physical_targets(forest.graph)
    occurrence_roles: dict[str, list[str]] = {}
    for occurrence in forest.graph.occurrences:
        occurrence_roles.setdefault(occurrence.node_id, []).append(occurrence.path)

    metric_adapters: dict[str, Any] = {
        metric_id: _catalog_metric_adapter(catalog, metric_id)
        for metric_id in sorted(set(representatives.values()))
    }
    leaves: list[ResolvedMetricLeafV1] = []
    datasource_names: set[str] = set()
    warnings: list[dict[str, Any]] = []
    lineage_leaves: list[dict[str, Any]] = []
    scope_failures: list[tuple[str, ObservePlanningError]] = []
    for node_id, (value_node_id, local_where) in sorted(physical_targets.items()):
        value_node = node_by_id[value_node_id]
        metric_id = representatives.get(value_node_id)
        if metric_id is not None:
            metric_ir = metric_adapters[metric_id]
        elif isinstance(value_node, AggregateNodeV1):
            metric_ir = _runtime_metric_adapter(catalog, value_node_id, value_node)
            metric_id = metric_ir.semantic_id
        else:
            raise_observe_planning_error(
                code="metric-graph-physical-leaf-missing",
                message="Metric graph contains a physical node without a governed execution contract.",
                candidates={"node_id": value_node_id, "node_kind": value_node.kind},
                repair=[],
            )
        leaf_where = _merge_leaf_where(where, local_where)
        physical_plan: PhysicalLeafPlanV1
        base_plan: BaseObservePlan
        try:
            if _is_cumulative_ir(metric_ir):
                physical_plan = _plan_cumulative_physical_leaf(
                    catalog=catalog,
                    session=session,
                    metric_id=metric_id,
                    metric_ir=metric_ir,
                    dataset_irs=dataset_irs,
                    dataset_fns=dataset_fns,
                    dimensions=dimensions,
                    where=leaf_where,
                    resolved_window=resolved_window,
                    time_dimension=time_dimension,
                )
                base_plan = physical_plan.base_plan
            else:
                physical_plan = plan_base_observe(
                    catalog=catalog,
                    session=session,
                    metric_ir=metric_ir,
                    dataset_irs=dataset_irs,
                    dataset_fns=dataset_fns,
                    dimensions=dimensions,
                    where=leaf_where,
                    resolved_window=resolved_window,
                    time_dimension=time_dimension,
                    allow_unqualified_outside_scope=True,
                )
                base_plan = physical_plan
        except WindowInvalidError as exc:
            if "has no @ms.time_dimension" not in (exc.message or ""):
                raise
            if _is_cumulative_ir(metric_ir):
                physical_plan = _plan_cumulative_physical_leaf(
                    catalog=catalog,
                    session=session,
                    metric_id=metric_id,
                    metric_ir=metric_ir,
                    dataset_irs=dataset_irs,
                    dataset_fns=dataset_fns,
                    dimensions=dimensions,
                    where=leaf_where,
                    resolved_window=None,
                    time_dimension=time_dimension,
                )
                base_plan = physical_plan.base_plan
            else:
                physical_plan = plan_base_observe(
                    catalog=catalog,
                    session=session,
                    metric_ir=metric_ir,
                    dataset_irs=dataset_irs,
                    dataset_fns=dataset_fns,
                    dimensions=dimensions,
                    where=leaf_where,
                    resolved_window=None,
                    time_dimension=time_dimension,
                    allow_unqualified_outside_scope=True,
                )
                base_plan = physical_plan
        except ObservePlanningError as exc:
            details = exc._context if isinstance(exc._context, dict) else {}
            if details.get("code") in {
                "field-ref-not-found",
                "field-ref-ambiguous",
                "path-missing",
                "path-ambiguous",
            }:
                scope_failures.append((metric_id, exc))
                continue
            raise
        datasource_names.add(base_plan.datasource_name)
        warnings.extend(base_plan.warnings)
        roles = tuple(sorted(occurrence_roles.get(node_id, ())))
        leaves.append(
            ResolvedMetricLeafV1(
                node_id=node_id,
                value_node_id=value_node_id,
                occurrence_roles=roles,
                metric_id=metric_id,
                metric_ir=metric_ir,
                node=node_by_id[node_id],
                plan=physical_plan,
            )
        )
        lineage_leaves.append(
            {
                "node_id": node_id,
                "metric_id": metric_id,
                "occurrence_roles": list(roles),
                "datasource": base_plan.datasource_name,
                "lineage_metadata": base_plan.lineage_metadata,
            }
        )
    if scope_failures:
        _raise_unreachable_scope(
            scope_failures,
            leaves,
            dimensions=dimensions,
            where=where,
        )
    _validate_leaf_comparability(leaves, dimensions=dimensions, where=where)
    if len(datasource_names) != 1:
        raise_observe_planning_error(
            code="metric-graph-source-domain-mismatch",
            message="A metric expression graph must resolve to one datasource compatibility domain.",
            candidates={"datasources": sorted(datasource_names)},
            repair=[],
        )
    datasource_name = next(iter(datasource_names))
    datasource_ir = registry.datasources.get(datasource_name)
    if datasource_ir is None:
        datasource_ir = next(
            (
                candidate
                for candidate_id, candidate in registry.datasources.items()
                if candidate_id.rsplit(".", 1)[-1] == datasource_name
            ),
            None,
        )
    if datasource_ir is None:
        raise_observe_planning_error(
            code="metric-graph-source-domain-mismatch",
            message=f"Resolved datasource {datasource_name!r} has no semantic declaration.",
            candidates={"datasource": datasource_name},
            repair=[],
        )
    source_domain = DatasourceCompatibilityDomainV1(
        schema="datasource-compatibility/v1",
        datasource_id=datasource_name,
        backend_type=datasource_ir.backend_type,
        profile_fingerprint=fingerprint(
            (
                datasource_name,
                datasource_ir.backend_type,
                datasource_ir.fields,
                datasource_ir.env_refs,
            )
        ),
    )
    return MetricGraphObservePlanV1(
        forest=forest,
        leaves=tuple(leaves),
        datasource_name=datasource_name,
        source_domain=source_domain,
        lineage_metadata={
            "metric_graph_schema": forest.graph.schema,
            "root_node_ids": list(forest.graph.roots),
            "dependency_fingerprint": forest.dependency_digest.fingerprint,
            "physical_leaves": lineage_leaves,
        },
        warnings=tuple(warnings),
    )


def plan_metric_graph_observe(
    *,
    catalog: SemanticCatalog,
    session: Any,
    metric_inputs: tuple[MetricRef | RuntimeMetricExpr, ...],
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_dimension: str | None,
) -> MetricGraphObservePlanV1:
    """Lower and plan one ordered catalog/runtime expression forest."""
    forest = lower_metric_inputs(catalog._require_index().registry, metric_inputs)
    return _plan_metric_expression_forest(
        catalog=catalog,
        session=session,
        forest=forest,
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=dimensions,
        where=where,
        resolved_window=resolved_window,
        time_dimension=time_dimension,
    )


def plan_catalog_metric_graph_observe(
    *,
    catalog: SemanticCatalog,
    session: Any,
    metric_ids: tuple[str, ...],
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_dimension: str | None,
) -> MetricGraphObservePlanV1:
    """Plan one ordered catalog forest through the unified graph planner."""
    forest = lower_catalog_metrics(catalog._require_index().registry, metric_ids)
    return _plan_metric_expression_forest(
        catalog=catalog,
        session=session,
        forest=forest,
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=dimensions,
        where=where,
        resolved_window=resolved_window,
        time_dimension=time_dimension,
    )


__all__ = [
    "MetricGraphObservePlanV1",
    "PhysicalLeafPlanV1",
    "ResolvedMetricLeafV1",
    "plan_catalog_metric_graph_observe",
    "plan_metric_graph_observe",
]
