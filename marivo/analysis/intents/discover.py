"""Discover candidate follow-ups from committed analysis artifacts."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import datetime
from numbers import Real
from time import monotonic
from typing import Any, Literal, TypeGuard, cast

import numpy as np
import pandas as pd

from marivo._compat import UTC
from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.candidate_identity import assign_scored_frame_item_ids
from marivo.analysis.errors import (
    AnalysisRepair,
    DiscoverAxisNotMaterializedError,
    DiscoverInsufficientDataError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.candidate import (
    CandidateObjective,
    CandidateSet,
    CandidateSetMeta,
    CandidateSourceKind,
    CandidateStrategy,
    PointAnomalyStrategy,
    ScoredCandidateObjective,
    ScoredCandidateShape,
)
from marivo.analysis.frames.delta import DeltaFrame, FunnelDeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._candidate_columns import (
    build_union_columns,
    validate_shape_columns,
)
from marivo.analysis.intents._derived import (
    compose_candidate_origins,
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
    require_numeric_column,
    resolve_metric_value_column,
    resolve_session,
)
from marivo.analysis.intents._discover_scorers import (
    score_cross_sectional_outliers,
    score_driver_axes,
    score_interesting_slices,
    score_interesting_windows,
    score_period_shifts,
    score_point_anomalies,
    score_point_anomalies_seasonal_robust,
)
from marivo.analysis.intents._validate import require_single_metric
from marivo.analysis.lineage import LineageStep
from marivo.analysis.semantic_inputs import (
    normalize_dimension_boundary as normalize_catalog_dimension_boundary,
)
from marivo.analysis.session._runtime import persist_job_record, register_frame_artifact
from marivo.analysis.session.core import Session, ensure_session_can_execute
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import DimensionKind, TimeDimensionKind
from marivo.semantic.catalog import _SemanticInput

_DEFAULT_STRATEGY: dict[ScoredCandidateObjective, CandidateStrategy] = {
    "point_anomalies": "zscore",
    "period_shifts": "delta_window_zscore",
    "driver_axes": "concentration",
    "interesting_slices": "slice_zscore",
    "interesting_windows": "global_zscore_runs",
    "cross_sectional_outliers": "mad",
}

_OBJECTIVE_TO_SHAPE: dict[ScoredCandidateObjective, ScoredCandidateShape] = {
    "point_anomalies": "point_anomaly",
    "period_shifts": "period_shift",
    "driver_axes": "driver_axis",
    "interesting_slices": "slice",
    "interesting_windows": "window",
    "cross_sectional_outliers": "cross_sectional_outlier",
}

_VALID_OBJECTIVES = set(_OBJECTIVE_TO_SHAPE.keys())

_OBJECTIVE_REQUIRED_KWARGS: dict[ScoredCandidateObjective, tuple[str, ...]] = {
    "point_anomalies": (),
    "period_shifts": (),
    "driver_axes": ("search_space",),
    "interesting_slices": (),
    "interesting_windows": (),
    "cross_sectional_outliers": (),
}

_OBJECTIVE_THRESHOLD: dict[ScoredCandidateObjective, dict[str, Any] | None] = {
    "point_anomalies": {
        "method": "zscore",
        "default": 3.0,
        "description": "absolute z-score cutoff (|z| >= threshold)",
    },
    "period_shifts": {
        "method": "delta_window_zscore",
        "default": 2.0,
        "description": "absolute z-score of rolling window mean (|z| >= threshold)",
    },
    "interesting_slices": {
        "method": "slice_zscore",
        "default": 2.0,
        "description": "absolute z-score of slice means (|z| >= threshold)",
    },
    "interesting_windows": {
        "method": "global_zscore_runs",
        "default": 2.0,
        "description": "absolute global z-score per value (|z| >= threshold)",
    },
    "cross_sectional_outliers": {
        "method": "mad",
        "default": 3.0,
        "description": "robust z-score using MAD (|robust_z| >= threshold)",
    },
    "driver_axes": None,
}

# Human-readable applicability notes rendered into focused help and mirrored in
# the latest site docs. Keep these in one place; they are not a separate fact
# source, only a stable reading of the strategy tables above.
_OBJECTIVE_STRATEGY_APPLICABILITY: dict[ScoredCandidateObjective, str] = {
    "point_anomalies": (
        "single points deviating from the series baseline; seasonal_robust_zscore "
        "suits strong weekly seasonality where anomalies would skew mean/std"
    ),
    "period_shifts": (
        "a local regime change inside the delta series; a sustained whole-window "
        "shift belongs to compare, not discovery"
    ),
    "driver_axes": (
        "deterministic dimension concentration ranking for one delta; not causal attribution"
    ),
    "interesting_slices": "dimension slices whose means differ from the frame baseline",
    "interesting_windows": "time windows with globally unusual per-value behavior",
    "cross_sectional_outliers": (
        "segments that are outliers relative to their peers in the same frame"
    ),
}


def _is_valid_objective(objective: str) -> TypeGuard[ScoredCandidateObjective]:
    return objective in _VALID_OBJECTIVES


def _normalize_dimension_boundary(
    session: Session,
    value: _SemanticInput[DimensionKind | TimeDimensionKind],
    *,
    argument: str,
) -> str:
    return normalize_catalog_dimension_boundary(
        session.catalog,
        value,
        argument=argument,
        help_target="discover",
    )


def _normalize_dimension_inputs_boundary(
    session: Session,
    values: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None,
    *,
    argument: str,
) -> list[str] | None:
    if values is None:
        return None
    return [_normalize_dimension_boundary(session, value, argument=argument) for value in values]


_DEFAULT_DISCOVER_LIMIT: int = 50


def _discover_dispatch(
    source: MetricFrame | DeltaFrame,
    *,
    objective: CandidateObjective | str,
    strategy: CandidateStrategy | None = None,
    value: str | None = None,
    threshold: float | None = None,
    limit: int | None = _DEFAULT_DISCOVER_LIMIT,
    search_space: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None = None,
    peer_scope: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None = None,
    session: Session | None = None,
    analysis_purpose: str | None = None,
) -> CandidateSet:
    """Discover candidate follow-ups (anomalies, drivers, outliers) from a frame.

    When to use: find anomalies, drivers, or outliers without a specific hypothesis.

    Each ``objective`` is compatible with specific semantic kinds;
    mismatches raise ``SemanticKindMismatchError``. ``driver_axes``
    requires a non-empty ``search_space``.

    Args:
        source: A MetricFrame or DeltaFrame, depending on ``objective``.
        objective: One of ``point_anomalies``, ``period_shifts``, ``driver_axes``,
            ``interesting_slices``, ``interesting_windows``, ``cross_sectional_outliers``.
        strategy: Scoring strategy. Defaults are picked per objective
            (e.g. ``zscore`` for ``point_anomalies``).
        value: Numeric column to score. For a DeltaFrame, defaults to the
            canonical ``delta`` column. For a MetricFrame, defaults to the
            declared measure/value column. ``discover`` requires a single-metric
            frame; project a multi-metric frame with ``frame.metric(...)`` first.
        threshold: Score cutoff whose meaning depends on the objective.
        ``point_anomalies``: absolute z-score cutoff, default 3.0.
        ``period_shifts``: absolute z-score of rolling window mean, default 2.0.
        ``interesting_slices``: absolute z-score of slice means, default 2.0.
        ``interesting_windows``: absolute z-score per value, default 2.0.
        ``cross_sectional_outliers``: robust z-score via MAD, default 3.0.
        ``driver_axes`` does not accept threshold.
        limit: Maximum number of candidates to return, applied to every objective
        (top candidates by |score|; truncation is recorded in ``params``).
        Defaults to ``_DEFAULT_DISCOVER_LIMIT`` (50); pass ``None`` for unbounded.
        search_space: Required for ``driver_axes`` — dimensions to consider as drivers.
        peer_scope: Optional peer grouping for ``cross_sectional_outliers``.
        session: Defaults to the currently-attached session.

    Raises:
        SemanticKindMismatchError: Wrong semantic_kind for the objective,
            missing required kwargs (e.g. ``search_space`` for ``driver_axes``), or
            unsupported ``objective``.
        CrossSessionFrameError: ``source`` belongs to a different session.

    Example:
        >>> candidates = session.discover.point_anomalies(
        ...     series,
        ...     threshold=1.0,
        ...     analysis_purpose="flag revenue time-series anomalies",
        ... )
        >>> candidates.show()
    """
    session = resolve_session(session)
    ensure_session_can_execute(session)
    search_space_ids = _normalize_dimension_inputs_boundary(
        session,
        search_space,
        argument="search_space",
    )
    peer_scope_ids = _normalize_dimension_inputs_boundary(
        session,
        peer_scope,
        argument="peer_scope",
    )

    ensure_frame_in_session(source, session=session, label="discover source")
    if isinstance(source, MetricFrame):
        require_single_metric(source, intent=f"discover.{objective}")

    if not _is_valid_objective(objective):
        raise SemanticKindMismatchError(
            message=f"unsupported discover objective {objective!r}",
            context={
                "expected_kind": "|".join(sorted(_VALID_OBJECTIVES)),
                "got_kind": str(objective),
            },
        )
    discover_objective = objective

    source_kind: CandidateSourceKind = (
        "metric_frame" if isinstance(source, MetricFrame) else "delta_frame"
    )
    if isinstance(source.meta, FunnelDeltaFrameMeta):
        allowed_semantic_kinds = _objective_semantic_kinds(discover_objective)
        raise SemanticKindMismatchError(
            message=(
                f"discover objective {discover_objective!r} does not accept DeltaFrame[funnel]"
            ),
            context={
                "objective": discover_objective,
                "semantic_kind": source.meta.semantic_kind,
                "expected_kind": "|".join(sorted(allowed_semantic_kinds)),
            },
        )
    _check_objective_compatibility(discover_objective, source.meta.semantic_kind)

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
        strategy=resolved_strategy,
        search_space=search_space_ids,
        peer_scope=peer_scope_ids,
    )
    source_artifact_ref = source.meta.artifact_id or source.ref
    rows, limit_info = _apply_limit(rows, limit)
    params = {**params, **limit_info}
    df = build_union_columns(shape, rows)
    assign_scored_frame_item_ids(
        shape=shape,
        source_artifact_ref=source_artifact_ref,
        dataframe=df,
    )
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
    # discover operates on arity-1 frames; multi-metric frames are gated out
    # upstream. Narrow metric_id for CandidateSetMeta.metric_ids (list[str]).
    assert source.meta.metric_id is not None
    meta = CandidateSetMeta(
        kind="candidate_set",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        analysis_purpose=analysis_purpose,
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
                analysis_purpose=analysis_purpose,
            ),
        ),
        candidate_origins=compose_candidate_origins((source,)),
        shape=shape,
        objective=discover_objective,
        strategy=resolved_strategy,
        source_ref=source_artifact_ref,
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
        if meta.semantic_kind == "time_series"
        else "segment"
        if meta.semantic_kind == "segmented"
        else "panel"
        if meta.semantic_kind == "panel"
        else meta.semantic_kind
    )
    observed_window = source.meta.window if hasattr(source.meta, "window") else None
    frame = cast(
        "CandidateSet",
        commit_result(
            session=session,
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=frame,
            step_type="discover",
            inputs=CommitInputs(input_refs=[source_ref]),
            params=CommitParams(values=full_params),
            semantic_anchors=CommitSemanticAnchors.from_frame(source),
            subject=Subject(
                grain=getattr(source.meta, "grain", None),
                analysis_axis=axis,
            ),
            extractor_family="candidate_set",
            seeding_context={"observed_window": observed_window},
        ),
    )
    register_frame_artifact(session, frame)
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "discover",
            **job_semantics_from_frames(source),
            "analysis_purpose": analysis_purpose,
            "params": full_params,
            "input_frame_refs": [source.ref],
            "output_frame_ref": frame.meta.artifact_id or frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog.semantic_root),
        },
    )
    return frame


class DiscoverAPI:
    """Callable namespace for candidate discovery objectives."""

    def point_anomalies(
        self,
        source: MetricFrame,
        *,
        value: str | None = None,
        threshold: float | None = None,
        limit: int | None = _DEFAULT_DISCOVER_LIMIT,
        strategy: PointAnomalyStrategy | None = None,
        session: Session | None = None,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        return _discover_dispatch(
            source,
            objective="point_anomalies",
            value=value,
            threshold=threshold,
            limit=limit,
            strategy=strategy,
            session=session,
            analysis_purpose=analysis_purpose,
        )

    def period_shifts(
        self,
        source: DeltaFrame,
        *,
        value: str | None = None,
        threshold: float | None = None,
        limit: int | None = _DEFAULT_DISCOVER_LIMIT,
        session: Session | None = None,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:

        return _discover_dispatch(
            source,
            objective="period_shifts",
            value=value,
            threshold=threshold,
            limit=limit,
            session=session,
            analysis_purpose=analysis_purpose,
        )

    def driver_axes(
        self,
        source: DeltaFrame,
        *,
        search_space: list[_SemanticInput[DimensionKind | TimeDimensionKind]],
        value: str | None = None,
        limit: int | None = _DEFAULT_DISCOVER_LIMIT,
        session: Session | None = None,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        return _discover_dispatch(
            source,
            objective="driver_axes",
            value=value,
            limit=limit,
            search_space=search_space,
            session=session,
            analysis_purpose=analysis_purpose,
        )

    def interesting_slices(
        self,
        source: MetricFrame | DeltaFrame,
        *,
        search_space: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None = None,
        value: str | None = None,
        threshold: float | None = None,
        limit: int | None = _DEFAULT_DISCOVER_LIMIT,
        session: Session | None = None,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        return _discover_dispatch(
            source,
            objective="interesting_slices",
            value=value,
            threshold=threshold,
            limit=limit,
            search_space=search_space,
            session=session,
            analysis_purpose=analysis_purpose,
        )

    def interesting_windows(
        self,
        source: MetricFrame | DeltaFrame,
        *,
        value: str | None = None,
        threshold: float | None = None,
        limit: int | None = _DEFAULT_DISCOVER_LIMIT,
        session: Session | None = None,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        return _discover_dispatch(
            source,
            objective="interesting_windows",
            value=value,
            threshold=threshold,
            limit=limit,
            session=session,
            analysis_purpose=analysis_purpose,
        )

    def cross_sectional_outliers(
        self,
        source: MetricFrame,
        *,
        peer_scope: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None = None,
        value: str | None = None,
        threshold: float | None = None,
        limit: int | None = _DEFAULT_DISCOVER_LIMIT,
        session: Session | None = None,
        analysis_purpose: str | None = None,
    ) -> CandidateSet:
        return _discover_dispatch(
            source,
            objective="cross_sectional_outliers",
            value=value,
            threshold=threshold,
            limit=limit,
            peer_scope=peer_scope,
            session=session,
            analysis_purpose=analysis_purpose,
        )


discover = DiscoverAPI()


_STRATEGY_ALTERNATIVES: dict[ScoredCandidateObjective, set[CandidateStrategy]] = {
    "point_anomalies": {"seasonal_robust_zscore"},
}


def _resolve_strategy(
    objective: ScoredCandidateObjective, strategy: CandidateStrategy | None
) -> CandidateStrategy:
    default = _DEFAULT_STRATEGY[objective]
    if strategy is None or strategy == default:
        return default
    if strategy in _STRATEGY_ALTERNATIVES.get(objective, set()):
        return strategy
    raise SemanticKindMismatchError(
        message=f"unsupported discover strategy {strategy!r}",
        context={"expected_kind": default, "got_kind": str(strategy)},
    )


def _check_objective_compatibility(
    objective: ScoredCandidateObjective,
    semantic_kind: str,
) -> None:
    allowed = _objective_semantic_kinds(objective)
    if semantic_kind not in allowed:
        raise SemanticKindMismatchError(
            message=(
                f"discover objective {objective!r} does not accept semantic_kind {semantic_kind!r}"
            ),
            context={
                "objective": objective,
                "semantic_kind": semantic_kind,
                "expected_kind": "|".join(sorted(allowed)),
            },
        )


def _objective_semantic_kinds(objective: ScoredCandidateObjective) -> frozenset[str]:
    from marivo.analysis._capabilities.model import OperatorCapability
    from marivo.analysis._capabilities.registry import REGISTRY

    descriptor = REGISTRY.by_id(f"discover.{objective}")
    assert isinstance(descriptor, OperatorCapability)
    admission = descriptor.artifact_admission["source"]
    return frozenset(shape for shapes in admission.semantic_shapes.values() for shape in shapes)


def _discover_value_repair(df: pd.DataFrame) -> AnalysisRepair:
    """Build a discover-specific repair pointing at the numeric value columns."""
    numeric = [str(c) for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if numeric:
        action = f"Pass value=<column> to select one of the numeric columns: {numeric!r}."
    else:
        action = "Ensure the source frame materializes a numeric value column."
    return AnalysisRepair(
        kind="retry",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id="discover"),
        candidates=tuple(numeric),
    )


def _resolve_discover_value_column(
    source: MetricFrame | DeltaFrame,
    df: pd.DataFrame,
    value: str | None,
    *,
    purpose: str = "discover",
) -> str:
    """Resolve the value column to score for a discover objective.

    An explicit ``value`` is validated strictly (must exist and be numeric). For
    a :class:`MetricFrame`, an explicit ``value`` may be either the public
    measure name (``value_columns``) or its internal storage column, so it is
    resolved to the internal column before the numeric check. For a
    :class:`DeltaFrame`, an explicit ``value`` must already name an internal
    column (public names equal internal names). An omitted ``value`` defaults to
    the frame's canonical value column — ``delta`` for a :class:`DeltaFrame`,
    the declared measure/value column for a :class:`MetricFrame` — instead of
    dtype-sniffing for a single numeric column. This keeps a DeltaFrame's
    current/baseline/delta/pct_change set (four numeric columns) from tripping
    the single-column requirement and lets a MetricFrame with an extra numeric
    axis still resolve its declared measure.
    """
    if value is not None:
        if isinstance(source, MetricFrame):
            try:
                resolved = resolve_metric_value_column(
                    source, df, value, parameter="value", purpose=purpose
                )
            except SemanticKindMismatchError as exc:
                raise SemanticKindMismatchError(
                    message=exc.message,
                    context=exc._context,
                    repair=_discover_value_repair(df),
                ) from exc
            return require_numeric_column(
                df, resolved.internal_name, purpose=purpose, repair=_discover_value_repair(df)
            )
        return require_numeric_column(df, value, purpose=purpose, repair=_discover_value_repair(df))

    if isinstance(source, DeltaFrame):
        return require_numeric_column(df, "delta", purpose=purpose)

    try:
        resolved = resolve_metric_value_column(source, df, None, parameter="value", purpose=purpose)
    except SemanticKindMismatchError as exc:
        raise SemanticKindMismatchError(
            message=(
                "discover requires an explicit value when the frame value column cannot be resolved"
            ),
            context={
                "columns": list(df.columns),
                "value_columns": list(source.value_columns),
            },
            repair=_discover_value_repair(df),
        ) from exc
    return resolved.internal_name


def _run_scorer(
    *,
    objective: ScoredCandidateObjective,
    source: MetricFrame | DeltaFrame,
    source_kind: CandidateSourceKind,
    value: str | None,
    threshold: float | None,
    strategy: CandidateStrategy,
    search_space: list[str] | None,
    peer_scope: list[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if objective == "point_anomalies":
        _threshold_info = _OBJECTIVE_THRESHOLD[objective]
        assert _threshold_info is not None  # guaranteed for point_anomalies
        threshold_value = _validate_threshold(
            _threshold_info["default"] if threshold is None else threshold
        )
        df = source._dataframe_copy()
        value_column = _resolve_discover_value_column(source, df, value, purpose="discover")
        time_column, dim_columns = _resolve_frame_axes(source, df)
        if strategy == "seasonal_robust_zscore":
            rows = score_point_anomalies_seasonal_robust(
                df,
                source_ref=source.ref,
                value_column=value_column,
                threshold=threshold_value,
                time_column=time_column,
            )
        else:
            # Panel (time + segments) scores each segment series independently so a
            # small segment's intra-series anomaly is not drowned by cross-segment
            # magnitude differences. Gate on a real time column so a string-bucket
            # series is not mis-grouped on its bucket.
            group_columns = dim_columns if time_column else []
            rows = score_point_anomalies(
                df,
                source_ref=source.ref,
                value_column=value_column,
                threshold=threshold_value,
                time_column=time_column,
                group_columns=group_columns,
            )
        params = {"value": value_column, "threshold": threshold_value}
        return rows, params

    if objective == "period_shifts":
        _threshold_info = _OBJECTIVE_THRESHOLD[objective]
        assert _threshold_info is not None  # guaranteed for period_shifts
        threshold_value = _validate_threshold(
            _threshold_info["default"] if threshold is None else threshold
        )
        df = source._dataframe_copy()
        bucket_column, group_columns = _delta_axes(cast("DeltaFrame", source))
        value_column = _resolve_discover_value_column(source, df, value, purpose="discover")
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
        params = {"value": value_column, "threshold": threshold_value}
        return rows, params

    if objective == "driver_axes":
        if not search_space:
            raise SemanticKindMismatchError(
                message="discover(driver_axes) requires a non-empty search_space",
                context={"objective": objective, "missing": "search_space"},
            )
        df = source._dataframe_copy()
        bucket_column, dim_columns = _delta_axes(cast("DeltaFrame", source))
        value_column = _resolve_discover_value_column(source, df, value, purpose="discover")
        axes = _dimension_columns_for_ids(source, search_space)
        _require_materialized_axes(
            objective=objective,
            df=df,
            axes=axes,
            dimension_ids=search_space,
            available_dimension_columns=dim_columns,
        )
        semantic_id_by_column = _semantic_ids_by_column(source, search_space)
        rows = score_driver_axes(
            df,
            source_ref=source.ref,
            value_column=value_column,
            axes=axes,
            bucket_column=bucket_column if bucket_column in df.columns else None,
            limit=None,
        )
        _attach_axis_semantic_ids(rows, semantic_id_by_column)
        driver_params: dict[str, Any] = {
            "value": value_column,
            "search_space": search_space,
            "search_space_columns": axes,
        }
        return rows, driver_params

    if objective == "interesting_slices":
        _threshold_info = _OBJECTIVE_THRESHOLD[objective]
        assert _threshold_info is not None  # guaranteed for interesting_slices
        threshold_value = _validate_threshold(
            _threshold_info["default"] if threshold is None else threshold
        )
        df = source._dataframe_copy()
        if isinstance(source, DeltaFrame):
            _, dim_columns = _delta_axes(source)
        else:
            _, dim_columns = _resolve_frame_axes(source, df)
        value_column = _resolve_discover_value_column(source, df, value, purpose="discover")
        axes = _dimension_columns_for_ids(source, search_space or []) or dim_columns
        if search_space:
            _require_materialized_axes(
                objective=objective,
                df=df,
                axes=axes,
                dimension_ids=search_space,
                available_dimension_columns=dim_columns,
            )
        semantic_id_by_column = _semantic_ids_by_column(
            source,
            search_space or [],
            available_columns=axes,
        )
        rows, skipped_subsets = score_interesting_slices(
            df,
            source_ref=source.ref,
            value_column=value_column,
            axes=axes,
            threshold=threshold_value,
            limit=None,
        )
        _attach_selector_semantic_ids(rows, semantic_id_by_column)
        slice_params: dict[str, Any] = {
            "value": value_column,
            "threshold": threshold_value,
            "search_space": search_space or [],
            "search_space_columns": axes,
        }
        if skipped_subsets:
            slice_params["skipped_subsets"] = skipped_subsets
        return rows, slice_params

    if objective == "interesting_windows":
        _threshold_info = _OBJECTIVE_THRESHOLD[objective]
        assert _threshold_info is not None  # guaranteed for interesting_windows
        threshold_value = _validate_threshold(
            _threshold_info["default"] if threshold is None else threshold
        )
        df = source._dataframe_copy()
        if isinstance(source, DeltaFrame):
            bucket_column, group_columns = _delta_axes(source)
        else:
            time_column, group_columns = _resolve_frame_axes(source, df)
            if time_column is None:
                raise SemanticKindMismatchError(
                    message="interesting_windows requires a time bucket column",
                    context={
                        "objective": objective,
                        "expected_kind": "time_series|panel",
                    },
                )
            bucket_column = time_column
        value_column = _resolve_discover_value_column(source, df, value, purpose="discover")
        rows = score_interesting_windows(
            df,
            source_ref=source.ref,
            bucket_column=bucket_column,
            value_column=value_column,
            threshold=threshold_value,
            group_columns=group_columns,
        )
        params = {"value": value_column, "threshold": threshold_value}
        return rows, params

    if objective == "cross_sectional_outliers":
        _threshold_info = _OBJECTIVE_THRESHOLD[objective]
        assert _threshold_info is not None  # guaranteed for cross_sectional_outliers
        threshold_value = _validate_threshold(
            _threshold_info["default"] if threshold is None else threshold
        )
        df = source._dataframe_copy()
        bucket_col, segment_columns = _resolve_frame_axes(source, df)
        value_column = _resolve_discover_value_column(source, df, value, purpose="discover")
        peer_axes = _dimension_columns_for_ids(source, peer_scope or [])
        if peer_scope:
            # Fail closed on a peer_scope axis not materialized in the frame,
            # mirroring the search_space check: never silently fall back to a
            # global comparison while still recording the axis as grouped.
            missing_peer_axes = [axis for axis in peer_axes if axis not in df.columns]
            if missing_peer_axes:
                raise SemanticKindMismatchError(
                    message=(
                        "discover(cross_sectional_outliers) peer_scope references axes "
                        f"not materialized in the source frame: {', '.join(missing_peer_axes)}"
                    ),
                    context={
                        "objective": objective,
                        "missing_axes": missing_peer_axes,
                        "peer_scope": peer_axes,
                        "available_dimension_columns": segment_columns,
                    },
                )
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
            "value": value_column,
            "threshold": threshold_value,
            "peer_scope": peer_axes,
        }
        return rows, outlier_params

    raise SemanticKindMismatchError(
        message=f"discover objective {objective!r} is not implemented",
        context={
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


def _validate_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise SemanticKindMismatchError(message="discover limit must be a positive integer or None")
    if limit < 1:
        raise SemanticKindMismatchError(message="discover limit must be a positive integer or None")
    return limit


def _apply_limit(
    rows: list[dict[str, Any]], limit: int | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Truncate candidates to the top ``limit`` by |score|, recording the fact.

    Truncation is never silent: when rows are dropped the returned params carry
    ``truncated=True`` plus before/after counts so agents can see that the
    CandidateSet was bounded. ``limit`` is always recorded (None = unbounded).
    """
    limit = _validate_limit(limit)
    info: dict[str, Any] = {"limit": limit}
    if limit is None or len(rows) <= limit:
        return rows, info
    kept = sorted(rows, key=lambda r: abs(float(r["score"])), reverse=True)[:limit]
    info["truncated"] = True
    info["candidate_count_before_limit"] = len(rows)
    info["candidate_count"] = len(kept)
    return kept, info


def _validate_period_shift_min_buckets(
    df: pd.DataFrame,
    *,
    bucket_column: str,
    group_columns: list[str],
) -> None:
    # Matches the scorer's rolling-window floor: window_size = max(7, n // 10)
    # with min_periods=window_size, so fewer than 7 buckets yield all-NaN
    # means and a silently empty result. Fail closed at the same floor.
    minimum = 7
    if bucket_column not in df.columns:
        raise DiscoverInsufficientDataError(
            message="discover(period_shifts) requires a time bucket column",
            context={
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
                f"discover(period_shifts) requires at least {minimum} time buckets; "
                f"got {bucket_count}"
            ),
            context={
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
            f"{minimum} time buckets; got max {max_count}"
        ),
        context={
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

    baseline_bucket_column: str | None = None
    if isinstance(source, DeltaFrame):
        candidate = source.meta.alignment.get("baseline_bucket_column")
        if isinstance(candidate, str):
            baseline_bucket_column = candidate

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
        excluded = {time_column} if time_column else set()
        if baseline_bucket_column is not None:
            excluded.add(baseline_bucket_column)
        dim_columns = [
            col
            for col in df.columns
            if col not in excluded
            and not is_numeric_dtype(df[col])
            and not is_datetime64_any_dtype(df[col])
        ]

    if baseline_bucket_column is not None:
        dim_columns = [col for col in dim_columns if col != baseline_bucket_column]

    return time_column, sorted(dim_columns)


def _axis_matches(axis_id: str, axis_meta: Any, requested: str) -> bool:
    candidates = {axis_id, axis_id.rsplit(".", 1)[-1]}
    if isinstance(axis_meta, dict):
        column = axis_meta.get("column")
        ref = axis_meta.get("ref")
        if isinstance(column, str):
            candidates.add(column)
        if isinstance(ref, str):
            candidates.add(ref)
            candidates.add(ref.rsplit(".", 1)[-1])
    return requested in candidates or requested.rsplit(".", 1)[-1] in candidates


def _dimension_columns_for_ids(
    source: MetricFrame | DeltaFrame, dimension_ids: list[str]
) -> list[str]:
    if not dimension_ids:
        return []
    axes = source.meta.alignment.get("axes") if isinstance(source, DeltaFrame) else source.meta.axes
    columns: list[str] = []
    df_columns = set(source._dataframe_copy().columns)
    for dimension_id in dimension_ids:
        matched_column: str | None = None
        if isinstance(axes, dict):
            for axis_id, axis_meta in axes.items():
                if not isinstance(axis_meta, dict) or axis_meta.get("role") != "dimension":
                    continue
                if not _axis_matches(str(axis_id), axis_meta, dimension_id):
                    continue
                column = axis_meta.get("column")
                if isinstance(column, str):
                    matched_column = column
                    break
        if matched_column is None:
            axis_leaf = dimension_id.rsplit(".", 1)[-1]
            matched_column = dimension_id if dimension_id in df_columns else axis_leaf
        columns.append(matched_column)
    return columns


def _require_materialized_axes(
    *,
    objective: ScoredCandidateObjective,
    df: pd.DataFrame,
    axes: list[str],
    dimension_ids: list[str],
    available_dimension_columns: list[str],
) -> None:
    """Fail closed when a requested search_space axis is not in the frame.

    Previously these axes were silently skipped, producing an empty (or
    partial) CandidateSet indistinguishable from "no driver/slice found".
    """
    missing = [axis for axis in axes if axis not in df.columns]
    if not missing:
        return
    raise DiscoverAxisNotMaterializedError(
        message=(
            f"discover({objective}) search_space references axes not materialized "
            f"in the source frame: {', '.join(missing)}"
        ),
        context={
            "objective": objective,
            "missing_axes": missing,
            "search_space": dimension_ids,
            "available_dimension_columns": available_dimension_columns,
        },
    )


def _semantic_ids_by_column(
    source: MetricFrame | DeltaFrame,
    dimension_ids: list[str],
    *,
    available_columns: list[str] | None = None,
) -> dict[str, str]:
    axes = source.meta.alignment.get("axes") if isinstance(source, DeltaFrame) else source.meta.axes
    allowed_columns = set(available_columns or ())
    mapping: dict[str, str] = {}
    if isinstance(axes, dict):
        for axis_id, axis_meta in axes.items():
            if not isinstance(axis_meta, dict) or axis_meta.get("role") != "dimension":
                continue
            column = axis_meta.get("column")
            if not isinstance(column, str) or (allowed_columns and column not in allowed_columns):
                continue
            ref = axis_meta.get("ref")
            if isinstance(ref, str) and ref:
                mapping[column] = ref
            elif isinstance(axis_id, str) and axis_id.count(".") >= 2:
                mapping[column] = axis_id

    columns = _dimension_columns_for_ids(source, dimension_ids)
    mapping.update(
        {column: semantic_id for semantic_id, column in zip(dimension_ids, columns, strict=False)}
    )
    return mapping


def _attach_axis_semantic_ids(
    rows: list[dict[str, Any]],
    semantic_id_by_column: dict[str, str],
) -> None:
    for row in rows:
        axis = row.get("axis")
        if isinstance(axis, str) and axis in semantic_id_by_column:
            row["axis_semantic_id"] = semantic_id_by_column[axis]


def _attach_selector_semantic_ids(
    rows: list[dict[str, Any]],
    semantic_id_by_column: dict[str, str],
) -> None:
    if not semantic_id_by_column:
        return
    for row in rows:
        selector = row.get("selector")
        if not isinstance(selector, dict):
            continue
        semantic_selector = {
            semantic_id_by_column.get(str(key), str(key)): value for key, value in selector.items()
        }
        row["selector"] = semantic_selector
        if isinstance(row.get("keys"), dict):
            row["keys"] = dict(semantic_selector)


def _delta_axes(source: DeltaFrame) -> tuple[str, list[str]]:
    """Return (bucket_column, dimension_columns) for a DeltaFrame.

    Delegates to _resolve_frame_axes for metadata-aware detection, then
    applies the DeltaFrame-specific default that a bucket column always
    exists.
    """
    df = source._dataframe_copy()
    time_column, dim_columns = _resolve_frame_axes(source, df)
    bucket_column = time_column or "bucket_start"
    return bucket_column, dim_columns
