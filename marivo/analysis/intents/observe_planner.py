"""Phase 1 observe planner dispatch.

The planner is split across private submodules (types, catalog, fields,
versioning, joins, base, comparability). This shell retains the public
``plan_observe`` dispatcher and re-exports the symbols that ``observe`` /
``observe_multi`` / tests import from ``marivo.analysis.intents.observe_planner``.
``__all__`` also satisfies mypy's ``no_implicit_reexport``.
"""

from __future__ import annotations

from typing import Any

from marivo.analysis.intents._observe_planner_base import plan_base_observe
from marivo.analysis.intents._observe_planner_catalog import resolve_metric_root
from marivo.analysis.intents._observe_planner_comparability import (
    _plan_cumulative_observe,
    _plan_derived_observe,
)
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
    ComponentPlan,
    CumulativeObservePlan,
    DerivedObservePlan,
    JoinSafety,
    ObservePlan,
    _is_cumulative_metric,
    _planned_metric,
)
from marivo.analysis.intents._observe_planner_versioning import _derive_version_mode
from marivo.semantic.catalog import SemanticCatalog

__all__ = [
    "BaseObservePlan",
    "ComponentPlan",
    "CumulativeObservePlan",
    "DerivedObservePlan",
    "JoinSafety",
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
    component_metric_irs: dict[str, Any] | None = None,
) -> ObservePlan:
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
    if _is_cumulative_metric(metric_ir):
        return _plan_cumulative_observe(
            catalog=catalog,
            session=session,
            metric_ir=metric_ir,
            dataset_irs=dataset_irs,
            dataset_fns=dataset_fns,
            dimensions=dimensions,
            where=where,
            resolved_window=resolved_window,
            time_dimension=time_dimension,
            component_metric_irs=component_metric_irs,
        )
    return _plan_derived_observe(
        catalog=catalog,
        session=session,
        metric_ir=metric_ir,
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=dimensions,
        where=where,
        resolved_window=resolved_window,
        time_dimension=time_dimension,
        component_metric_irs=component_metric_irs,
    )
