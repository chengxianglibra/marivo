"""Derived and cumulative observe planning with cross-component comparability.

Internal to ``marivo.analysis.intents`` — extracted from ``observe_planner``.
"""

from __future__ import annotations

from typing import Any

from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.intents._observe_planner_base import plan_base_observe
from marivo.analysis.intents._observe_planner_catalog import (
    _input_ref_id,
    _metric,
    _ref_id,
)
from marivo.analysis.intents._observe_planner_types import (
    BaseObservePlan,
    ComponentPlan,
    CumulativeObservePlan,
    DerivedObservePlan,
    PlannedWhere,
    _is_cumulative_metric,
    _planned_metric,
)
from marivo.analysis.intents.observe_errors import (
    ObservePlanningError,
    raise_observe_planning_error,
)
from marivo.semantic.catalog import SemanticCatalog


def _component_dataset_adapters(
    component_ir: Any,
    parent_dataset_irs: dict[str, Any],
    parent_dataset_fns: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-adapt the component's own datasets, reusing parent adapters when they already exist.

    Returns a dataset_irs/fns map that includes ALL parent datasets so the planner
    can resolve qualified dimension/filter refs that land on datasets outside the
    component's own dataset list.  The component's own datasets are always included;
    parent datasets are merged in so relationship-path resolution can proceed.
    """
    component_dataset_irs: dict[str, Any] = dict(parent_dataset_irs)
    component_dataset_fns: dict[str, Any] = dict(parent_dataset_fns)
    for entity_id in component_ir.entities:
        if entity_id not in component_dataset_irs:
            raise_observe_planning_error(
                code="derived-shared-planner-unsupported",
                message=(
                    f"entity {entity_id!r} adapter not provided for component metric "
                    f"{component_ir.semantic_id!r}"
                ),
                candidates={"entity": entity_id},
                repair=[],
            )
        if entity_id not in component_dataset_fns:
            component_dataset_fns[entity_id] = component_dataset_irs[entity_id].fn
    return component_dataset_irs, component_dataset_fns


def _accumulate_unreachable_ref(
    exc: ObservePlanningError,
    component_id: str,
    *,
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    axes_acc: dict[str, list[str]],
    where_acc: dict[str, list[str]],
) -> None:
    """Classify a field-ref-not-found/ambiguous error as a missing axis or missing filter."""
    msg = exc.message or ""
    for dim in dimensions or []:
        dim_id = _input_ref_id(dim)
        if f"{dim_id!r}" in msg:
            axes_acc.setdefault(dim_id, []).append(component_id)
            return
    for raw_key in where or {}:
        key = _input_ref_id(raw_key)
        if f"{key!r}" in msg:
            where_acc.setdefault(key, []).append(component_id)
            return
    raise exc


def _accumulate_path_unreachable(
    exc: ObservePlanningError,
    component_id: str,
    *,
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    axes_acc: dict[str, list[str]],
    where_acc: dict[str, list[str]],
) -> None:
    """Classify a path-missing/path-ambiguous error as a missing axis or missing filter.

    When a component cannot reach a dimension's dataset via its relationship graph,
    we attribute the failure to the first dimension or filter whose dataset matches
    the unreachable target.  If no match is found, re-raise.
    """
    details = exc.details or {}
    candidates = details.get("candidates", {}) if isinstance(details, dict) else {}
    to_dataset = candidates.get("to_dataset") if isinstance(candidates, dict) else None
    # Try to match to a dimension
    for dim in dimensions or []:
        dim_id = _input_ref_id(dim)
        # The dimension id may be qualified (e.g. 'sales.country') or unqualified.
        # We match on the local name part.
        local_name = dim_id.rsplit(".", 1)[-1]
        if to_dataset is not None and local_name in to_dataset:
            axes_acc.setdefault(dim_id, []).append(component_id)
            return
    # Fallback: attribute to the first dimension if any
    for dim in dimensions or []:
        axes_acc.setdefault(_input_ref_id(dim), []).append(component_id)
        return
    # Try to match to a where filter
    for raw_key in where or {}:
        key = _input_ref_id(raw_key)
        where_acc.setdefault(key, []).append(component_id)
        return
    raise exc


def _raise_component_axis_unreachable(
    missing_map: dict[str, list[str]],
    component_plans: list[ComponentPlan],
    parent_dimensions: list[Any] | None,
) -> None:
    dim_id, components_missing = next(iter(missing_map.items()))
    resolved = []
    target = next((p for p in (parent_dimensions or []) if _input_ref_id(p) == dim_id), None)
    for cp in component_plans:
        for d in cp.base_plan.dimensions:
            if target is not None and d.column == _input_ref_id(target).rsplit(".", 1)[-1]:
                resolved.append(
                    {
                        "metric": cp.component_metric_ir.semantic_id,
                        "resolved_field_id": d.field.semantic_id,
                    }
                )
    raise_observe_planning_error(
        code="component-axis-unreachable",
        message=f"Parent dimension {dim_id!r} cannot be resolved by every component.",
        candidates={
            "dimension": dim_id,
            "missing_components": components_missing,
            "resolved_components": resolved,
        },
        repair=[],
    )


def _raise_component_filter_unreachable(
    missing_map: dict[str, list[str]],
    component_plans: list[ComponentPlan],
    parent_where: dict[Any, Any] | None,
) -> None:
    key, components_missing = next(iter(missing_map.items()))
    resolved = []
    for cp in component_plans:
        for pw in cp.base_plan.where:
            if pw.original_key == key:
                resolved.append(
                    {
                        "metric": cp.component_metric_ir.semantic_id,
                        "resolved_field_id": pw.field.semantic_id,
                    }
                )
    raise_observe_planning_error(
        code="component-filter-unreachable",
        message=f"Parent filter {key!r} cannot be applied by every component.",
        candidates={
            "filter_key": key,
            "missing_components": components_missing,
            "resolved_components": resolved,
        },
        repair=[],
    )


def _check_axis_comparability(
    component_plans: list[ComponentPlan],
    parent_dimensions: list[Any] | None,
) -> None:
    for dim in parent_dimensions or []:
        dim_id = _input_ref_id(dim)
        col = dim_id.rsplit(".", 1)[-1]
        per_component: dict[str, list[Any]] = {
            cp.component_metric_ir.semantic_id: [
                d.field for d in cp.base_plan.dimensions if d.column == col
            ]
            for cp in component_plans
        }
        ids = {fields[0].semantic_id for fields in per_component.values() if fields}
        if len(ids) > 1:
            raise_observe_planning_error(
                code="component-axis-field-mismatch",
                message=f"Dimension {dim_id!r} resolves to different field ids across components.",
                candidates={
                    "dimension": dim_id,
                    "components": [
                        {"metric": cid, "resolved_field_id": fields[0].semantic_id}
                        for cid, fields in per_component.items()
                        if fields
                    ],
                },
                repair=[],
            )


def _check_filter_comparability(
    component_plans: list[ComponentPlan],
    parent_where: dict[Any, Any] | None,
) -> None:
    for raw_key in parent_where or {}:
        key = _input_ref_id(raw_key)
        applied: dict[str, list[PlannedWhere]] = {
            cp.component_metric_ir.semantic_id: [
                pw for pw in cp.base_plan.where if pw.original_key == key
            ]
            for cp in component_plans
        }
        field_ids = {applied[cid][0].field.semantic_id for cid in applied if applied[cid]}
        if len(field_ids) > 1:
            raise_observe_planning_error(
                code="component-filter-field-mismatch",
                message=f"Filter {key!r} resolves to different field ids across components.",
                candidates={
                    "filter_key": key,
                    "components": [
                        {"metric": cid, "resolved_field_id": pws[0].field.semantic_id}
                        for cid, pws in applied.items()
                        if pws
                    ],
                },
                repair=[],
            )


def _check_version_comparability(component_plans: list[ComponentPlan]) -> None:
    by_dataset: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for cp in component_plans:
        for vmeta in cp.base_plan.lineage_metadata.get("version_resolutions", []):
            by_dataset.setdefault(vmeta["dataset"], []).append(
                (cp.component_metric_ir.semantic_id, vmeta)
            )
    for dataset_id, entries in by_dataset.items():
        if len(entries) < 2:
            continue
        keys = {
            (
                v["mode"],
                v["anchor_source"],
                v.get("anchor_value"),
                v.get("resolved_partition"),
                v.get("resolved_interval_predicate"),
                v.get("anchor_to_partition_mapping_digest"),
            )
            for _cid, v in entries
        }
        if len(keys) > 1:
            raise_observe_planning_error(
                code="component-version-mismatch",
                message=f"Versioned dataset {dataset_id!r} differs across components.",
                candidates={
                    "versioned_dataset": dataset_id,
                    "components": [
                        {
                            "metric": cid,
                            "mode": v["mode"],
                            "anchor_source": v["anchor_source"],
                            "anchor_value": v.get("anchor_value"),
                            "resolved_partition_or_predicate": (
                                v.get("resolved_partition") or v.get("resolved_interval_predicate")
                            ),
                            "mapping_digest": v.get("anchor_to_partition_mapping_digest"),
                        }
                        for cid, v in entries
                    ],
                },
                repair=[],
            )


def _plan_cumulative_observe(
    *,
    catalog: SemanticCatalog,
    session: Any,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_dimension: Any,
    component_metric_irs: dict[str, Any] | None,
) -> CumulativeObservePlan:
    """Plan a cumulative observe by delegating to the base metric's plan_base_observe.

    The cumulative metric's composition carries ``base`` (the metric to
    accumulate) and ``over`` (the time axis to accumulate along).  The base
    metric is planned via ``plan_base_observe`` using the cumulative's
    ``over`` as the time dimension when available.
    """
    component = metric_ir.composition
    base_ref = _ref_id(component.base)
    base_details = _metric(catalog, base_ref)
    base_ir = component_metric_irs.get(base_ref) if component_metric_irs is not None else None
    if base_ir is None:
        base_ir = _planned_metric(base_details)
    base_dataset_irs, base_dataset_fns = _component_dataset_adapters(
        base_ir,
        dataset_irs,
        dataset_fns,
    )
    # Use the cumulative's over axis as the time dimension for the base plan
    # when it is available; fall back to the caller-supplied time_dimension.
    cumulative_over = getattr(component, "over", None)
    # Resolve the real CumulativeComposition (carrying the anchor) from the
    # registry. metric_ir.composition here is the _MetricDetailsAdapter
    # composition, which defaults over=None and anchor='all_history'; the real
    # IR with the resolved anchor lives on the registry.
    resolved_composition = component
    registry = catalog._require_index().registry
    real_ir = registry.metrics.get(metric_ir.semantic_id)
    if real_ir is not None and real_ir.composition is not None:
        resolved_composition = real_ir.composition
        if cumulative_over is None:
            cumulative_over = getattr(resolved_composition, "over", None)
    base_time_dimension = cumulative_over or time_dimension
    base_plan = plan_base_observe(
        catalog=catalog,
        session=session,
        metric_ir=base_ir,
        dataset_irs=base_dataset_irs,
        dataset_fns=base_dataset_fns,
        dimensions=dimensions,
        where=where,
        resolved_window=resolved_window,
        time_dimension=base_time_dimension,
        allow_unqualified_outside_scope=True,
    )
    return CumulativeObservePlan(
        metric_ir=metric_ir,
        base_metric_ir=base_ir,
        base_plan=base_plan,
        over=cumulative_over,
        window=resolved_window,
        composition=resolved_composition,
    )


def _plan_derived_observe(
    *,
    catalog: SemanticCatalog,
    session: Any,
    metric_ir: Any,
    dataset_irs: dict[str, Any],
    dataset_fns: dict[str, Any],
    dimensions: list[Any] | None,
    where: dict[Any, Any] | None,
    resolved_window: Any | None,
    time_dimension: Any,
    component_metric_irs: dict[str, Any] | None,
) -> DerivedObservePlan:
    component_plans: list[ComponentPlan] = []
    component_unreachable_axes: dict[str, list[str]] = {}
    component_unreachable_where: dict[str, list[str]] = {}

    for role, component_id in metric_ir.composition.components.items():
        component_ref = _ref_id(component_id)
        component_details = _metric(catalog, component_ref)
        component_ir = (
            component_metric_irs.get(component_ref) if component_metric_irs is not None else None
        )
        if component_ir is None:
            component_ir = _planned_metric(component_details)
        if component_ir.metric_type == "derived" and not _is_cumulative_metric(component_ir):
            raise_observe_planning_error(
                code="nested-derived-unsupported",
                message=(
                    f"component metric {component_ref!r} is itself derived; "
                    "nested derived is unsupported."
                ),
                candidates={"metric": component_ref},
                repair=[],
            )
        component_dataset_irs, component_dataset_fns = _component_dataset_adapters(
            component_ir,
            dataset_irs,
            dataset_fns,
        )
        # When a component metric has a status_time_dimension, use it as the
        # time dimension for planning so the planner resolves the correct
        # time axis when the entity has multiple time dimensions.
        component_time_dimension = time_dimension
        if (
            getattr(component_ir, "status_time_dimension", None) is not None
            and component_time_dimension is None
        ):
            component_time_dimension = component_ir.status_time_dimension
        try:
            if _is_cumulative_metric(component_ir):
                base_plan: BaseObservePlan | CumulativeObservePlan = _plan_cumulative_observe(
                    catalog=catalog,
                    session=session,
                    metric_ir=component_ir,
                    dataset_irs=component_dataset_irs,
                    dataset_fns=component_dataset_fns,
                    dimensions=dimensions,
                    where=where,
                    resolved_window=resolved_window,
                    time_dimension=component_time_dimension,
                    component_metric_irs=component_metric_irs,
                )
            else:
                base_plan = plan_base_observe(
                    catalog=catalog,
                    session=session,
                    metric_ir=component_ir,
                    dataset_irs=component_dataset_irs,
                    dataset_fns=component_dataset_fns,
                    dimensions=dimensions,
                    where=where,
                    resolved_window=resolved_window,
                    time_dimension=component_time_dimension,
                    allow_unqualified_outside_scope=True,
                )
        except WindowInvalidError as _win_exc:
            # Component root has no time field; skip window for this component.
            if "has no @ms.time_dimension" not in (_win_exc.message or ""):
                raise
            if _is_cumulative_metric(component_ir):
                base_plan = _plan_cumulative_observe(
                    catalog=catalog,
                    session=session,
                    metric_ir=component_ir,
                    dataset_irs=component_dataset_irs,
                    dataset_fns=component_dataset_fns,
                    dimensions=dimensions,
                    where=where,
                    resolved_window=None,
                    time_dimension=component_time_dimension,
                    component_metric_irs=component_metric_irs,
                )
            else:
                base_plan = plan_base_observe(
                    catalog=catalog,
                    session=session,
                    metric_ir=component_ir,
                    dataset_irs=component_dataset_irs,
                    dataset_fns=component_dataset_fns,
                    dimensions=dimensions,
                    where=where,
                    resolved_window=None,
                    time_dimension=component_time_dimension,
                    allow_unqualified_outside_scope=True,
                )
        except ObservePlanningError as exc:
            details = exc.details
            code = details.get("code") if isinstance(details, dict) else None
            if code in ("field-ref-not-found", "field-ref-ambiguous"):
                _accumulate_unreachable_ref(
                    exc,
                    component_id,
                    dimensions=dimensions,
                    where=where,
                    axes_acc=component_unreachable_axes,
                    where_acc=component_unreachable_where,
                )
                continue
            if code in ("path-missing", "path-ambiguous"):
                # The component cannot reach a dimension or filter dataset via
                # its relationship graph.  Classify as unreachable axis/filter.
                _accumulate_path_unreachable(
                    exc,
                    component_id,
                    dimensions=dimensions,
                    where=where,
                    axes_acc=component_unreachable_axes,
                    where_acc=component_unreachable_where,
                )
                continue
            raise
        component_plans.append(
            ComponentPlan(component_metric_ir=component_ir, role=role, base_plan=base_plan)
        )

    if component_unreachable_axes:
        _raise_component_axis_unreachable(component_unreachable_axes, component_plans, dimensions)
    if component_unreachable_where:
        _raise_component_filter_unreachable(component_unreachable_where, component_plans, where)

    _check_axis_comparability(component_plans, dimensions)
    _check_filter_comparability(component_plans, where)
    _check_version_comparability(component_plans)

    parent_axes = component_plans[0].base_plan.axes_metadata if component_plans else {}
    lineage_metadata: dict[str, Any] = {
        "metric": metric_ir.semantic_id,
        "components": [
            {
                "component_metric_id": cp.component_metric_ir.semantic_id,
                "role": cp.role,
                "datasource": cp.base_plan.datasource_name,
                "lineage_metadata": cp.base_plan.lineage_metadata,
            }
            for cp in component_plans
        ],
        "component_datasources": [
            (cp.component_metric_ir.semantic_id, cp.base_plan.datasource_name)
            for cp in component_plans
        ],
    }
    return DerivedObservePlan(
        metric_ir=metric_ir,
        component_plans=component_plans,
        parent_axes=parent_axes,
        lineage_metadata=lineage_metadata,
        warnings=[w for cp in component_plans for w in cp.base_plan.warnings],
    )
