"""Discover candidate follow-ups from committed analysis artifacts."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from numbers import Real
from time import monotonic
from typing import Any, cast

import numpy as np

from marivo.analysis_py.errors import SemanticKindMismatchError
from marivo.analysis_py.frames.candidate import (
    CandidateObjective,
    CandidateSet,
    CandidateSetMeta,
    CandidateShape,
    CandidateSourceKind,
    CandidateStrategy,
)
from marivo.analysis_py.frames.delta import DeltaFrame
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.intents._candidate_columns import (
    build_union_columns,
    validate_shape_columns,
)
from marivo.analysis_py.intents._derived import (
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis_py.intents._discover_scorers import (
    score_cross_sectional_outliers,
    score_driver_axes,
    score_interesting_slices,
    score_interesting_windows,
    score_period_shifts,
    score_point_anomalies,
)
from marivo.analysis_py.lineage import LineageStep
from marivo.analysis_py.refs import DimensionRef
from marivo.analysis_py.session.core import Session, ensure_session_writable
from marivo.analysis_py.session.persistence import write_frame_to_disk, write_job_record

_DEFAULT_STRATEGY: dict[CandidateObjective, CandidateStrategy] = {
    "point_anomalies": "zscore",
    "period_shifts": "delta_window_zscore",
    "driver_axes": "variance_explained",
    "interesting_slices": "delta_magnitude",
    "interesting_windows": "rolling_zscore",
    "cross_sectional_outliers": "mad",
}

_OBJECTIVE_TO_SHAPE: dict[CandidateObjective, CandidateShape] = {
    "point_anomalies": "point_anomaly",
    "period_shifts": "period_shift",
    "driver_axes": "driver_axis",
    "interesting_slices": "slice",
    "interesting_windows": "window",
    "cross_sectional_outliers": "cross_sectional_outlier",
}

_VALID_OBJECTIVES = set(_OBJECTIVE_TO_SHAPE.keys())


def discover(
    source: MetricFrame | DeltaFrame,
    *,
    objective: CandidateObjective,
    strategy: CandidateStrategy | None = None,
    value: str | None = None,
    threshold: float | None = None,
    sensitivity: str = "balanced",
    limit: int | None = None,
    search_space: list[DimensionRef] | None = None,
    peer_scope: list[DimensionRef] | None = None,
    session: Session | None = None,
) -> CandidateSet:
    session = resolve_session(session)
    ensure_session_writable(session)

    if not isinstance(source, MetricFrame | DeltaFrame):
        raise SemanticKindMismatchError(
            message="discover requires a MetricFrame or DeltaFrame input",
            details={
                "expected_kind": "metric_frame|delta_frame",
                "got_kind": type(source).__name__,
            },
        )
    ensure_frame_in_session(source, session=session, label="discover source")

    if objective not in _VALID_OBJECTIVES:
        raise SemanticKindMismatchError(
            message=f"unsupported discover objective {objective!r}",
            details={
                "expected_kind": "|".join(sorted(_VALID_OBJECTIVES)),
                "got_kind": str(objective),
            },
        )

    source_kind: CandidateSourceKind = (
        "metric_frame" if isinstance(source, MetricFrame) else "delta_frame"
    )
    _check_objective_compatibility(objective, source_kind, source.meta.semantic_kind)

    resolved_strategy = _resolve_strategy(objective, strategy)
    shape = _OBJECTIVE_TO_SHAPE[objective]

    started_at = datetime.now(UTC)
    started = monotonic()
    rows, params = _run_scorer(
        objective=objective,
        source=source,
        source_kind=source_kind,
        value=value,
        threshold=threshold,
        sensitivity=sensitivity,
        limit=limit,
        search_space=search_space,
        peer_scope=peer_scope,
    )
    df = build_union_columns(shape, rows)
    validate_shape_columns(shape, df)

    full_params: dict[str, Any] = {
        "source_ref": source.ref,
        "objective": objective,
        "strategy": resolved_strategy,
        **params,
    }

    frame_ref = gen_ref("frame")
    job_ref = gen_ref("job")
    finished_at = datetime.now(UTC)
    meta = CandidateSetMeta(
        kind="candidate_set",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        created_at=finished_at,
        row_count=len(df),
        byte_size=0,
        lineage=compose_lineage(
            [source],
            step=LineageStep(
                intent="discover",
                job_ref=job_ref,
                inputs=[source.ref],
                params_digest=params_digest(full_params),
            ),
        ),
        shape=shape,
        objective=objective,
        strategy=resolved_strategy,
        source_ref=source.ref,
        source_kind=source_kind,
        metric_ids=[source.meta.metric_id],
        semantic_kind=source.meta.semantic_kind,
        semantic_model=source.meta.semantic_model,
        source_refs=[source.ref],
        params=full_params,
    )
    frame = CandidateSet(_df=df, meta=meta)
    frame.meta = cast("CandidateSetMeta", write_frame_to_disk(session.layout, frame))
    write_job_record(
        session.layout,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "discover",
            "params": full_params,
            "input_frame_refs": [source.ref],
            "output_frame_ref": frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": session.semantic_project.root,
            "semantic_model": source.meta.semantic_model,
        },
    )
    return frame


def _resolve_strategy(
    objective: CandidateObjective, strategy: CandidateStrategy | None
) -> CandidateStrategy:
    default = _DEFAULT_STRATEGY[objective]
    if strategy is None or strategy == default:
        return default
    raise SemanticKindMismatchError(
        message=f"unsupported discover strategy {strategy!r}",
        details={"expected_kind": default, "got_kind": str(strategy)},
    )


def _check_objective_compatibility(
    objective: CandidateObjective,
    source_kind: CandidateSourceKind,
    semantic_kind: str,
) -> None:
    table: dict[CandidateObjective, dict[str, set[str]]] = {
        "point_anomalies": {"metric_frame": {"time_series", "panel"}},
        "period_shifts": {"delta_frame": {"time_series", "panel"}},
        "driver_axes": {"delta_frame": {"scalar", "time_series", "segmented", "panel"}},
        "interesting_slices": {
            "metric_frame": {"scalar", "time_series", "segmented", "panel"},
            "delta_frame": {"scalar", "time_series", "segmented", "panel"},
        },
        "interesting_windows": {
            "metric_frame": {"time_series", "panel"},
            "delta_frame": {"time_series", "panel"},
        },
        "cross_sectional_outliers": {"metric_frame": {"segmented", "panel"}},
    }
    allowed_kinds = table[objective].get(source_kind)
    if allowed_kinds is None:
        raise SemanticKindMismatchError(
            message=(
                f"discover objective {objective!r} does not accept source kind {source_kind!r}"
            ),
            details={
                "objective": objective,
                "source_kind": source_kind,
                "expected_kind": "|".join(sorted(table[objective].keys())),
            },
        )
    if semantic_kind not in allowed_kinds:
        raise SemanticKindMismatchError(
            message=(
                f"discover objective {objective!r} does not accept "
                f"semantic_kind {semantic_kind!r} on a {source_kind}"
            ),
            details={
                "objective": objective,
                "source_kind": source_kind,
                "semantic_kind": semantic_kind,
                "expected_kind": "|".join(sorted(allowed_kinds)),
            },
        )


def _run_scorer(
    *,
    objective: CandidateObjective,
    source: MetricFrame | DeltaFrame,
    source_kind: CandidateSourceKind,
    value: str | None,
    threshold: float | None,
    sensitivity: str,
    limit: int | None,
    search_space: list[DimensionRef] | None,
    peer_scope: list[DimensionRef] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if objective == "point_anomalies":
        threshold_value = _validate_threshold(3.0 if threshold is None else threshold)
        df = source.to_pandas()
        value_column = require_numeric_column(df, value, purpose="discover")
        rows = score_point_anomalies(
            df,
            source_ref=source.ref,
            value_column=value_column,
            threshold=threshold_value,
        )
        params = {"value": value, "threshold": threshold_value}
        return rows, params

    if objective == "period_shifts":
        threshold_value = _validate_threshold(2.0 if threshold is None else threshold)
        df = source.to_pandas()
        bucket_column, group_columns = _delta_axes(cast("DeltaFrame", source))
        value_column = require_numeric_column(
            df.drop(columns=[bucket_column, *group_columns]), value, purpose="discover"
        )
        rows = score_period_shifts(
            df,
            source_ref=source.ref,
            bucket_column=bucket_column,
            value_column=value_column,
            threshold=threshold_value,
            group_columns=group_columns,
        )
        params = {"value": value, "threshold": threshold_value}
        return rows, params

    if objective == "driver_axes":
        if not search_space:
            raise SemanticKindMismatchError(
                message="discover(driver_axes) requires a non-empty search_space",
                details={"objective": objective, "missing": "search_space"},
            )
        df = source.to_pandas()
        bucket_column, _ = _delta_axes(cast("DeltaFrame", source))
        value_column = require_numeric_column(
            df.drop(columns=[c for c in [bucket_column] if c in df.columns]),
            value,
            purpose="discover",
        )
        axes = [ref.id for ref in search_space]
        rows = score_driver_axes(
            df,
            source_ref=source.ref,
            value_column=value_column,
            axes=axes,
            bucket_column=bucket_column if bucket_column in df.columns else None,
            limit=limit,
        )
        driver_params: dict[str, Any] = {
            "value": value,
            "search_space": axes,
            "limit": limit,
        }
        return rows, driver_params

    if objective == "interesting_slices":
        threshold_value = _validate_threshold(2.0 if threshold is None else threshold)
        df = source.to_pandas()
        if isinstance(source, DeltaFrame):
            bucket_column, dim_columns = _delta_axes(source)
            measure_kind = "delta"
            non_value_columns = [bucket_column, *dim_columns]
        else:
            measure_kind = "metric"
            from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

            non_value_columns = [col for col in df.columns if is_datetime64_any_dtype(df[col])]
            dim_columns = [
                col
                for col in df.columns
                if col not in non_value_columns and not is_numeric_dtype(df[col])
            ]
        value_column = require_numeric_column(
            df.drop(columns=[c for c in non_value_columns if c in df.columns]),
            value,
            purpose="discover",
        )
        axes = [ref.id for ref in (search_space or [])] or dim_columns
        rows = score_interesting_slices(
            df,
            source_ref=source.ref,
            value_column=value_column,
            axes=axes,
            threshold=threshold_value,
            measure_kind=measure_kind,
            limit=limit,
        )
        slice_params: dict[str, Any] = {
            "value": value,
            "threshold": threshold_value,
            "search_space": axes,
            "limit": limit,
        }
        return rows, slice_params

    if objective == "interesting_windows":
        threshold_value = _validate_threshold(2.0 if threshold is None else threshold)
        df = source.to_pandas()
        if isinstance(source, DeltaFrame):
            bucket_column, group_columns = _delta_axes(source)
        else:
            from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

            time_columns = [c for c in df.columns if is_datetime64_any_dtype(df[c])]
            if not time_columns:
                raise SemanticKindMismatchError(
                    message="interesting_windows requires a time bucket column",
                    details={
                        "objective": objective,
                        "expected_kind": "time_series|panel",
                    },
                )
            bucket_column = time_columns[0]
            group_columns = [
                c for c in df.columns if c != bucket_column and not is_numeric_dtype(df[c])
            ]
        value_column = require_numeric_column(
            df.drop(columns=[c for c in [bucket_column, *group_columns] if c in df.columns]),
            value,
            purpose="discover",
        )
        rows = score_interesting_windows(
            df,
            source_ref=source.ref,
            bucket_column=bucket_column,
            value_column=value_column,
            threshold=threshold_value,
            group_columns=group_columns,
        )
        params = {"value": value, "threshold": threshold_value}
        return rows, params

    if objective == "cross_sectional_outliers":
        threshold_value = _validate_threshold(3.0 if threshold is None else threshold)
        df = source.to_pandas()
        from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

        time_columns = [c for c in df.columns if is_datetime64_any_dtype(df[c])]
        bucket_col: str | None = time_columns[0] if time_columns else None
        segment_columns = [
            c for c in df.columns if c not in time_columns and not is_numeric_dtype(df[c])
        ]
        value_column = require_numeric_column(
            df.drop(columns=[c for c in [bucket_col, *segment_columns] if c]),
            value,
            purpose="discover",
        )
        peer_axes = [ref.id for ref in (peer_scope or [])]
        rows = score_cross_sectional_outliers(
            df,
            source_ref=source.ref,
            value_column=value_column,
            segment_columns=segment_columns,
            bucket_column=bucket_col,
            threshold=threshold_value,
            peer_scope=peer_axes,
        )
        outlier_params: dict[str, Any] = {
            "value": value,
            "threshold": threshold_value,
            "peer_scope": peer_axes,
        }
        return rows, outlier_params

    raise SemanticKindMismatchError(
        message=f"discover objective {objective!r} is not implemented",
        details={
            "expected_kind": "implemented_objective",
            "objective": objective,
        },
    )


def _validate_threshold(threshold: float) -> float:
    if isinstance(threshold, bool) or not isinstance(threshold, Real):
        raise SemanticKindMismatchError(
            message="discover threshold must be a positive finite number"
        )
    threshold_value = float(threshold)
    if not np.isfinite(threshold_value) or threshold_value <= 0:
        raise SemanticKindMismatchError(
            message="discover threshold must be a positive finite number"
        )
    return threshold_value


def _delta_axes(source: DeltaFrame) -> tuple[str, list[str]]:
    """Return (bucket_column, dimension_columns) for a DeltaFrame.

    Falls back to the first datetime column + auto-detected non-numeric
    columns when alignment metadata is incomplete; real compare-produced
    frames already populate alignment.axes correctly.
    """

    from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

    df = source.to_pandas()
    df_columns = list(df.columns)
    axes = source.meta.alignment.get("axes")
    bucket_column = "bucket_start"
    dim_columns: list[str] = []
    if isinstance(axes, dict):
        for axis in axes.values():
            if not isinstance(axis, dict):
                continue
            column = axis.get("column")
            if not isinstance(column, str) or not column:
                continue
            if axis.get("role") == "time":
                bucket_column = column
            elif axis.get("role") == "dimension":
                dim_columns.append(column)
    if bucket_column not in df_columns:
        for col in df_columns:
            if is_datetime64_any_dtype(df[col]):
                bucket_column = col
                break
    if not dim_columns:
        dim_columns = [
            col
            for col in df_columns
            if col != bucket_column
            and not is_numeric_dtype(df[col])
            and not is_datetime64_any_dtype(df[col])
        ]
    return bucket_column, sorted(dim_columns)
