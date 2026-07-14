"""Multi-metric observe: shared-scope planning, fusion, and one MetricFrame."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import pandas as pd

from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    compute_prospective_artifact_id,
    frame_exists_on_disk,
)
from marivo.analysis.executor.bucketing import (
    apply_time_series_bucket,
    ensure_bucket_start_timestamp,
)
from marivo.analysis.executor.runner import (
    execute,
    normalize_slice_for_storage,
)
from marivo.analysis.executor.windowing import (
    datasource_engine_profile,
    datasource_read_timezone,
    resolve_window_time_field,
)
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._observe_persist import (
    _meta_aggregation,
    _metric_semantics_payload,
)
from marivo.analysis.intents._shape import SemanticShape, observe_output_shape
from marivo.analysis.intents._types import SliceValue
from marivo.analysis.intents.observe import (
    _build_entity_adapter,
    _catalog_object,
    _commit_observe_metric_frame,
    _dump_dimensions,
    _entity_adapter_maps,
    _entity_details,
    _field_details,
    _gen_ref,
    _meta_additivity,
    _metric_expr,
    _metric_planner_scope,
    _normalize_dimension_boundary,
    _normalize_dimension_list_boundary,
    _normalize_where_boundary,
    _params_digest,
    _resolve_timescope,
    _validate_dimension_ids,
)
from marivo.analysis.intents.observe_planner import (
    BaseObservePlan,
    _is_cumulative_metric,
    _planned_metric,
    _validate_field_expr,
    plan_base_observe,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.semantic_inputs import (
    DimensionInput,
    MetricInput,
    normalize_metric_input,
)
from marivo.analysis.session._load import load_frame
from marivo.analysis.session._runtime import (
    persist_job_record,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.windows.grain import ensure_grain_supported
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    GrainInput,
    TimeScopeInput,
    dump_window,
)
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    SemanticKind,
    SimpleMetricDetails,
)


def _normalize_metric_list(catalog: Any, metrics: list[Any]) -> list[str]:
    metric_ids = [normalize_metric_input(catalog, metric) for metric in metrics]
    seen: set[str] = set()
    duplicates: list[str] = []
    for metric_id in metric_ids:
        if metric_id in seen and metric_id not in duplicates:
            duplicates.append(metric_id)
        seen.add(metric_id)
    if duplicates:
        raise SemanticKindMismatchError(
            message=f"observe received duplicate metrics {duplicates!r}",
            hint="pass each metric once; the frame carries one column per metric",
            context={"argument": "metric", "duplicates": duplicates},
        )
    return metric_ids


def _reject_unsupported_metric(metric_id: str, metric_details: Any, metric_ir: Any) -> None:
    if _is_cumulative_metric(metric_ir):
        raise SemanticKindMismatchError(
            message=(
                "observe with multiple metrics accepts simple, unfolded metrics; "
                f"{metric_id!r} is a cumulative metric and must be observed "
                "as a single metric"
            ),
            hint=(
                f"observe {metric_id!r} separately: observe(catalog.get({metric_id!r}), ...). "
                "Observe cumulative metrics one at a time so the baseline/flow "
                "query cost and frame metadata are explicit."
            ),
            context={"metric": metric_id, "reason": "cumulative"},
        )
    if isinstance(metric_details, DerivedMetricDetails) or metric_ir.metric_type == "derived":
        raise SemanticKindMismatchError(
            message=(
                "observe with multiple metrics accepts simple, unfolded metrics; "
                f"{metric_id!r} is a derived metric"
            ),
            hint=f"observe {metric_id!r} separately: observe(catalog.get({metric_id!r}), ...)",
            context={"metric": metric_id, "reason": "derived"},
        )
    if getattr(metric_ir, "time_fold", None) is not None:
        raise SemanticKindMismatchError(
            message=(
                "observe with multiple metrics accepts simple, unfolded metrics; "
                f"{metric_id!r} declares a time fold"
            ),
            hint=f"observe {metric_id!r} separately: observe(catalog.get({metric_id!r}), ...)",
            context={"metric": metric_id, "reason": "folded"},
        )


@dataclass(frozen=True)
class _PlannedMetric:
    metric_id: str
    metric_name: str
    model_name: str
    column: str
    metric_ir: Any
    plan: BaseObservePlan


def _metric_columns(metric_ids: list[str]) -> dict[str, str]:
    """Metric id -> value column name; short name unless names collide."""
    short_names = [metric_id.split(".", 1)[1] for metric_id in metric_ids]
    columns: dict[str, str] = {}
    for metric_id, short in zip(metric_ids, short_names, strict=True):
        if short_names.count(short) > 1:
            columns[metric_id] = metric_id.replace(".", "__")
        else:
            columns[metric_id] = short
    return columns


def _fusion_key(plan: BaseObservePlan) -> tuple[str, str]:
    return (plan.datasource_name, plan.root_entity)


def _execute_fused_group(
    group: list[_PlannedMetric],
    *,
    catalog: Any,
    resolver: Any,
    session: Session,
    resolved_window: AbsoluteWindow | None,
) -> tuple[Any, dict[str, Any], Literal["scalar", "time_series", "segmented", "panel"]]:
    """Execute one fusion group; returns (ExecuteResult, axes, semantic_kind)."""
    first = group[0]
    plan = first.plan
    primary_datasource = plan.datasource_name
    read_tz = datasource_read_timezone(session._connection_runtime, primary_datasource)
    profile = datasource_engine_profile(session._connection_runtime, primary_datasource)
    resolved_dimensions = [(d.field.entity, d.field) for d in plan.dimensions]
    is_time_series = resolved_window is not None and resolved_window.grain is not None
    table = plan.table
    axes: dict[str, Any] = {}
    time_dimension_ir = None
    root_adapter = None
    if is_time_series and resolved_window is not None and resolved_window.grain is not None:
        narrowed_window = resolved_window
        assert narrowed_window.grain is not None
        root_adapter = _build_entity_adapter(
            catalog, resolver, _entity_details(catalog, plan.root_entity)
        )
        time_dimension_ir = resolve_window_time_field(root_adapter, window=narrowed_window)
        base = (
            time_dimension_ir.time_meta.granularity if time_dimension_ir.time_meta else None
        ) or "day"
        ensure_grain_supported(narrowed_window.grain, base)
        table = apply_time_series_bucket(
            table,
            field_ir=time_dimension_ir,
            window=narrowed_window,
            report_tz=cast("ZoneInfo", session.report_tz),
            datasource_read_tz=read_tz,
            profile=profile,
            dataset_ir=root_adapter,
        )
    dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
    if dimension_names:
        dimension_exprs = {
            field_ir.name: _validate_field_expr(
                resolver.dimension_on(_field_details(catalog, field_ir.semantic_id).ref, table),
                field_id=field_ir.semantic_id,
            ).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        table = table.mutate(**dimension_exprs)
    group_datasets = tuple(sorted({e for pm in group for e in pm.metric_ir.entities}))
    dataset_tables = dict.fromkeys(group_datasets, table)
    aggregates = {
        pm.column: _metric_expr(
            catalog,
            resolver,
            pm.metric_ir.semantic_id,
            tuple(pm.metric_ir.entities),
            dataset_tables,
        )
        for pm in group
    }
    group_names = (["bucket_start"] if is_time_series else []) + dimension_names
    if group_names:
        grouped = (
            table.group_by(group_names)
            .aggregate(**aggregates)
            .order_by(group_names)
            .select(*group_names, *aggregates.keys())
        )
    else:
        grouped = table.aggregate(**aggregates)
    result = execute(
        grouped,
        datasource_name=primary_datasource,
        cache=session._connection_runtime,
        session_id=session.id,
    )
    if (
        is_time_series
        and resolved_window is not None
        and resolved_window.grain is not None
        and time_dimension_ir is not None
        and root_adapter is not None
        and "bucket_start" in result.df
    ):
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
        }
    for _, field_ir in resolved_dimensions:
        axes[field_ir.name] = {"role": "dimension", "column": field_ir.name}
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = observe_output_shape(
        has_grain=is_time_series, has_dimensions=bool(dimension_names)
    )
    return result, axes, semantic_kind


def observe_multi(
    metrics: list[MetricInput],
    *,
    time_scope: TimeScopeInput = None,
    grain: GrainInput = None,
    dimensions: list[DimensionInput] | None = None,
    slice_by: dict[DimensionInput, SliceValue] | None = None,
    time_dimension: DimensionInput | None = None,
    expect_shape: SemanticShape | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> MetricFrame:
    if session is None:
        session = require_current_session()
    ensure_session_writable(session)
    catalog = session.catalog
    catalog._require_ready()
    metric_ids = _normalize_metric_list(catalog, metrics)
    columns_by_metric = _metric_columns(metric_ids)

    planned: list[_PlannedMetric] = []
    metric_irs: dict[str, Any] = {}
    for metric_id in metric_ids:
        metric_details = _catalog_object(catalog, metric_id, SemanticKind.METRIC).details()
        assert isinstance(metric_details, (SimpleMetricDetails, DerivedMetricDetails))
        metric_ir = _planned_metric(metric_details)
        _reject_unsupported_metric(metric_id, metric_details, metric_ir)
        metric_irs[metric_id] = metric_ir

    resolver = catalog._resolver(connections=session._connection_runtime)

    # Shared scope: dimensions/slice/time resolve per metric scope and must agree.
    scopes = {mid: _metric_planner_scope(catalog, metric_irs[mid]) for mid in metric_ids}
    time_dimension_id = (
        _normalize_dimension_boundary(
            catalog,
            time_dimension,
            argument="time_dimension",
            scoped_entity_refs=scopes[metric_ids[0]],
        )
        if time_dimension is not None
        else None
    )
    normalized_dimensions: list[str] | None = None
    where_by_id: dict[str, Any] = {}
    for metric_id in metric_ids:
        metric_dimensions = _normalize_dimension_list_boundary(
            catalog, dimensions, scoped_entity_refs=scopes[metric_id]
        )
        metric_where = _normalize_where_boundary(
            catalog, slice_by, scoped_entity_refs=scopes[metric_id]
        )
        if normalized_dimensions is None:
            normalized_dimensions = metric_dimensions
            where_by_id = metric_where
        elif metric_dimensions != normalized_dimensions or metric_where != where_by_id:
            raise SemanticKindMismatchError(
                message=(
                    "observe with multiple metrics requires dimensions and slice_by "
                    f"to resolve identically for every metric; {metric_id!r} resolved "
                    "differently"
                ),
                context={
                    "metric": metric_id,
                    "expected_dimensions": normalized_dimensions,
                    "got_dimensions": metric_dimensions,
                },
            )

    resolved_window, original_timescope = _resolve_timescope(
        time_scope, grain=grain, time_dimension=time_dimension_id
    )
    is_time_series = resolved_window is not None and resolved_window.grain is not None
    dimension_refs = _validate_dimension_ids(normalized_dimensions)
    if expect_shape is not None:
        predicted_shape = observe_output_shape(
            has_grain=is_time_series, has_dimensions=bool(dimension_refs)
        )
        if predicted_shape != expect_shape:
            raise SemanticKindMismatchError(
                message=(
                    f"observe will produce semantic_shape {predicted_shape!r} for these "
                    f"inputs, but expect_shape={expect_shape!r} was requested"
                ),
                context={
                    "intent": "observe",
                    "predicted_semantic_shape": predicted_shape,
                    "expect_shape": expect_shape,
                },
            )

    started_at = datetime.now(UTC)
    started = monotonic()
    stored_where = normalize_slice_for_storage(where_by_id)

    session._connection_runtime.begin_query_capture()
    try:
        for metric_id in metric_ids:
            metric_ir = metric_irs[metric_id]
            required_entity_refs = set(metric_ir.entities)
            for field_id in [
                *((dimension_refs and normalized_dimensions) or []),
                *where_by_id,
            ]:
                if "." in field_id:
                    required_entity_refs.add(_field_details(catalog, field_id).entity.id)
            _, _, dataset_irs, dataset_fns = _entity_adapter_maps(
                catalog=catalog, resolver=resolver, entity_refs=required_entity_refs
            )
            plan = plan_base_observe(
                catalog=catalog,
                session=session,
                metric_ir=metric_ir,
                dataset_irs=dataset_irs,
                dataset_fns=dataset_fns,
                dimensions=dimension_refs,
                where=where_by_id,
                resolved_window=resolved_window,
                time_dimension=(
                    resolved_window.time_dimension
                    if resolved_window is not None
                    else time_dimension_id
                ),
            )
            model_name, metric_name = metric_id.split(".", 1)
            planned.append(
                _PlannedMetric(
                    metric_id=metric_id,
                    metric_name=metric_name,
                    model_name=model_name,
                    column=columns_by_metric[metric_id],
                    metric_ir=metric_ir,
                    plan=plan,
                )
            )

        groups: dict[tuple[str, str], list[_PlannedMetric]] = {}
        for pm in planned:
            groups.setdefault(_fusion_key(pm.plan), []).append(pm)

        params_timescope = None
        if resolved_window is not None:
            params_timescope = {
                "original": original_timescope,
                "resolved": dump_window(resolved_window),
                "report_tz": session.report_tz_name,
            }
        fusion_groups = [[pm.metric_id for pm in group] for group in groups.values()]
        params: dict[str, Any] = {
            "metrics": metric_ids,
            "timescope": params_timescope,
            "dimensions": _dump_dimensions(dimension_refs),
            "where": stored_where,
            "fusion": fusion_groups,
            "per_metric": {
                pm.metric_id: {
                    "relationships": pm.plan.lineage_metadata.get("relationships") or [],
                    "version_resolutions": pm.plan.lineage_metadata.get("version_resolutions")
                    or [],
                    "fanout_policy": pm.plan.lineage_metadata.get("fanout_policy"),
                    "fanouts": pm.plan.lineage_metadata.get("fanouts") or [],
                }
                for pm in planned
            },
            "metric_semantics": {
                pm.metric_id: _metric_semantics_payload(pm.metric_ir)
                for pm in sorted(planned, key=lambda item: item.metric_id)
            },
            "warnings": [w for pm in planned for w in pm.plan.warnings],
        }
        models = list(dict.fromkeys(pm.model_name for pm in planned))
        prospective_id = compute_prospective_artifact_id(
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors(
                values={"metrics": metric_ids, "models": models}
            ),
        )
        if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
            session._connection_runtime.take_captured_queries()
            return cast("MetricFrame", load_frame(prospective_id, session=session))

        group_results = []
        axes: dict[str, Any] = {}
        semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "scalar"
        for group in groups.values():
            result, group_axes, semantic_kind = _execute_fused_group(
                group,
                catalog=catalog,
                resolver=resolver,
                session=session,
                resolved_window=resolved_window,
            )
            axes = axes or group_axes
            group_results.append(result)
    except BaseException:
        session._connection_runtime.take_captured_queries()
        raise
    _captured_queries = session._connection_runtime.take_captured_queries()
    finished_at = datetime.now(UTC)

    axis_columns = (["bucket_start"] if is_time_series else []) + [
        d.field.name for d in planned[0].plan.dimensions
    ]
    ordered_value_columns = [columns_by_metric[m] for m in metric_ids]
    dfs = [r.df for r in group_results]
    if axis_columns:
        merged = functools.reduce(
            lambda left, right: left.merge(right, on=axis_columns, how="outer"), dfs
        )
        merged = merged.sort_values(axis_columns).reset_index(drop=True)
    else:
        merged = pd.concat([df.reset_index(drop=True) for df in dfs], axis=1)
    merged = merged[[*axis_columns, *ordered_value_columns]]

    measures = [
        {
            "metric_id": pm.metric_id,
            "name": pm.metric_name,
            "column": pm.column,
            "unit": pm.metric_ir.unit,
            "additivity": _meta_additivity(pm.metric_ir.additivity),
            "aggregation": _meta_aggregation(pm.metric_ir.aggregation),
            "status_time_dimension": pm.metric_ir.status_time_dimension,
            "reaggregatable": True,
        }
        for pm in planned
    ]
    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        analysis_purpose=analysis_purpose,
        created_at=finished_at,
        row_count=len(merged),
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="observe",
                    job_ref=job_ref,
                    inputs=[],
                    params_digest=_params_digest(params),
                    analysis_purpose=analysis_purpose,
                    params=params,
                )
            ]
        ),
        metric_id=None,
        axes=axes,
        measure={},
        measures=measures,
        window=dump_window(resolved_window),
        where=stored_where,
        semantic_kind=semantic_kind,
        semantic_model=planned[0].model_name,
        unit=None,
        reaggregatable=all(m["reaggregatable"] for m in measures),
        additivity=None,
    )
    frame = MetricFrame(_df=merged, meta=meta)
    frame = _commit_observe_metric_frame(
        session=session,
        frame=frame,
        params=params,
        metric_id=None,
        model_name=planned[0].model_name,
        stored_where=stored_where,
        semantic_kind=semantic_kind,
        subject_grain=(
            resolved_window.grain.to_token()
            if resolved_window is not None and resolved_window.grain is not None
            else None
        ),
        metric_ids=metric_ids,
        models=models,
    )
    _output_ref = frame.meta.artifact_id or frame.ref
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "observe",
            "analysis_purpose": analysis_purpose,
            "params": params,
            "input_frame_refs": [],
            "output_frame_ref": _output_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog.semantic_root),
            "semantic_model": planned[0].model_name,
            "queries": [{**qe.to_dict(), "output_ref": _output_ref} for qe in _captured_queries],
        },
    )
    return frame
