"""Derived observe execution: folded components, coverage merge, composition evaluation.

Internal to ``marivo.analysis.intents`` — extracted from ``observe``.
"""

from __future__ import annotations

from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from marivo.analysis._cumulative import (
    CumulativeCompareBlocker,
    normalize_cumulative_anchor,
)
from marivo.analysis.errors import MetricNotFoundError
from marivo.analysis.executor.bucketing import (
    apply_time_series_bucket,
    bucket_start_expr,
    ensure_bucket_start_timestamp,
)
from marivo.analysis.executor.runner import execute
from marivo.analysis.executor.windowing import (
    datasource_engine_profile,
    datasource_read_timezone,
    resolve_window_time_field,
)
from marivo.analysis.intents._observe_base import _resolve_fold_time_field
from marivo.analysis.intents._observe_catalog import _build_entity_adapter, _entity_details
from marivo.analysis.intents._observe_components import (
    _add_fold_metadata_to_component_df,
    _component_frame_df,
    _evaluate_composition_on_frame,
    _is_component_aware_composition,
    _role_to_column_name,
)
from marivo.analysis.intents._observe_cumulative import _execute_cumulative
from marivo.analysis.intents._observe_dense import _fixed_grain_seconds_for_coverage
from marivo.analysis.intents._observe_inputs import _metric_expr, _Result
from marivo.analysis.intents.observe_planner import (
    ComponentPlan,
    CumulativeObservePlan,
    DerivedObservePlan,
)
from marivo.analysis.intents.sampled_fold import (
    compile_fold,
    ensure_sampled_grain_supported,
    sample_interval_token,
    sample_point_table,
)
from marivo.analysis.session.core import Session
from marivo.analysis.windows.grain import Grain, ensure_grain_supported
from marivo.analysis.windows.spec import AbsoluteWindow


def _execute_folded_component(
    cp: ComponentPlan,
    component_name: str,
    component_datasets: tuple[str, ...],
    dim_columns: list[str],
    *,
    catalog: Any,
    resolver: Any,
    session: Session,
    resolved_window: AbsoluteWindow | None,
) -> tuple[Any, Any | None]:
    """Execute a single folded component through the two-phase sampled path.

    Returns (component_df, coverage_df_or_None).
    """
    component_metric_ir = cp.component_metric_ir
    assert component_metric_ir.status_time_dimension is not None
    time_dimension_ir = _resolve_fold_time_field(catalog, component_metric_ir.status_time_dimension)
    root_adapter = _build_entity_adapter(
        catalog,
        resolver,
        _entity_details(catalog, cp.base_plan.root_entity),
    )
    root_time_adapter = root_adapter.fields.get(time_dimension_ir.name)
    if root_time_adapter is None:
        for _fname, fadapter in root_adapter.fields.items():
            if (
                fadapter.is_time
                and fadapter.semantic_id == component_metric_ir.status_time_dimension
            ):
                root_time_adapter = fadapter
                break
    if root_time_adapter is None:
        raise MetricNotFoundError(
            message=f"time field adapter for '{component_metric_ir.status_time_dimension}' not found",
            context={"status_time_dimension": component_metric_ir.status_time_dimension},
        )
    sample_interval = root_time_adapter.sample_interval
    assert sample_interval is not None
    assert root_time_adapter.time_meta is not None
    ensure_sampled_grain_supported(
        requested_grain=resolved_window.grain if resolved_window is not None else None,
        time_meta=root_time_adapter.time_meta,
        sample_interval=sample_interval,
    )
    sample_grain = Grain(count=sample_interval.count, unit=sample_interval.unit)
    read_tz = datasource_read_timezone(session._connection_runtime, cp.base_plan.datasource_name)
    profile = datasource_engine_profile(session._connection_runtime, cp.base_plan.datasource_name)
    table = sample_point_table(
        cp.base_plan.table,
        time_field_ir=root_time_adapter,
        sample_grain=sample_grain,
        report_tz=cast("ZoneInfo", session.report_tz),
        datasource_read_tz=read_tz,
        window=resolved_window,
        dataset_ir=root_adapter,
        profile=profile,
    )
    dimension_names = [dimension.column for dimension in cp.base_plan.dimensions]
    dataset_tables = dict.fromkeys(component_datasets, table)
    metric_expr = _metric_expr(
        catalog,
        resolver,
        component_metric_ir.semantic_id,
        component_datasets,
        dataset_tables,
    )
    phase_a = table.group_by(["sample_point", *dimension_names]).aggregate(value=metric_expr)
    is_time_series = resolved_window is not None and resolved_window.grain is not None
    if is_time_series and resolved_window is not None:
        assert resolved_window.grain is not None
        phase_b_source = phase_a.mutate(
            bucket_start=bucket_start_expr(phase_a.sample_point, resolved_window.grain)
        )
        group_names = ["bucket_start", *dimension_names]
    else:
        phase_b_source = phase_a
        group_names = list(dimension_names)
    folded_value = compile_fold(
        phase_b_source.value, phase_b_source.sample_point, component_metric_ir.time_fold
    )
    if group_names:
        grouped_expr = (
            phase_b_source.group_by(group_names)
            .aggregate(**{component_name: folded_value})
            .order_by(group_names)
            .select(*group_names, component_name)
        )
    else:
        grouped_expr = phase_b_source.aggregate(**{component_name: folded_value}).select(
            component_name
        )
    result = execute(
        grouped_expr,
        datasource_name=cp.base_plan.datasource_name,
        cache=session._connection_runtime,
        session_id=session.id,
    )
    df = result.df
    # Normalize bucket_start
    if "bucket_start" in df and resolved_window is not None:
        assert resolved_window.grain is not None
        df["bucket_start"] = ensure_bucket_start_timestamp(
            df["bucket_start"],
            time_meta=root_time_adapter.time_meta,
            dataset_ir=root_adapter,
            grain=resolved_window.grain,
            report_tz=cast("ZoneInfo", session.report_tz),
            backend_datetime_decode_policy=result.backend_datetime_decode_policy,
        )
    # Coverage sidecar
    coverage_df: Any | None = None
    if is_time_series and resolved_window is not None:
        assert resolved_window.grain is not None
        coverage_expr = phase_b_source.group_by(group_names).aggregate(
            actual_samples=phase_b_source.sample_point.nunique()
        )
        coverage_result = execute(
            coverage_expr,
            datasource_name=cp.base_plan.datasource_name,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        coverage_df = coverage_result.df
        if "bucket_start" in coverage_df.columns:
            coverage_df["bucket_start"] = ensure_bucket_start_timestamp(
                coverage_df["bucket_start"],
                time_meta=root_time_adapter.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                report_tz=cast("ZoneInfo", session.report_tz),
                backend_datetime_decode_policy=coverage_result.backend_datetime_decode_policy,
            )
        bucket_seconds = _fixed_grain_seconds_for_coverage(
            resolved_window.grain.count, resolved_window.grain.unit
        )
        interval_seconds = _fixed_grain_seconds_for_coverage(
            sample_interval.count, sample_interval.unit
        )
        expected = bucket_seconds // interval_seconds if interval_seconds > 0 else 0
        coverage_df["expected_samples"] = expected
        coverage_df["coverage_ratio"] = coverage_df["actual_samples"] / expected
        coverage_df["coverage_status"] = coverage_df["coverage_ratio"].apply(
            lambda r: "complete" if r == 1.0 else "partial"
        )
    return df, coverage_df


def _merge_component_coverages(
    component_coverages: list[Any],
    merge_keys: list[str],
) -> Any:
    """Merge homogeneous component coverage using the least-covered component."""
    pandas = __import__("pandas")
    if not component_coverages:
        return None
    window_columns = ["expected_span", "covered_span"]
    sample_columns = ["actual_samples", "expected_samples"]
    window_coverages = [
        frame for frame in component_coverages if set(window_columns).issubset(frame.columns)
    ]
    sample_coverages = [
        frame for frame in component_coverages if set(sample_columns).issubset(frame.columns)
    ]
    # Preserve the pre-existing sampled-fold behavior for mixed derived metrics.
    selected = sample_coverages or window_coverages
    if not selected:
        return None
    fields = (
        [*sample_columns, "coverage_ratio", "coverage_status"]
        if sample_coverages
        else [*window_columns, "coverage_ratio", "coverage_status"]
    )
    normalized = [frame[[*merge_keys, *fields]] for frame in selected]
    combined = pandas.concat(normalized, ignore_index=True)
    aggregations = {
        fields[0]: "min" if sample_coverages else "max",
        fields[1]: "max" if sample_coverages else "min",
        "coverage_ratio": "min",
    }
    if merge_keys:
        merged = combined.groupby(merge_keys, dropna=False, as_index=False).agg(aggregations)
    else:
        merged = pandas.DataFrame(
            {
                column: [getattr(combined[column], operation)()]
                for column, operation in aggregations.items()
            }
        )
    merged["coverage_status"] = (
        merged["coverage_ratio"].eq(1.0).map({True: "complete", False: "partial"})
    )
    return merged


def _execute_derived(
    plan: DerivedObservePlan,
    metric_ir: Any,
    *,
    catalog: Any,
    resolver: Any,
    session: Session,
    resolved_window: AbsoluteWindow | None,
) -> tuple[
    Any,
    Any | None,
    dict[str, Any],
    Literal["scalar", "time_series", "segmented", "panel"],
    Any | None,
]:
    """Execute a DerivedObservePlan.

    Returns (result, component_df, axes, semantic_kind, derived_coverage_df_or_None).
    """
    pandas = __import__("pandas")
    metric_name = metric_ir.name
    component_frames: list[Any] = []
    component_coverages: list[Any] = []
    dim_columns = list(
        dict.fromkeys(d.column for cp in plan.component_plans for d in cp.base_plan.dimensions)
    )
    has_time = any(
        cp.base_plan.axes_metadata.get("time") is not None for cp in plan.component_plans
    )
    merge_keys = (["bucket_start"] if has_time else []) + dim_columns

    for cp in plan.component_plans:
        component_name = _role_to_column_name(metric_ir, cp.role)
        component_datasets = tuple(cp.component_metric_ir.entities)
        component_time_fold = getattr(cp.component_metric_ir, "time_fold", None)

        if isinstance(cp.base_plan, CumulativeObservePlan):
            # Cumulative component: execute via _execute_cumulative and
            # rename the "value" column to the component name so the
            # result is shape-compatible with other component DataFrames.
            cum_result, _cum_axes, _cum_kind, _cum_coverage_df = _execute_cumulative(
                cp.base_plan,
                catalog=catalog,
                resolver=resolver,
                session=session,
                resolved_window=resolved_window,
            )
            df = cum_result.df.rename(columns={"value": component_name})
            component_frames.append(df)
            if _cum_coverage_df is not None:
                component_coverages.append(_cum_coverage_df)
            continue

        if component_time_fold is not None and has_time:
            # Folded component: execute through two-phase sampled path
            df, coverage_df = _execute_folded_component(
                cp=cp,
                component_name=component_name,
                component_datasets=component_datasets,
                dim_columns=dim_columns,
                catalog=catalog,
                resolver=resolver,
                session=session,
                resolved_window=resolved_window,
            )
            if coverage_df is not None:
                component_coverages.append(coverage_df)
        else:
            # Standard (non-folded) component path
            table = cp.base_plan.table
            if has_time:
                assert (
                    resolved_window is not None
                )  # narrowing: has_time implies resolved_window is set
                root_adapter = _build_entity_adapter(
                    catalog,
                    resolver,
                    _entity_details(catalog, cp.base_plan.root_entity),
                )
                time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
                if resolved_window.grain is not None:
                    base = (
                        time_dimension_ir.time_meta.granularity
                        if time_dimension_ir.time_meta
                        else None
                    ) or "day"
                    ensure_grain_supported(resolved_window.grain, base)
                read_tz = datasource_read_timezone(
                    session._connection_runtime, cp.base_plan.datasource_name
                )
                profile = datasource_engine_profile(
                    session._connection_runtime, cp.base_plan.datasource_name
                )
                table = apply_time_series_bucket(
                    table,
                    field_ir=time_dimension_ir,
                    window=resolved_window,
                    report_tz=cast("ZoneInfo", session.report_tz),
                    datasource_read_tz=read_tz,
                    profile=profile,
                    dataset_ir=root_adapter,
                )
                group_names = ["bucket_start", *dim_columns]
            else:
                group_names = list(dim_columns)
            # The planner already widened all component datasets into a single table,
            # so map every component dataset to the same resolver input table.
            component_dataset_tables = dict.fromkeys(component_datasets, table)
            metric_expr = _metric_expr(
                catalog,
                resolver,
                cp.component_metric_ir.semantic_id,
                component_datasets,
                component_dataset_tables,
            )
            if group_names:
                grouped_expr = (
                    table.group_by(group_names)
                    .aggregate(**{component_name: metric_expr})
                    .order_by(group_names)
                    .select(*group_names, component_name)
                )
            else:
                grouped_expr = table.aggregate(**{component_name: metric_expr}).select(
                    component_name
                )
            df_result = execute(
                grouped_expr,
                datasource_name=cp.base_plan.datasource_name,
                cache=session._connection_runtime,
                session_id=session.id,
            )
            df = df_result.df
            if has_time and "bucket_start" in df:
                df["bucket_start"] = ensure_bucket_start_timestamp(
                    df["bucket_start"],
                    time_meta=time_dimension_ir.time_meta,
                    dataset_ir=root_adapter,
                    grain=resolved_window.grain if resolved_window else None,
                    report_tz=cast("ZoneInfo", session.report_tz),
                    backend_datetime_decode_policy=df_result.backend_datetime_decode_policy,
                )
        component_frames.append(df)

    if not component_frames:
        merged = pandas.DataFrame(columns=[*merge_keys, "value"])
    else:
        merged = component_frames[0]
        for frame in component_frames[1:]:
            if merge_keys:
                merged = pandas.merge(merged, frame, on=merge_keys, how="outer")
            else:
                merged = pandas.concat([merged, frame], axis=1)
    merged["value"] = _evaluate_composition_on_frame(metric_ir, merged)
    if merge_keys:
        result_df = merged[[*merge_keys, "value"]].sort_values(merge_keys).reset_index(drop=True)
    else:
        result_df = merged[["value"]]

    component_df: Any | None = None
    if _is_component_aware_composition(metric_ir):
        # ComponentFrame keeps the metric name as its value column so that the
        # melt in _add_fold_metadata_to_component_df (which renames role
        # columns to "value") does not collide with the metric value column.
        component_df = _component_frame_df(
            raw_df=merged.rename(columns={"value": metric_name}),
            metric_ir=metric_ir,
            axes_columns=merge_keys,
            metric_value_column=metric_name,
        )
        if merge_keys:
            component_df = component_df.sort_values(merge_keys).reset_index(drop=True)
        # Add fold metadata columns for folded components (long format rows)
        _any_component_folded = any(
            getattr(cp.component_metric_ir, "time_fold", None) is not None
            for cp in plan.component_plans
        )
        if _any_component_folded:
            component_df = _add_fold_metadata_to_component_df(
                component_df,
                metric_ir,
                plan.component_plans,
                merge_keys,
                metric_name,
            )

    # --- Merge coverage from folded and cumulative components ---
    derived_coverage_df: Any | None = None
    if component_coverages:
        derived_coverage_df = _merge_component_coverages(component_coverages, merge_keys)

    if has_time and dim_columns:
        semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "panel"
    elif has_time:
        semantic_kind = "time_series"
    elif dim_columns:
        semantic_kind = "segmented"
    else:
        semantic_kind = "scalar"

    axes: dict[str, Any] = {}
    if has_time and plan.component_plans and resolved_window is not None:
        # Resolve time dimension for axes metadata.
        # Prefer the status_time_dimension from any folded component, otherwise
        # fall back to the standard time dimension resolution.
        fold_time_dim_name: str | None = None
        for cp in plan.component_plans:
            cp_fold = getattr(cp.component_metric_ir, "status_time_dimension", None)
            if cp_fold is not None:
                fold_time_dim_name = cp_fold
                break
        if fold_time_dim_name is not None:
            axes["time"] = {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain.to_token()
                if resolved_window.grain is not None
                else None,
                "time_dimension": fold_time_dim_name.rsplit(".", 1)[-1]
                if "." in fold_time_dim_name
                else fold_time_dim_name,
            }
        else:
            first_cp = plan.component_plans[0]
            root_adapter = _build_entity_adapter(
                catalog,
                resolver,
                _entity_details(catalog, first_cp.base_plan.root_entity),
            )
            time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
            axes["time"] = {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain.to_token()
                if resolved_window.grain is not None
                else None,
                "time_dimension": time_dimension_ir.name,
            }
    for col in dim_columns:
        axes[col] = {"role": "dimension", "column": col}

    return _Result(result_df), component_df, axes, semantic_kind, derived_coverage_df


def _build_fold_meta(metric_ir: Any, catalog: Any) -> dict[str, Any]:
    """Build fold metadata dict for a folded metric's MetricFrameMeta."""
    sample_interval_token_val: str | None = None
    if metric_ir.status_time_dimension is not None:
        tf = _resolve_fold_time_field(catalog, metric_ir.status_time_dimension)
        si = getattr(tf, "sample_interval", None)
        if si is not None:
            sample_interval_token_val = sample_interval_token(si)
    return {
        "time_fold": metric_ir.time_fold.label(),
        "fold_kind": getattr(metric_ir.time_fold, "kind", None),
        "status_time_dimension": metric_ir.status_time_dimension,
        "sample_interval": sample_interval_token_val,
    }


def _build_derived_fold_meta(derived_plan: DerivedObservePlan, catalog: Any) -> dict[str, Any]:
    """Build fold metadata dict for a derived metric with folded components."""
    component_folds: list[dict[str, Any]] = []
    sample_interval_token_val: str | None = None
    for cp in derived_plan.component_plans:
        cp_ir = cp.component_metric_ir
        if getattr(cp_ir, "time_fold", None) is None:
            continue
        fold_entry: dict[str, Any] = {
            "component_metric_id": cp_ir.semantic_id,
            "time_fold": cp_ir.time_fold.label(),
            "fold_kind": getattr(cp_ir.time_fold, "kind", None),
            "status_time_dimension": cp_ir.status_time_dimension,
        }
        component_folds.append(fold_entry)
        # Capture sample_interval from the first folded component
        if sample_interval_token_val is None and cp_ir.status_time_dimension is not None:
            tf = _resolve_fold_time_field(catalog, cp_ir.status_time_dimension)
            si = getattr(tf, "sample_interval", None)
            if si is not None:
                sample_interval_token_val = sample_interval_token(si)
    return {
        "time_fold": "derived",
        "component_folds": component_folds,
        "sample_interval": sample_interval_token_val,
    }


def _cumulative_marker_for_plan(plan: CumulativeObservePlan, catalog: Any) -> dict[str, Any]:
    """Build per-component cumulative marker for a CumulativeObservePlan.

    Resolves the real ``over`` from the catalog registry, mirroring the
    direct cumulative path.  ``plan.over`` and
    ``plan.base_metric_ir.composition.over`` are both ``None`` for
    per-component plans because the metric IR is a ``_MetricDetailsAdapter``
    whose ``composition.over`` defaults to ``None``.
    """
    over = plan.over
    anchor: Any = "all_history"
    real_ir = None
    registry = catalog._require_index().registry
    real_ir = registry.metrics.get(plan.metric_ir.semantic_id)
    if real_ir is not None and real_ir.composition is not None:
        if over is None:
            over = getattr(real_ir.composition, "over", None)
        anchor = getattr(real_ir.composition, "anchor", "all_history") or "all_history"
    # Prefer the plan's resolved composition when available (carries the real
    # anchor even when the registry is not attached).
    plan_composition = getattr(plan, "composition", None)
    if plan_composition is not None:
        anchor = getattr(plan_composition, "anchor", anchor) or anchor
    return {
        "kind": "cumulative",
        "base": plan.base_metric_ir.semantic_id,
        "over": over,
        "anchor": anchor,
        "components": None,
    }


def _derived_cumulative_marker(plan: DerivedObservePlan, catalog: Any) -> dict[str, Any] | None:
    """Return derived-contains-cumulative metadata if any component is cumulative."""
    components: dict[str, dict[str, Any]] = {
        cp.role: _cumulative_marker_for_plan(cp.base_plan, catalog)
        for cp in plan.component_plans
        if isinstance(cp.base_plan, CumulativeObservePlan)
    }
    if not components:
        return None
    non_cumulative_roles = [
        cp.role
        for cp in plan.component_plans
        if not isinstance(cp.base_plan, CumulativeObservePlan)
    ]
    anchors = [
        normalize_cumulative_anchor(payload.get("anchor")) for payload in components.values()
    ]
    compare_blocker: CumulativeCompareBlocker | None = None
    anchor = None
    if non_cumulative_roles:
        compare_blocker = "non_cumulative_component"
    elif any(item is None for item in anchors):
        compare_blocker = "unresolved_component_anchor"
    elif anchors and any(item != anchors[0] for item in anchors[1:]):
        compare_blocker = "mixed_component_anchors"
    elif anchors:
        anchor = anchors[0]
    return {
        "kind": "derived_contains_cumulative",
        "anchor": anchor,
        "compare_blocker": compare_blocker,
        "components": components,
    }
