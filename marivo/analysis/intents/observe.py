"""Materialize a semantic metric into a MetricFrame."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from typing import Any, cast

from marivo.analysis.errors import (
    AnalysisError,
    MetricNotFoundError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    compute_prospective_artifact_id,
    frame_exists_on_disk,
)
from marivo.analysis.executor.runner import (
    normalize_slice_for_storage,
)
from marivo.analysis.executor.windowing import (
    datasource_engine_profile,
)
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._observe_base import (  # noqa: F401
    _execute_base,
    _execute_sampled_base,
    _expression_source_columns,
    _prune_base_observe_projection,
    _resolve_fold_time_field,
    _time_dependency_exprs,
)
from marivo.analysis.intents._observe_catalog import (  # noqa: F401
    _build_entity_adapter,
    _catalog_id,
    _catalog_kind,
    _catalog_object,
    _DimensionIRAdapter,
    _entity_details,
    _EntityIRAdapter,
    _field_details,
    _fields_for_entity,
    _TimeFieldMetaAdapter,
)
from marivo.analysis.intents._observe_components import (  # noqa: F401
    _COMPONENT_AWARE_COMPOSITIONS,
    _add_fold_metadata_to_component_df,
    _component_frame_df,
    _component_parent_columns,
    _composition_payload,
    _evaluate_composition_on_frame,
    _is_component_aware_composition,
    _require_component_role_column,
    _role_to_column_name,
)
from marivo.analysis.intents._observe_cumulative import (  # noqa: F401
    _MAX_TRAILING_DISTINCT_EXPANSION,
    _apply_where_to_raw_table,
    _base_aggregation_name,
    _base_measure_ref,
    _count_distinct_key_expr,
    _execute_cumulative,
    _execute_trailing_additive,
    _execute_trailing_distinct,
)
from marivo.analysis.intents._observe_dense import (  # noqa: F401
    _FIXED_GRAINS,
    _GRAIN_PANDAS_FREQ,
    _align_to_grain_start,
    _bucket_date_range,
    _dense_cumulative_frame,
    _fixed_grain_seconds_for_coverage,
    _grain_to_date_dense_frame,
    _require_grain_to_date_compat,
    _trailing_coverage_df,
    _trailing_rolling_frame,
    _trunc_series_to_grain,
)
from marivo.analysis.intents._observe_derived import (  # noqa: F401
    _build_derived_fold_meta,
    _build_fold_meta,
    _cumulative_marker_for_plan,
    _derived_cumulative_marker,
    _execute_derived,
    _execute_folded_component,
    _merge_component_coverages,
)
from marivo.analysis.intents._observe_inputs import (  # noqa: F401
    _analysis_axis_for_kind,
    _backend_for_datasource,
    _dump_dimensions,
    _entity_adapter_maps,
    _gen_ref,
    _metric_expr,
    _metric_planner_scope,
    _normalize_dimension_boundary,
    _normalize_dimension_list_boundary,
    _normalize_metric_boundary,
    _normalize_where_boundary,
    _params_digest,
    _resolve_timescope,
    _Result,
    _validate_dimension_ids,
)
from marivo.analysis.intents._observe_persist import (
    _attach_metric_component_ref,
    _commit_observe_metric_frame,
    _meta_additivity,
    _meta_aggregation,
    _metric_semantics_payload,
    _persist_and_attach_coverage_sidecar,
    _persist_metric_component_frame,
)
from marivo.analysis.intents._observe_planner_fields import _all_entity_ids
from marivo.analysis.intents._shape import SemanticShape, observe_output_shape
from marivo.analysis.intents._types import SliceValue
from marivo.analysis.intents.observe_planner import (
    CumulativeObservePlan,
    DerivedObservePlan,
    _planned_metric,
    plan_base_observe,
    plan_observe,
)
from marivo.analysis.intents.sampled_fold import (
    quantile_capability,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.semantic_inputs import (
    DimensionInput,
    MetricInput,
)
from marivo.analysis.session._load import load_frame
from marivo.analysis.session._runtime import (
    persist_job_record,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.windows.spec import (
    GrainInput,
    TimeScopeInput,
    dump_window,
)
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    SemanticKind,
    SimpleMetricDetails,
)

# Symbols that remain importable from this module for ``observe_multi`` /
# ``derive`` / ``transform`` / ``frames._metric_projection`` / tests after
# extraction into private submodules. ``__all__`` also satisfies mypy's
# ``no_implicit_reexport``.
__all__ = [
    "_analysis_axis_for_kind",
    "_build_entity_adapter",
    "_catalog_object",
    "_commit_observe_metric_frame",
    "_dump_dimensions",
    "_entity_adapter_maps",
    "_entity_details",
    "_evaluate_composition_on_frame",
    "_field_details",
    "_gen_ref",
    "_meta_additivity",
    "_meta_aggregation",
    "_metric_expr",
    "_metric_planner_scope",
    "_normalize_dimension_boundary",
    "_normalize_dimension_list_boundary",
    "_normalize_where_boundary",
    "_params_digest",
    "_persist_and_attach_coverage_sidecar",
    "_resolve_timescope",
    "_validate_dimension_ids",
    "observe",
]
# attributes like ``fn``, ``fields``, ``is_time``, and ``time_meta``. These
# adapters are intentionally narrow: they are built from catalog details and
# call resolver.dimension_on(...), never SemanticProject sidecar callables.


# ---------------------------------------------------------------------------
# Observe intent
# ---------------------------------------------------------------------------


def observe(
    metric: MetricInput | list[MetricInput] | tuple[MetricInput, ...],
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
    if isinstance(metric, (list, tuple)):
        metric_items: list[MetricInput] = list(metric)
        if not metric_items:
            raise SemanticKindMismatchError(
                message="observe requires at least one metric",
                context={"argument": "metric", "got": "empty sequence"},
            )
        if len(metric_items) > 1:
            from marivo.analysis.intents.observe_multi import observe_multi

            return observe_multi(
                metric_items,
                time_scope=time_scope,
                grain=grain,
                dimensions=dimensions,
                slice_by=slice_by,
                time_dimension=time_dimension,
                expect_shape=expect_shape,
                analysis_purpose=analysis_purpose,
                session=session,
            )
        single_metric: MetricInput = metric_items[0]
    else:
        single_metric = metric
    if session is None:
        session = require_current_session()
    ensure_session_writable(session)
    catalog = session.catalog
    catalog._require_index()
    metric_id = _normalize_metric_boundary(catalog, single_metric)
    model_name, metric_name = metric_id.split(".", 1)
    metric_details = _catalog_object(catalog, metric_id, SemanticKind.METRIC).details()
    assert isinstance(metric_details, (SimpleMetricDetails, DerivedMetricDetails))
    metric_ir = _planned_metric(metric_details)
    planner_scope = _metric_planner_scope(catalog, metric_ir)
    time_dimension_id = (
        _normalize_dimension_boundary(
            catalog,
            time_dimension,
            argument="time_dimension",
            scoped_entity_refs=planner_scope,
        )
        if time_dimension is not None
        else None
    )
    where_by_id = _normalize_where_boundary(catalog, slice_by, scoped_entity_refs=planner_scope)
    dimension_ids = _normalize_dimension_list_boundary(
        catalog,
        dimensions,
        scoped_entity_refs=planner_scope,
    )
    resolver = catalog._resolver(connections=session._connection_runtime)
    resolved_window, original_timescope = _resolve_timescope(
        time_scope,
        grain=grain,
        time_dimension=time_dimension_id,
    )
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    # For semi-additive metrics, inject status_time_dimension into the window if
    # not already specified so downstream resolution picks the status axis.
    if (
        getattr(metric_ir, "additivity", None) == "semi_additive"
        and metric_ir.status_time_dimension is not None
        and time_dimension_id is None
        and resolved_window is not None
        and resolved_window.time_dimension is None
    ):
        resolved_window, original_timescope = _resolve_timescope(
            time_scope,
            grain=grain,
            time_dimension=metric_ir.status_time_dimension,
        )

    # For derived metrics with semi-additive components, inject the first
    # component's status_time_dimension so the planner resolves the status axis.
    if (
        metric_ir.metric_type == "derived"
        and time_dimension_id is None
        and resolved_window is not None
        and resolved_window.time_dimension is None
    ):
        for _role, _comp_id in metric_ir.composition.components.items():
            _comp_details = _catalog_object(catalog, _comp_id, SemanticKind.METRIC).details()
            assert isinstance(_comp_details, (SimpleMetricDetails, DerivedMetricDetails))
            _comp_ir = _planned_metric(_comp_details)
            if (
                getattr(_comp_ir, "additivity", None) == "semi_additive"
                and getattr(_comp_ir, "status_time_dimension", None) is not None
            ):
                resolved_window, original_timescope = _resolve_timescope(
                    time_scope,
                    grain=grain,
                    time_dimension=_comp_ir.status_time_dimension,
                )
                break

    planner_time_dimension_id = (
        resolved_window.time_dimension if resolved_window is not None else time_dimension_id
    )

    started_at = datetime.now(UTC)
    started = monotonic()
    primary_datasource: str | None = None
    stored_where = normalize_slice_for_storage(where_by_id)
    metric_datasets = tuple(metric_ir.entities)
    dimension_refs = _validate_dimension_ids(dimension_ids)
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
    if metric_ir.metric_type == "derived":
        # Build adapters for all catalog entities so derived components can plan
        # across any entity they reference.
        all_entity_refs = _all_entity_ids(catalog)
        _, _, all_dataset_irs, all_dataset_fns = _entity_adapter_maps(
            catalog=catalog,
            resolver=resolver,
            entity_refs=all_entity_refs,
        )
        component_metric_irs = {
            component_id: _planned_metric(component_details)
            for component_id in metric_ir.composition.components.values()
            if isinstance(
                component_details := _catalog_object(
                    catalog, component_id, SemanticKind.METRIC
                ).details(),
                (SimpleMetricDetails, DerivedMetricDetails),
            )
        }

        session._connection_runtime.begin_query_capture()
        try:
            derived_plan = plan_observe(
                catalog=catalog,
                session=session,
                metric_ir=metric_ir,
                dataset_irs=all_dataset_irs,
                dataset_fns=all_dataset_fns,
                dimensions=dimension_refs,
                where=where_by_id,
                resolved_window=resolved_window,
                time_dimension=planner_time_dimension_id,
                component_metric_irs=component_metric_irs,
            )
            # plan_observe returns DerivedObservePlan for ratio/weighted/linear
            # derived metrics, or CumulativeObservePlan for cumulative metrics.
            assert isinstance(derived_plan, (DerivedObservePlan, CumulativeObservePlan))
            if isinstance(derived_plan, CumulativeObservePlan):
                # --- Cumulative observe execution ---
                # Resolve the real 'over' and 'anchor' from the MetricIR in
                # the registry, since _MetricDetailsAdapter.composition defaults
                # over=None and anchor='all_history'.
                cum_over = derived_plan.over
                cum_anchor: Any = "all_history"
                registry = catalog._require_index().registry
                real_ir = registry.metrics.get(metric_id)
                if real_ir is not None and real_ir.composition is not None:
                    if cum_over is None:
                        cum_over = getattr(real_ir.composition, "over", None)
                    cum_anchor = (
                        getattr(real_ir.composition, "anchor", "all_history") or "all_history"
                    )
                # Prefer the plan's resolved composition (carries the real
                # anchor even when the registry is not attached).
                plan_composition = getattr(derived_plan, "composition", None)
                if plan_composition is not None:
                    cum_anchor = getattr(plan_composition, "anchor", cum_anchor) or cum_anchor
                cumulative_meta = {
                    "kind": "cumulative",
                    "base": derived_plan.base_metric_ir.semantic_id,
                    "over": cum_over,
                    "anchor": cum_anchor,
                    "components": None,
                }
                params_timescope_cum = None
                if resolved_window is not None:
                    params_timescope_cum = {
                        "original": original_timescope,
                        "resolved": dump_window(resolved_window),
                        "report_tz": session.report_tz_name,
                    }
                params = {
                    "metric": metric_id,
                    "timescope": params_timescope_cum,
                    "dimensions": _dump_dimensions(dimension_refs),
                    "where": stored_where,
                    "version_resolutions": derived_plan.base_plan.lineage_metadata.get(
                        "version_resolutions", []
                    ),
                    "warnings": derived_plan.warnings,
                    "lineage_metadata": derived_plan.base_plan.lineage_metadata,
                    "cumulative": {
                        "base": derived_plan.base_metric_ir.semantic_id,
                        "over": cum_over,
                        "anchor": cum_anchor,
                        "spine_synthesized": bool(resolved_window and resolved_window.grain),
                        "query_strategy": (
                            "baseline_plus_flow"
                            if resolved_window and resolved_window.grain
                            else "as_of_end"
                        ),
                    },
                    "metric_semantics": _metric_semantics_payload(
                        metric_ir,
                        force_additivity="non_additive",
                    ),
                }
                prospective_id = compute_prospective_artifact_id(
                    step_type="observe",
                    inputs=CommitInputs(input_refs=[]),
                    params=CommitParams(values=params),
                    semantic_anchors=CommitSemanticAnchors(
                        values={"metric_id": metric_id, "model": model_name}
                    ),
                )
                if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
                    session._connection_runtime.take_captured_queries()
                    return cast("MetricFrame", load_frame(prospective_id, session=session))

                cum_result, cum_axes, cum_kind, cum_coverage_df = _execute_cumulative(
                    derived_plan,
                    catalog=catalog,
                    resolver=resolver,
                    session=session,
                    resolved_window=resolved_window,
                )
            else:
                # Build params and check cache before executing the backend query.
                params_timescope = None
                if resolved_window is not None:
                    params_timescope = {
                        "original": original_timescope,
                        "resolved": dump_window(resolved_window),
                        "report_tz": session.report_tz_name,
                    }
                params = {
                    "metric": metric_id,
                    "timescope": params_timescope,
                    "dimensions": _dump_dimensions(dimension_refs),
                    "where": stored_where,
                    "version_resolutions": [
                        vr
                        for cp in derived_plan.component_plans
                        for vr in cp.base_plan.lineage_metadata.get("version_resolutions", [])
                    ],
                    "warnings": derived_plan.warnings,
                    "lineage_metadata": derived_plan.lineage_metadata,
                    "metric_semantics": _metric_semantics_payload(metric_ir),
                }
                prospective_id = compute_prospective_artifact_id(
                    step_type="observe",
                    inputs=CommitInputs(input_refs=[]),
                    params=CommitParams(values=params),
                    semantic_anchors=CommitSemanticAnchors(
                        values={"metric_id": metric_id, "model": model_name}
                    ),
                )
                if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
                    session._connection_runtime.take_captured_queries()
                    return cast("MetricFrame", load_frame(prospective_id, session=session))

                result, component_df, derived_axes, derived_kind, derived_coverage_df = (
                    _execute_derived(
                        derived_plan,
                        metric_ir,
                        catalog=catalog,
                        resolver=resolver,
                        session=session,
                        resolved_window=resolved_window,
                    )
                )
        except BaseException:
            session._connection_runtime.take_captured_queries()
            raise
        _captured_queries = session._connection_runtime.take_captured_queries()
        finished_at = datetime.now(UTC)
        frame_ref = _gen_ref("frame")
        job_ref = _gen_ref("job")
        if isinstance(derived_plan, CumulativeObservePlan):
            # Record captured query executions on the cumulative params for
            # observability (mirrors the derived path). These do not affect
            # the prospective cache id, which was computed before execution.
            params["cumulative"]["queries"] = (
                [qe.to_dict() for qe in _captured_queries] if _captured_queries else []
            )
            meta = MetricFrameMeta(
                kind="metric_frame",
                ref=frame_ref,
                session_id=session.id,
                project_root=str(session.project_root),
                produced_by_job=job_ref,
                analysis_purpose=analysis_purpose,
                created_at=finished_at,
                row_count=cum_result.row_count,
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
                metric_id=metric_id,
                axes=cum_axes,
                measure={"name": metric_name},
                window=dump_window(resolved_window),
                where=stored_where,
                semantic_kind=cum_kind,
                semantic_model=model_name,
                unit=metric_ir.unit,
                reaggregatable=False,
                additivity="non_additive",
                aggregation=_meta_aggregation(metric_ir.aggregation),
                status_time_dimension=metric_ir.status_time_dimension,
                cumulative=cumulative_meta,
                rollup_fold="last",
            )
            frame = MetricFrame(_df=cum_result.df, meta=meta)
            _grain_token = (
                resolved_window.grain.to_token()
                if resolved_window is not None and resolved_window.grain is not None
                else None
            )
            frame = _commit_observe_metric_frame(
                session=session,
                frame=frame,
                params=params,
                metric_id=metric_id,
                model_name=model_name,
                stored_where=stored_where,
                semantic_kind=cum_kind,
                subject_grain=_grain_token,
            )
            # Trailing produces a window-coverage sidecar (partial buckets where
            # the rolling span reaches before the data start). all_history and
            # grain_to_date do not produce a coverage sidecar here.
            if cum_coverage_df is not None:
                frame = _persist_and_attach_coverage_sidecar(
                    session=session,
                    df=cum_coverage_df,
                    parent=frame,
                    job_ref=job_ref,
                )
        else:
            # Determine fold metadata for derived metrics with folded components
            _any_folded = any(
                getattr(cp.component_metric_ir, "time_fold", None) is not None
                for cp in derived_plan.component_plans
            )
            _derived_fold: dict[str, Any] | None = None
            if _any_folded:
                _derived_fold = _build_derived_fold_meta(derived_plan, catalog)
            _derived_cumulative = _derived_cumulative_marker(derived_plan, catalog)
            meta = MetricFrameMeta(
                kind="metric_frame",
                ref=frame_ref,
                session_id=session.id,
                project_root=str(session.project_root),
                produced_by_job=job_ref,
                analysis_purpose=analysis_purpose,
                created_at=finished_at,
                row_count=result.row_count,
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
                metric_id=metric_id,
                axes=derived_axes,
                measure={"name": metric_name},
                window=dump_window(resolved_window),
                where=stored_where,
                semantic_kind=derived_kind,
                semantic_model=model_name,
                unit=metric_ir.unit,
                fold=_derived_fold,
                reaggregatable=not _any_folded and _derived_cumulative is None,
                additivity=_meta_additivity(metric_ir.additivity),
                aggregation=_meta_aggregation(metric_ir.aggregation),
                status_time_dimension=metric_ir.status_time_dimension,
                cumulative=_derived_cumulative,
            )
            frame = MetricFrame(_df=result.df, meta=meta)
            frame = _commit_observe_metric_frame(
                session=session,
                frame=frame,
                params=params,
                metric_id=metric_id,
                model_name=model_name,
                stored_where=stored_where,
                semantic_kind=derived_kind,
            )
            if component_df is not None:
                component = _persist_metric_component_frame(
                    session=session,
                    df=component_df,
                    parent=frame,
                    metric_ir=metric_ir,
                    axes=derived_axes,
                    semantic_kind=derived_kind,
                    job_ref=job_ref,
                )
                frame = _attach_metric_component_ref(
                    session=session,
                    parent=frame,
                    component=component,
                    metric_ir=metric_ir,
                )
            # --- Persist coverage sidecar for derived metrics with folded components ---
            if derived_coverage_df is not None:
                frame = _persist_and_attach_coverage_sidecar(
                    session=session,
                    df=derived_coverage_df,
                    parent=frame,
                    job_ref=job_ref,
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
                "semantic_model": model_name,
                "queries": [
                    {**qe.to_dict(), "output_ref": _output_ref} for qe in _captured_queries
                ],
            },
        )
        return frame

    # --- Base (non-derived) metric path: route through planner ---
    required_entity_refs = set(metric_datasets)
    for field_id in [*(dimension_refs or []), *where_by_id]:
        if "." in field_id:
            required_entity_refs.add(_field_details(catalog, field_id).entity.id)
    _entity_details_by_id, _dataset_tables, dataset_irs, dataset_fns = _entity_adapter_maps(
        catalog=catalog,
        resolver=resolver,
        entity_refs=required_entity_refs,
    )

    session._connection_runtime.begin_query_capture()
    try:
        plan = plan_base_observe(
            catalog=catalog,
            session=session,
            metric_ir=metric_ir,
            dataset_irs=dataset_irs,
            dataset_fns=dataset_fns,
            dimensions=dimension_refs,
            where=where_by_id,
            resolved_window=resolved_window,
            time_dimension=planner_time_dimension_id,
        )
        primary_datasource = plan.datasource_name

        if primary_datasource is None:
            raise MetricNotFoundError(message=f"metric '{metric_id}' references no datasets")

        # Build params and check cache before executing the backend query.
        params_timescope = None
        if resolved_window is not None:
            params_timescope = {
                "original": original_timescope,
                "resolved": dump_window(resolved_window),
                "report_tz": session.report_tz_name,
            }
        params = {
            "metric": metric_id,
            "timescope": params_timescope,
            "dimensions": _dump_dimensions(dimension_refs),
            "where": stored_where,
            "relationships": plan.lineage_metadata.get("relationships") or [],
            "version_resolutions": plan.lineage_metadata.get("version_resolutions") or [],
            "fanout_policy": plan.lineage_metadata.get("fanout_policy"),
            "fanouts": plan.lineage_metadata.get("fanouts") or [],
            "warnings": plan.warnings,
            "metric_semantics": _metric_semantics_payload(metric_ir),
        }
        prospective_id = compute_prospective_artifact_id(
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors(
                values={"metric_id": metric_id, "model": model_name}
            ),
        )
        if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
            session._connection_runtime.take_captured_queries()
            return cast("MetricFrame", load_frame(prospective_id, session=session))

        result, axes, semantic_kind, coverage_df = _execute_base(
            plan,
            metric_ir,
            catalog=catalog,
            resolver=resolver,
            session=session,
            dimensions=dimension_refs,
            resolved_window=resolved_window,
        )
    except BaseException:
        session._connection_runtime.take_captured_queries()
        raise
    _captured_queries = session._connection_runtime.take_captured_queries()
    finished_at = datetime.now(UTC)

    # Resolve quantile capability for percentile-folded metrics
    _capability = None
    _time_fold = getattr(metric_ir, "time_fold", None)
    if _time_fold is not None and _time_fold.kind == "percentile":
        if primary_datasource is None:
            raise AnalysisError(
                message="percentile sampled fold requires a primary datasource to resolve backend type.",
                context={"metric": metric_ir.semantic_id},
            )
        _profile = datasource_engine_profile(session._connection_runtime, primary_datasource)
        _capability = quantile_capability(_profile)
    quantile_mode = _capability.mode if _capability is not None else None
    quantile_method = _capability.method if _capability is not None else None

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
        row_count=result.row_count,
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
        metric_id=metric_id,
        axes=axes,
        measure={"name": metric_name},
        window=dump_window(resolved_window),
        where=stored_where,
        semantic_kind=semantic_kind,
        semantic_model=model_name,
        unit=metric_ir.unit,
        fold=_build_fold_meta(metric_ir, catalog) if metric_ir.time_fold is not None else None,
        reaggregatable=metric_ir.time_fold is None,
        additivity=_meta_additivity(metric_ir.additivity),
        aggregation=_meta_aggregation(metric_ir.aggregation),
        status_time_dimension=metric_ir.status_time_dimension,
        quantile_mode=quantile_mode,
        quantile_method=quantile_method,
    )
    frame = MetricFrame(_df=result.df, meta=meta)

    # --- Evidence pipeline: commit_result replaces write_frame_to_disk ---
    _grain_token = (
        resolved_window.grain.to_token()
        if resolved_window is not None and resolved_window.grain is not None
        else None
    )
    frame = _commit_observe_metric_frame(
        session=session,
        frame=frame,
        params=params,
        metric_id=metric_id,
        model_name=model_name,
        stored_where=stored_where,
        semantic_kind=semantic_kind,
        subject_grain=_grain_token,
    )

    # --- Persist coverage sidecar for sampled metrics ---
    if coverage_df is not None:
        frame = _persist_and_attach_coverage_sidecar(
            session=session,
            df=coverage_df,
            parent=frame,
            job_ref=job_ref,
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
            "semantic_model": model_name,
            "queries": [{**qe.to_dict(), "output_ref": _output_ref} for qe in _captured_queries],
        },
    )
    return frame
