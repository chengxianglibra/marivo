"""Base (non-cumulative) observe execution: sampled, panel, time-series, segmented, scalar.

Internal to ``marivo.analysis.intents`` — extracted from ``observe``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from ibis.expr.operations.relations import Field

from marivo.analysis.errors import MetricNotFoundError, SemanticKindMismatchError
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
from marivo.analysis.intents._observe_catalog import (
    _build_entity_adapter,
    _catalog_object,
    _entity_details,
    _field_details,
)
from marivo.analysis.intents._observe_dense import _fixed_grain_seconds_for_coverage
from marivo.analysis.intents._observe_inputs import _metric_expr
from marivo.analysis.intents.observe_planner import BaseObservePlan, _validate_field_expr
from marivo.analysis.intents.sampled_fold import (
    compile_fold,
    ensure_sampled_grain_supported,
    sample_point_table,
)
from marivo.analysis.session.core import Session
from marivo.analysis.windows.grain import Grain, ensure_grain_supported
from marivo.analysis.windows.spec import AbsoluteWindow
from marivo.semantic.catalog import SemanticKind, TimeDimensionDetails

_MEAN_SUM_COLUMN = "__mean_sum"
_MEAN_COUNT_COLUMN = "__mean_count_non_null"


def _is_lowerable_tier1_mean(metric_ir: Any) -> bool:
    """Return whether a simple mean has exact runtime components."""
    return (
        getattr(metric_ir, "metric_type", None) == "simple"
        and getattr(metric_ir, "aggregation", None) == "mean"
        and isinstance(getattr(metric_ir, "measure", None), str)
        and getattr(metric_ir, "time_fold", None) is None
    )


def _mean_component_contract(metric_ir: Any) -> dict[str, Any] | None:
    """Return the persisted lowering contract for an exact tier-1 mean."""
    if not _is_lowerable_tier1_mean(metric_ir):
        return None
    return {
        "kind": "weighted_average",
        "components": {
            "value": _MEAN_SUM_COLUMN,
            "weight": _MEAN_COUNT_COLUMN,
        },
        "lowered_from": "mean",
        "denominator_semantics": "count_non_null",
        "version": 1,
    }


def _base_aggregations(
    *,
    catalog: Any,
    resolver: Any,
    metric_ir: Any,
    metric_datasets: tuple[str, ...],
    dataset_tables: dict[str, Any],
) -> dict[str, Any]:
    """Build the public metric value and any exact attribution components."""
    aggregations = {
        "value": _metric_expr(
            catalog,
            resolver,
            metric_ir.semantic_id,
            metric_datasets,
            dataset_tables,
            metric_ir=metric_ir,
        )
    }
    if not _is_lowerable_tier1_mean(metric_ir):
        return aggregations

    measure = _catalog_object(catalog, metric_ir.measure, SemanticKind.MEASURE)
    measure_expr = resolver.measure_on(measure.ref, dataset_tables[metric_datasets[0]])
    aggregations[_MEAN_SUM_COLUMN] = measure_expr.sum()
    aggregations[_MEAN_COUNT_COLUMN] = measure_expr.count()
    return aggregations


def _split_mean_components(
    result: Any,
    *,
    metric_ir: Any,
    axes: dict[str, Any],
) -> tuple[Any, Any | None]:
    """Detach mean components from the canonical MetricFrame payload."""
    if not _is_lowerable_tier1_mean(metric_ir):
        return result, None
    axis_columns = [
        axis["column"]
        for axis in axes.values()
        if isinstance(axis, dict) and isinstance(axis.get("column"), str)
    ]
    component_df = result.df[[*axis_columns, _MEAN_SUM_COLUMN, _MEAN_COUNT_COLUMN, "value"]].rename(
        columns={"value": metric_ir.name}
    )
    canonical_df = result.df[[*axis_columns, "value"]].copy()
    return replace(result, df=canonical_df, row_count=len(canonical_df)), component_df


def _execute_sampled_base(
    plan: BaseObservePlan,
    metric_ir: Any,
    *,
    catalog: Any,
    resolver: Any,
    session: Session,
    resolved_window: AbsoluteWindow | None,
) -> tuple[
    Any,
    dict[str, Any],
    Literal["scalar", "time_series", "segmented", "panel"],
    Any | None,
]:
    """Two-phase execution for sampled semi-additive metrics.

    Phase A: aggregate spatially within each sample point.
    Phase B: fold over time (mean/min/max/first/last).

    Returns (result, axes, semantic_kind, coverage_df_or_None).
    """
    assert metric_ir.status_time_dimension is not None
    time_dimension_ir = _resolve_fold_time_field(catalog, metric_ir.status_time_dimension)
    root_adapter = _build_entity_adapter(
        catalog,
        resolver,
        _entity_details(catalog, plan.root_entity),
    )
    root_time_adapter = root_adapter.fields.get(time_dimension_ir.name)
    if root_time_adapter is None:
        # Try by iterating time dimensions
        for _fname, fadapter in root_adapter.fields.items():
            if fadapter.is_time and fadapter.semantic_id == metric_ir.status_time_dimension:
                root_time_adapter = fadapter
                break
    if root_time_adapter is None:
        raise MetricNotFoundError(
            message=f"time field adapter for '{metric_ir.status_time_dimension}' not found",
            context={"status_time_dimension": metric_ir.status_time_dimension},
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
    read_tz = datasource_read_timezone(session._connection_runtime, plan.datasource_name)
    profile = datasource_engine_profile(session._connection_runtime, plan.datasource_name)
    table = sample_point_table(
        plan.table,
        time_field_ir=root_time_adapter,
        sample_grain=sample_grain,
        report_tz=cast("ZoneInfo", session.report_tz),
        datasource_read_tz=read_tz,
        window=resolved_window,
        dataset_ir=root_adapter,
        profile=profile,
    )
    dimension_names = [dimension.column for dimension in plan.dimensions]
    metric_datasets = tuple(metric_ir.entities)
    dataset_tables = dict.fromkeys(metric_datasets, table)
    metric_expr = _metric_expr(
        catalog,
        resolver,
        metric_ir.semantic_id,
        metric_datasets,
        dataset_tables,
        metric_ir=metric_ir,
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
        phase_b_source.value, phase_b_source.sample_point, metric_ir.time_fold
    )
    if group_names:
        grouped_expr = (
            phase_b_source.group_by(group_names)
            .aggregate(value=folded_value)
            .order_by(group_names)
            .select(*group_names, "value")
        )
    else:
        grouped_expr = phase_b_source.aggregate(value=folded_value).select("value")
    result = execute(
        grouped_expr,
        datasource_name=plan.datasource_name,
        cache=session._connection_runtime,
        session_id=session.id,
    )
    # --- Coverage sidecar: count distinct sample points per bucket ---
    coverage_df: Any | None = None
    if is_time_series and resolved_window is not None:
        assert resolved_window.grain is not None
        coverage_expr = phase_b_source.group_by(group_names).aggregate(
            actual_samples=phase_b_source.sample_point.nunique()
        )
        coverage_result = execute(
            coverage_expr,
            datasource_name=plan.datasource_name,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        coverage_df = coverage_result.df
        # Ensure bucket_start is normalized the same way as the main frame
        if "bucket_start" in coverage_df.columns:
            coverage_df["bucket_start"] = ensure_bucket_start_timestamp(
                coverage_df["bucket_start"],
                time_meta=root_time_adapter.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                report_tz=cast("ZoneInfo", session.report_tz),
                backend_datetime_decode_policy=coverage_result.backend_datetime_decode_policy,
            )
        # Compute expected_samples from bucket duration and sample interval
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
    if "bucket_start" in result.df and resolved_window is not None:
        assert resolved_window.grain is not None
        result.df["bucket_start"] = ensure_bucket_start_timestamp(
            result.df["bucket_start"],
            time_meta=root_time_adapter.time_meta,
            dataset_ir=root_adapter,
            grain=resolved_window.grain,
            report_tz=cast("ZoneInfo", session.report_tz),
            backend_datetime_decode_policy=result.backend_datetime_decode_policy,
        )
    axes = dict(plan.axes_metadata)
    if is_time_series and resolved_window is not None:
        assert resolved_window.grain is not None
        axes["time"] = {
            "role": "time",
            "column": "bucket_start",
            "grain": resolved_window.grain.to_token(),
            "time_dimension": root_time_adapter.name,
            "ref": root_time_adapter.semantic_id,
        }
    if is_time_series and dimension_names:
        semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "panel"
    elif is_time_series:
        semantic_kind = "time_series"
    elif dimension_names:
        semantic_kind = "segmented"
    else:
        semantic_kind = "scalar"
    return result, axes, semantic_kind, coverage_df


def _resolve_fold_time_field(catalog: Any, status_time_dimension_id: str) -> TimeDimensionDetails:
    details = _field_details(catalog, status_time_dimension_id)
    if not isinstance(details, TimeDimensionDetails):
        raise SemanticKindMismatchError(
            message=f"status time dimension {status_time_dimension_id!r} is not a time dimension",
            context={"status_time_dimension": status_time_dimension_id},
        )
    return details


def _expression_source_columns(expr: Any) -> set[str] | None:
    try:
        nodes = expr.op().find_topmost(lambda node: isinstance(node, Field))
    except Exception:
        return None
    columns: set[str] = set()
    for node in nodes:
        name = getattr(node, "name", None)
        if isinstance(name, str) and name:
            columns.add(name)
    return columns


def _time_dependency_exprs(table: Any, *, time_field_ir: Any, dataset_ir: Any) -> list[Any]:
    expressions = [time_field_ir.fn(table)]
    time_meta = getattr(time_field_ir, "time_meta", None)
    required_prefix = getattr(time_meta, "required_prefix", None)
    if required_prefix:
        for field in dataset_ir.fields.values():
            if field.name == required_prefix or field.semantic_id == required_prefix:
                expressions.append(field.fn(table))
                break
    return expressions


def _prune_base_observe_projection(
    plan: BaseObservePlan,
    metric_ir: Any,
    *,
    catalog: Any,
    resolver: Any,
    resolved_window: AbsoluteWindow | None,
) -> BaseObservePlan:
    table = plan.table
    metric_datasets = tuple(metric_ir.entities)
    try:
        dataset_tables = dict.fromkeys(metric_datasets, table)
        expressions = [
            _metric_expr(
                catalog,
                resolver,
                metric_ir.semantic_id,
                metric_datasets,
                dataset_tables,
                metric_ir=metric_ir,
            )
        ]
        root_adapter = _build_entity_adapter(
            catalog,
            resolver,
            _entity_details(catalog, plan.root_entity),
        )
        if resolved_window is not None and resolved_window.grain is not None:
            time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
            expressions.extend(
                _time_dependency_exprs(
                    table,
                    time_field_ir=time_dimension_ir,
                    dataset_ir=root_adapter,
                )
            )
        for dimension in plan.dimensions:
            dimension_ref = _field_details(catalog, dimension.field.semantic_id).ref
            expressions.append(
                _validate_field_expr(
                    resolver.dimension_on(dimension_ref, table),
                    field_id=dimension.field.semantic_id,
                )
            )

        required_columns: set[str] = set()
        for expr in expressions:
            columns = _expression_source_columns(expr)
            if columns is None:
                return plan
            required_columns.update(columns)

        available_columns = set(table.columns)
        if not required_columns or not required_columns.issubset(available_columns):
            return plan
        selected_columns = [column for column in table.columns if column in required_columns]
        if not selected_columns:
            return plan
        pruned_table = table.select(*selected_columns)
    except Exception:
        return plan

    return replace(
        plan,
        table=pruned_table,
        dataset_tables=dict.fromkeys(metric_datasets, pruned_table),
    )


def _execute_base(
    plan: BaseObservePlan,
    metric_ir: Any,
    *,
    catalog: Any,
    resolver: Any,
    session: Session,
    dimensions: list[Any] | None,
    resolved_window: AbsoluteWindow | None,
) -> tuple[
    Any,
    dict[str, Any],
    Literal["scalar", "time_series", "segmented", "panel"],
    Any | None,
    Any | None,
]:
    """Execute a base observe and return result, shape, coverage, and components."""
    if getattr(metric_ir, "time_fold", None) is not None:
        sampled_result, sampled_axes, sampled_kind, coverage_df = _execute_sampled_base(
            plan,
            metric_ir,
            catalog=catalog,
            resolver=resolver,
            session=session,
            resolved_window=resolved_window,
        )
        return sampled_result, sampled_axes, sampled_kind, coverage_df, None
    metric_datasets = tuple(metric_ir.entities)
    primary_datasource = plan.datasource_name
    read_tz = datasource_read_timezone(session._connection_runtime, primary_datasource)
    profile = datasource_engine_profile(session._connection_runtime, primary_datasource)
    plan = _prune_base_observe_projection(
        plan,
        metric_ir,
        catalog=catalog,
        resolver=resolver,
        resolved_window=resolved_window,
    )
    dataset_tables = plan.dataset_tables
    resolved_dimensions = [
        (dimension.field.entity, dimension.field) for dimension in plan.dimensions
    ]
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    axes: dict[str, Any] = {}
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "scalar"

    if is_time_series and resolved_window is not None and resolved_dimensions:
        root_adapter = _build_entity_adapter(
            catalog,
            resolver,
            _entity_details(catalog, plan.root_entity),
        )
        time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
        if resolved_window.grain is not None:
            base = (
                time_dimension_ir.time_meta.granularity if time_dimension_ir.time_meta else None
            ) or "day"
            ensure_grain_supported(resolved_window.grain, base)
        bucketed_table = apply_time_series_bucket(
            plan.table,
            field_ir=time_dimension_ir,
            window=resolved_window,
            report_tz=cast("ZoneInfo", session.report_tz),
            datasource_read_tz=read_tz,
            profile=profile,
            dataset_ir=root_adapter,
        )
        dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
        dimension_exprs = {
            field_ir.name: _validate_field_expr(
                resolver.dimension_on(
                    _field_details(catalog, field_ir.semantic_id).ref, bucketed_table
                ),
                field_id=field_ir.semantic_id,
            ).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        bucketed_table = bucketed_table.mutate(**dimension_exprs)
        dataset_tables = dict.fromkeys(metric_datasets, bucketed_table)
        aggregations = _base_aggregations(
            catalog=catalog,
            resolver=resolver,
            metric_ir=metric_ir,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        group_names = ["bucket_start", *dimension_names]
        grouped_expr = (
            bucketed_table.group_by(group_names)
            .aggregate(**aggregations)
            .order_by(group_names)
            .select(*group_names, *aggregations)
        )
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        if "bucket_start" in result.df:
            result.df["bucket_start"] = ensure_bucket_start_timestamp(
                result.df["bucket_start"],
                time_meta=time_dimension_ir.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                report_tz=cast("ZoneInfo", session.report_tz),
                backend_datetime_decode_policy=result.backend_datetime_decode_policy,
            )
        axes = {
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain.to_token()
                if resolved_window.grain is not None
                else None,
                "time_dimension": time_dimension_ir.name,
                "ref": time_dimension_ir.semantic_id,
            },
            **{
                field_ir.name: {
                    "role": "dimension",
                    "column": field_ir.name,
                    "ref": field_ir.semantic_id,
                }
                for _, field_ir in resolved_dimensions
            },
        }
        semantic_kind = "panel"
    elif is_time_series and resolved_window is not None:
        root_adapter = _build_entity_adapter(
            catalog,
            resolver,
            _entity_details(catalog, plan.root_entity),
        )
        time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
        if resolved_window.grain is not None:
            base = (
                time_dimension_ir.time_meta.granularity if time_dimension_ir.time_meta else None
            ) or "day"
            ensure_grain_supported(resolved_window.grain, base)
        bucketed_table = apply_time_series_bucket(
            plan.table,
            field_ir=time_dimension_ir,
            window=resolved_window,
            report_tz=cast("ZoneInfo", session.report_tz),
            datasource_read_tz=read_tz,
            profile=profile,
            dataset_ir=root_adapter,
        )
        dataset_tables = dict.fromkeys(metric_datasets, bucketed_table)
        aggregations = _base_aggregations(
            catalog=catalog,
            resolver=resolver,
            metric_ir=metric_ir,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        grouped_expr = (
            bucketed_table.group_by("bucket_start")
            .aggregate(**aggregations)
            .order_by("bucket_start")
            .select("bucket_start", *aggregations)
        )
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        if "bucket_start" in result.df:
            result.df["bucket_start"] = ensure_bucket_start_timestamp(
                result.df["bucket_start"],
                time_meta=time_dimension_ir.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                report_tz=cast("ZoneInfo", session.report_tz),
                backend_datetime_decode_policy=result.backend_datetime_decode_policy,
            )
        axes = {
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain.to_token()
                if resolved_window.grain is not None
                else None,
                "time_dimension": time_dimension_ir.name,
                "ref": time_dimension_ir.semantic_id,
            }
        }
        semantic_kind = "time_series"
    elif resolved_dimensions:
        table = plan.table
        dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
        dimension_exprs = {
            field_ir.name: _validate_field_expr(
                resolver.dimension_on(_field_details(catalog, field_ir.semantic_id).ref, table),
                field_id=field_ir.semantic_id,
            ).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        table = table.mutate(**dimension_exprs)
        dataset_tables = dict.fromkeys(metric_datasets, table)
        aggregations = _base_aggregations(
            catalog=catalog,
            resolver=resolver,
            metric_ir=metric_ir,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        grouped_expr = (
            table.group_by(dimension_names)
            .aggregate(**aggregations)
            .order_by(dimension_names)
            .select(*dimension_names, *aggregations)
        )
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        axes = {
            field_ir.name: {
                "role": "dimension",
                "column": field_ir.name,
                "ref": field_ir.semantic_id,
            }
            for _, field_ir in resolved_dimensions
        }
        semantic_kind = "segmented"
    else:
        aggregations = _base_aggregations(
            catalog=catalog,
            resolver=resolver,
            metric_ir=metric_ir,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        grouped_expr = plan.table.aggregate(**aggregations)
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session._connection_runtime,
            session_id=session.id,
        )
    result, component_df = _split_mean_components(result, metric_ir=metric_ir, axes=axes)
    return result, axes, semantic_kind, None, component_df
