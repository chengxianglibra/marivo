"""Graph-native observe planner entry point and stable planner helpers."""

from __future__ import annotations

from typing import Any

from marivo.analysis.intents._metric_graph_plan import (
    MetricGraphObservePlanV1,
    plan_catalog_metric_graph_observe,
)
from marivo.analysis.intents._observe_planner_base import plan_base_observe
from marivo.analysis.intents._observe_planner_catalog import resolve_metric_root
from marivo.analysis.intents._observe_planner_fields import (
    _effective_key,
    resolve_observe_fields,
    resolved_edge_safety,
    unique_shortest_relationship_path,
)
from marivo.analysis.intents._observe_planner_joins import (
    _field_fn,
    _validate_field_expr,
)
from marivo.analysis.intents._observe_planner_types import (
    BaseObservePlan,
    JoinSafety,
    _is_cumulative_metric,
    _planned_metric,
)
from marivo.analysis.intents._observe_planner_versioning import _derive_version_mode
from marivo.semantic.catalog import SemanticCatalog

__all__ = [
    "BaseObservePlan",
    "JoinSafety",
    "MetricGraphObservePlanV1",
    "_derive_version_mode",
    "_effective_key",
    "_field_fn",
    "_is_cumulative_metric",
    "_planned_metric",
    "_validate_field_expr",
    "plan_base_observe",
    "plan_observe",
    "resolve_metric_root",
    "resolve_observe_fields",
    "resolved_edge_safety",
    "unique_shortest_relationship_path",
]


def plan_observe(
    *,
    catalog: SemanticCatalog | None = None,
    session: Any,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: Any,
    where: Any,
    resolved_window: Any,
    time_dimension: Any,
) -> BaseObservePlan | MetricGraphObservePlanV1:
    """Plan a simple physical leaf or a recursive catalog metric graph."""

    if catalog is None:
        catalog = session.catalog
    if metric_ir.metric_type != "derived":
        return plan_base_observe(
            catalog=catalog,
            session=session,
            metric_ir=metric_ir,
            dataset_irs=dataset_irs,
            dataset_fns=dataset_fns,
            dimensions=dimensions,
            where=where,
            resolved_window=resolved_window,
            time_dimension=time_dimension,
        )
    return plan_catalog_metric_graph_observe(
        catalog=catalog,
        session=session,
        metric_ids=(metric_ir.semantic_id,),
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=dimensions,
        where=where,
        resolved_window=resolved_window,
        time_dimension=time_dimension,
    )
