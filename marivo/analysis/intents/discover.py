"""Discover candidate follow-ups from committed analysis artifacts."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from numbers import Real
from time import monotonic
from typing import Any, Literal, TypeGuard, cast

import numpy as np
import pandas as pd

from marivo.analysis.errors import DiscoverInsufficientDataError, SemanticKindMismatchError
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject, TriggeredByFollowup
from marivo.analysis.frames.candidate import (
    CandidateObjective,
    CandidateSet,
    CandidateSetMeta,
    CandidateShape,
    CandidateSourceKind,
    CandidateStrategy,
)
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._candidate_columns import (
    build_union_columns,
    validate_shape_columns,
)
from marivo.analysis.intents._derived import (
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis.intents._discover_scorers import (
    score_cross_sectional_outliers,
    score_driver_axes,
    score_interesting_slices,
    score_interesting_windows,
    score_period_shifts,
    score_point_anomalies,
)
from marivo.analysis.intents._types import DiscoverSensitivity
from marivo.analysis.lineage import LineageStep
from marivo.analysis.refs import DimensionRef
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.session.persistence import write_job_record

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

_OBJECTIVE_COMPATIBILITY: dict[CandidateObjective, dict[str, set[str]]] = {
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

_OBJECTIVE_REQUIRED_KWARGS: dict[CandidateObjective, tuple[str, ...]] = {
    "point_anomalies": (),
    "period_shifts": (),
    "driver_axes": ("search_space",),
    "interesting_slices": (),
    "interesting_windows": (),
    "cross_sectional_outliers": (),
}


def _is_valid_objective(objective: str) -> TypeGuard[CandidateObjective]:
    return objective in _VALID_OBJECTIVES


def _discover_dispatch(
    source: object,
    *,
    objective: CandidateObjective | str,
    strategy: CandidateStrategy | None = None,
    value: str | None = None,
    threshold: float | None = None,
    sensitivity: DiscoverSensitivity = "balanced",
    limit: int | None = None,
    search_space: list[DimensionRef] | None = None,
    peer_scope: list[DimensionRef] | None = None,
    session: Session | None = None,
    _triggered_by: TriggeredByFollowup | None = None,
) -> CandidateSet:
    """Discover candidate follow-ups (anomalies, drivers, outliers) from a frame.

    When to use: find anomalies, drivers, or outliers without a specific hypothesis.

    Each ``objective`` is compatible with specific source kinds and semantic
    kinds; mismatches raise ``SemanticKindMismatchError``. ``driver_axes``
    requires a non-empty ``search_space``.

    Args:
        source: A MetricFrame or DeltaFrame, depending on ``objective``.
        objective: One of ``point_anomalies``, ``period_shifts``, ``driver_axes``,
            ``interesting_slices``, ``interesting_windows``, ``cross_sectional_outliers``.
        strategy: Scoring strategy. Defaults are picked per objective
            (e.g. ``zscore`` for ``point_anomalies``).
        value: Numeric column to score. Defaults to the frame's measure column.
        threshold: Score cutoff. If omitted, all rows are returned (subject to ``limit``).
        sensitivity: ``"conservative" | "balanced" | "aggressive"``.
        limit: Maximum number of candidates to return.
        search_space: Required for ``driver_axes`` — dimensions to consider as drivers.
        peer_scope: Optional peer grouping for ``cross_sectional_outliers``.
        session: Defaults to the currently-attached session.

    Raises:
        SemanticKindMismatchError: Wrong source kind/semantic_kind for the objective,
            missing required kwargs (e.g. ``search_space`` for ``driver_axes``), or
            unsupported ``objective``.
        CrossSessionFrameError: ``source`` belongs to a different session.

    Example:
        >>> candidates = session.discover(
        ...     series,
        ...     objective="point_anomalies",
        ...     threshold=1.0,
        ... )
        >>> candidates.summary()
    """
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

    if not _is_valid_objective(objective):
        raise SemanticKindMismatchError(
            message=f"unsupported discover objective {objective!r}",
            details={
                "expected_kind": "|".join(sorted(_VALID_OBJECTIVES)),
                "got_kind": str(objective),
            },
        )
    discover_objective = objective

    source_kind: CandidateSourceKind = (
        "metric_frame" if isinstance(source, MetricFrame) else "delta_frame"
    )
    _check_objective_compatibility(discover_objective, source_kind, source.meta.semantic_kind)

    resolved_strategy = _resolve_strategy(discover_objective, strategy)
    shape = _OBJECTIVE_TO_SHAPE[discover_objective]

    started_at = datetime.now(UTC)
    started = monotonic()
    rows, params = _run_scorer(
        objective=discover_objective,
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
        "objective": discover_objective,
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
        objective=discover_objective,
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
    source_ref = source.meta.artifact_id or source.ref
    axis: Literal[
        "scalar",
        "time",
        "segment",
        "panel",
        "change",
        "decomposition",
        "correlation",
        "forecast",
        "anomaly",
    ] = (
        "time"
        if frame.meta.semantic_kind == "time_series"
        else "segment"
        if frame.meta.semantic_kind == "segmented"
        else "panel"
        if frame.meta.semantic_kind == "panel"
        else frame.meta.semantic_kind
    )
    observed_window = source.meta.window if hasattr(source.meta, "window") else None
    frame = cast(
        "CandidateSet",
        commit_result(
            store=session.evidence_store(),
            frames_dir=session.layout.frames_dir,
            frame=frame,
            step_type="discover",
            inputs=CommitInputs(input_refs=[source_ref]),
            params=CommitParams(values=full_params),
            semantic_anchors=CommitSemanticAnchors(
                values={"metric_id": getattr(source.meta, "metric_id", "")}
            ),
            subject=Subject(
                metric=getattr(source.meta, "metric_id", None),
                grain=getattr(source.meta, "grain", None),
                analysis_axis=axis,
            ),
            extractor_family="candidate_set",
            seeding_context={"observed_window": observed_window},
            triggered_by_followup=_triggered_by,
        ),
    )
    write_job_record(
        session.layout,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "discover",
            "params": full_params,
            "input_frame_refs": [source.ref],
            "output_frame_ref": frame.meta.artifact_id or frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.semantic_project.semantic_root),
            "semantic_model": source.meta.semantic_model,
        },
    )
    return frame


class DiscoverAPI:
    """Callable namespace for candidate discovery objectives."""

    def __call__(
        self,
        source: object,
        *,
        objective: CandidateObjective | str,
        strategy: CandidateStrategy | None = None,
        value: str | None = None,
        threshold: float | None = None,
        sensitivity: DiscoverSensitivity = "balanced",
        limit: int | None = None,
        search_space: list[DimensionRef] | None = None,
        peer_scope: list[DimensionRef] | None = None,
        session: Session | None = None,
        _triggered_by: TriggeredByFollowup | None = None,
    ) -> CandidateSet:
        """Discover candidate follow-ups. Prefer typed sub-methods (session.discover.point_anomalies, etc.) for precise signatures."""

        return _discover_dispatch(
            source,
            objective=objective,
            strategy=strategy,
            value=value,
            threshold=threshold,
            sensitivity=sensitivity,
            limit=limit,
            search_space=search_space,
            peer_scope=peer_scope,
            session=session,
            _triggered_by=_triggered_by,
        )

    def point_anomalies(
        self,
        source: MetricFrame,
        *,
        value: str | None = None,
        threshold: float | None = None,
        session: Session | None = None,
    ) -> CandidateSet:
        """Find time-series points with unusual values.

        Source must be a MetricFrame with time_series or panel shape.
        ``threshold`` controls anomaly sensitivity (lower = more candidates).
        """
        return _discover_dispatch(
            source,
            objective="point_anomalies",
            value=value,
            threshold=threshold,
            session=session,
        )

    def period_shifts(
        self,
        source: DeltaFrame,
        *,
        value: str | None = None,
        threshold: float | None = None,
        session: Session | None = None,
    ) -> CandidateSet:
        """Find period-shift candidates from a DeltaFrame.

        Requires at least four time buckets in a time-series delta, or at least
        one panel series with four time buckets.
        """

        return _discover_dispatch(
            source,
            objective="period_shifts",
            value=value,
            threshold=threshold,
            session=session,
        )

    def driver_axes(
        self,
        source: DeltaFrame,
        *,
        search_space: list[DimensionRef],
        value: str | None = None,
        limit: int | None = None,
        session: Session | None = None,
    ) -> CandidateSet:
        """Find dimensions that explain a delta.

        Source must be a DeltaFrame. ``search_space`` is required and lists
        the candidate dimensions to evaluate for explanatory power.
        """
        return _discover_dispatch(
            source,
            objective="driver_axes",
            value=value,
            limit=limit,
            search_space=search_space,
            session=session,
        )

    def interesting_slices(
        self,
        source: MetricFrame | DeltaFrame,
        *,
        search_space: list[DimensionRef] | None = None,
        value: str | None = None,
        threshold: float | None = None,
        limit: int | None = None,
        session: Session | None = None,
    ) -> CandidateSet:
        """Find dimension slices with notable values.

        Accepts a MetricFrame or DeltaFrame. Optionally narrow the search
        with ``search_space``; otherwise all available dimensions are probed.
        """
        return _discover_dispatch(
            source,
            objective="interesting_slices",
            value=value,
            threshold=threshold,
            limit=limit,
            search_space=search_space,
            session=session,
        )

    def interesting_windows(
        self,
        source: MetricFrame | DeltaFrame,
        *,
        value: str | None = None,
        threshold: float | None = None,
        session: Session | None = None,
    ) -> CandidateSet:
        """Find time windows with notable behavior.

        Source must have time_series or panel shape. Returns windows where
        the metric exhibits significant trends, level shifts, or volatility.
        """
        return _discover_dispatch(
            source,
            objective="interesting_windows",
            value=value,
            threshold=threshold,
            session=session,
        )

    def cross_sectional_outliers(
        self,
        source: MetricFrame,
        *,
        peer_scope: list[DimensionRef] | None = None,
        value: str | None = None,
        threshold: float | None = None,
        session: Session | None = None,
    ) -> CandidateSet:
        """Find segments that are outliers compared to their peers.

        Source must be a MetricFrame with segmented or panel shape.
        ``peer_scope`` defines the grouping for peer comparison; defaults to
        all non-time axes.
        """
        return _discover_dispatch(
            source,
            objective="cross_sectional_outliers",
            value=value,
            threshold=threshold,
            peer_scope=peer_scope,
            session=session,
        )


discover = DiscoverAPI()


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
    table = _OBJECTIVE_COMPATIBILITY
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
        time_column, _ = _resolve_frame_axes(source, df)
        rows = score_point_anomalies(
            df,
            source_ref=source.ref,
            value_column=value_column,
            threshold=threshold_value,
            time_column=time_column,
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
        _validate_period_shift_min_buckets(
            df,
            bucket_column=bucket_column,
            group_columns=group_columns,
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
            time_column, dim_columns = _resolve_frame_axes(source, df)
            non_value_columns = [c for c in [time_column, *dim_columns] if c is not None]
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
            time_column, group_columns = _resolve_frame_axes(source, df)
            if time_column is None:
                raise SemanticKindMismatchError(
                    message="interesting_windows requires a time bucket column",
                    details={
                        "objective": objective,
                        "expected_kind": "time_series|panel",
                    },
                )
            bucket_column = time_column
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
        bucket_col, segment_columns = _resolve_frame_axes(source, df)
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


def _validate_period_shift_min_buckets(
    df: pd.DataFrame,
    *,
    bucket_column: str,
    group_columns: list[str],
) -> None:
    minimum = 4
    if bucket_column not in df.columns:
        raise DiscoverInsufficientDataError(
            message="discover(period_shifts) requires a time bucket column",
            details={
                "objective": "period_shifts",
                "minimum": minimum,
                "row_count": 0,
                "bucket_column": bucket_column,
            },
        )

    if not group_columns:
        bucket_count = int(df[bucket_column].nunique(dropna=True))
        if bucket_count >= minimum:
            return
        raise DiscoverInsufficientDataError(
            message=(
                f"discover(period_shifts) requires at least 4 time buckets; got {bucket_count}"
            ),
            details={
                "objective": "period_shifts",
                "minimum": minimum,
                "row_count": bucket_count,
                "bucket_column": bucket_column,
            },
        )

    group_counts: dict[str, int] = {}
    for group_keys, group_df in df.groupby(group_columns, dropna=False):
        if not isinstance(group_keys, tuple):
            group_keys = (group_keys,)
        key = "|".join(str(value) for value in group_keys)
        group_counts[key] = int(group_df[bucket_column].nunique(dropna=True))
    if any(count >= minimum for count in group_counts.values()):
        return
    max_count = max(group_counts.values(), default=0)
    raise DiscoverInsufficientDataError(
        message=(
            "discover(period_shifts) requires at least one panel series with "
            f"4 time buckets; got max {max_count}"
        ),
        details={
            "objective": "period_shifts",
            "minimum": minimum,
            "row_count": max_count,
            "bucket_column": bucket_column,
            "group_columns": group_columns,
            "group_bucket_counts": group_counts,
        },
    )


def _resolve_frame_axes(
    source: MetricFrame | DeltaFrame,
    df: pd.DataFrame,
) -> tuple[str | None, list[str]]:
    """Return (time_column, dimension_columns) from axes metadata or dtype fallback.

    Checks source.meta.axes (MetricFrame) or source.meta.alignment["axes"]
    (DeltaFrame) for entries with role="time" and role="dimension".
    Falls back to is_datetime64_any_dtype detection when metadata is absent
    or incomplete.
    """
    from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

    df_columns = set(df.columns)
    axes = source.meta.alignment.get("axes") if isinstance(source, DeltaFrame) else source.meta.axes

    time_column: str | None = None
    dim_columns: list[str] = []

    if isinstance(axes, dict):
        for axis in axes.values():
            if not isinstance(axis, dict):
                continue
            column = axis.get("column")
            if not isinstance(column, str) or not column:
                continue
            if column not in df_columns:
                continue
            if axis.get("role") == "time" and time_column is None:
                time_column = column
            elif axis.get("role") == "dimension":
                dim_columns.append(column)

    if time_column is None:
        for col in df.columns:
            if is_datetime64_any_dtype(df[col]):
                time_column = col
                break

    if not dim_columns:
        time_col_set = {time_column} if time_column else set()
        dim_columns = [
            col
            for col in df.columns
            if col not in time_col_set
            and not is_numeric_dtype(df[col])
            and not is_datetime64_any_dtype(df[col])
        ]

    return time_column, sorted(dim_columns)


def _delta_axes(source: DeltaFrame) -> tuple[str, list[str]]:
    """Return (bucket_column, dimension_columns) for a DeltaFrame.

    Delegates to _resolve_frame_axes for metadata-aware detection, then
    applies the DeltaFrame-specific default that a bucket column always
    exists.
    """
    df = source.to_pandas()
    time_column, dim_columns = _resolve_frame_axes(source, df)
    bucket_column = time_column or "bucket_start"
    return bucket_column, dim_columns
