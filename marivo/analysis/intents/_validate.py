"""Pre-submit validators for analysis intents (no backend execution).

Each validator reads only frame metadata + policy and returns the first
incompatibility as a one-element list of constructed AnalysisError instances
(or [] when valid), mirroring the intents' fail-fast raise. Adapters support
both fail-fast raising and structured ValidationIssue conversion.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from marivo.analysis._cumulative import cumulative_compare_anchor
from marivo.analysis.errors import (
    AlignmentFailedError,
    AlignmentPolicyNotApplicableError,
    AnalysisError,
    AxisNotInPanelDimensionsError,
    CumulativeFrameUnsupportedError,
    MetricArityError,
    PanelGrainMismatchError,
    SegmentDimensionMismatchError,
    SemanticKindMismatchError,
)
from marivo.analysis.validation import ValidationIssue

if TYPE_CHECKING:
    import pandas as pd

    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.policies import AlignmentPolicy


def raise_first(issues: list[AnalysisError]) -> None:
    """Raise the first validation issue, if any (used by the intent call path)."""
    if issues:
        raise issues[0]


def to_validation_issues(intent: str, issues: list[AnalysisError]) -> list[ValidationIssue]:
    """Convert validator errors into structured ValidationIssue records."""
    return [
        ValidationIssue(
            intent=intent,
            error_type=type(error).__name__,
            message=error.message,
            context=dict(error._context),
        )
        for error in issues
    ]


def require_single_metric(frame: MetricFrame, *, intent: str) -> None:
    """Raise MetricArityError when a multi-metric frame reaches a single-metric intent."""
    measures = getattr(frame.meta, "measures", None)
    if not measures or len(measures) <= 1:
        return
    metric_ids = [entry["metric_id"] for entry in measures]
    raise MetricArityError(
        message=(
            f"{intent} expects a single-metric frame, got {len(metric_ids)} metrics {metric_ids!r}"
        ),
        hint=(
            f'call frame.metric("{metric_ids[0]}") (or another id above) to project '
            "a single-metric frame first"
        ),
        context={
            "intent": intent,
            "expected_arity": 1,
            "got_arity": len(metric_ids),
            "metrics": metric_ids,
        },
    )


def cumulative_issue(frame: MetricFrame, *, intent: str) -> AnalysisError | None:
    """Return a CumulativeFrameUnsupportedError when the frame is cumulative, else None.

    This is the blanket gate used by forecast/attribute/decompose (all anchors
    rejected). Compare uses :func:`cumulative_compare_issue` instead, which is
    anchor-dispatched and allows trailing / grain_to_date under validations.
    """
    cumulative = getattr(frame.meta, "cumulative", None)
    if cumulative is None:
        return None
    return CumulativeFrameUnsupportedError(
        intent=intent,
        frame_ref=frame.ref,
        metric_id=frame.meta.metric_id,
        cumulative=cumulative,
    )


def cumulative_compare_issue(
    current: MetricFrame,
    baseline: MetricFrame,
    *,
    report_tz: str | None = None,
) -> AnalysisError | None:
    """Anchor-dispatched compare gate for arity-1 cumulative frames.

    Returns a teaching error when the compare must be rejected, or None when the
    anchor's compare path is allowed:

    - ``all_history``: rejected (existing class; hint names the base ref).
    - ``trailing``: allowed iff both frames' anchor payloads match exactly.
    - ``grain_to_date``: allowed via :func:`_grain_to_date_compare_validations`.
    - incompatible derived wrappers and malformed markers: rejected.
    """
    cur_cum = current.meta.cumulative
    base_cum = baseline.meta.cumulative
    if cur_cum is None and base_cum is None:
        return None
    if cur_cum is None or base_cum is None:
        return AnalysisError(
            message="compare requires both frames to share cumulative metadata state.",
            hint="Re-observe both frames from the same metric contract before comparing.",
            context={
                "kind": "CumulativeMarkerPresenceMismatch",
                "current_cumulative": cur_cum,
                "baseline_cumulative": base_cum,
            },
        )
    if cur_cum.get("kind") != base_cum.get("kind"):
        return AnalysisError(
            message="compare requires both frames to share the same cumulative marker kind.",
            hint="Re-observe both frames from the same metric contract before comparing.",
            context={
                "kind": "CumulativeMarkerKindMismatch",
                "current_kind": cur_cum.get("kind"),
                "baseline_kind": base_cum.get("kind"),
            },
        )
    anchor = cumulative_compare_anchor(cur_cum)
    if anchor is None:
        return CumulativeFrameUnsupportedError(
            intent="compare",
            frame_ref=current.ref,
            metric_id=current.meta.metric_id,
            cumulative=cur_cum,
        )
    if anchor == "all_history":
        return CumulativeFrameUnsupportedError(
            intent="compare",
            frame_ref=current.ref,
            metric_id=current.meta.metric_id,
            cumulative=cur_cum,
        )
    if isinstance(anchor, tuple) and anchor and anchor[0] == "trailing":
        # Allowed iff both frames' anchor payloads match exactly.
        base_anchor = cumulative_compare_anchor(base_cum)
        if base_anchor != anchor:
            return AnalysisError(
                message=(
                    "compare(trailing) requires both frames to share the same "
                    "trailing anchor payload."
                ),
                hint=f"Observe the baseline with the same anchor {anchor!r}.",
                context={
                    "kind": "TrailingAnchorMismatch",
                    "current_anchor": anchor,
                    "baseline_anchor": base_anchor,
                },
            )
        return None
    if isinstance(anchor, tuple) and anchor and anchor[0] == "grain_to_date":
        return _grain_to_date_compare_validations(
            current,
            baseline,
            anchor[1],
            report_tz=report_tz,
        )
    return CumulativeFrameUnsupportedError(
        intent="compare",
        frame_ref=current.ref,
        metric_id=current.meta.metric_id,
        cumulative=cur_cum,
    )


def _grain_to_date_compare_validations(
    current: MetricFrame,
    baseline: MetricFrame,
    reset_grain: str,
    *,
    report_tz: str | None = None,
) -> AnalysisError | None:
    """Validations for compare(grain_to_date) on effective cumulative anchors.

    Three structural validations plus a scalar elapsed-span check. Each returns a
    teaching error stating expected/received/next-step. Returns None on success.

    1. Both frames share reset grain AND query grain.
    2. Window starts on a reset boundary (via window meta truncation).
    3. Window spans at most one reset period.
    4. Scalar elapsed-span check: current elapsed span == baseline elapsed span.
    """
    from marivo.analysis.intents._window_pairs import (
        _advance_bucket_date,
        _panel_grain,
        _parse_window_datetime,
        _truncate_bucket_date,
    )

    # Validation 1: both frames share reset grain AND query grain.
    base_cum = baseline.meta.cumulative
    if base_cum is not None:
        base_anchor = cumulative_compare_anchor(base_cum)
        if (
            not (
                isinstance(base_anchor, tuple) and base_anchor and base_anchor[0] == "grain_to_date"
            )
            or base_anchor[1] != reset_grain
        ):
            return AnalysisError(
                message=(
                    "compare(grain_to_date) requires both frames to share the same reset grain."
                ),
                hint=(f"Observe the baseline with anchor grain_to_date(grain={reset_grain!r})."),
                context={
                    "kind": "GrainToDateResetGrainMismatch",
                    "current_reset_grain": reset_grain,
                    "baseline_anchor": base_anchor,
                },
            )
    cur_query_grain = _panel_grain(current)
    base_query_grain = _panel_grain(baseline)
    if cur_query_grain != base_query_grain:
        return AnalysisError(
            message=("compare(grain_to_date) requires both frames to share the same query grain."),
            hint=("Re-observe current and baseline at the same time grain before comparing."),
            context={
                "kind": "GrainToDateQueryGrainMismatch",
                "current_query_grain": cur_query_grain,
                "baseline_query_grain": base_query_grain,
            },
        )

    # Validation 2 + 3 + scalar elapsed-span check operate on the window meta.
    cur_window = current.meta.window
    base_window = baseline.meta.window
    if not isinstance(cur_window, dict) or not isinstance(base_window, dict):
        return AnalysisError(
            message="compare(grain_to_date) requires window metadata on both frames.",
            hint="Re-observe with an explicit time_scope so window metadata is recorded.",
            context={
                "kind": "GrainToDateWindowMissing",
                "current_window": cur_window,
                "baseline_window": base_window,
            },
        )

    def _elapsed_span(window: dict[str, object]) -> timedelta | None:
        start = window.get("start")
        end = window.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            return None
        try:
            s = _parse_window_datetime(start, field="start", report_tz=report_tz)
            e = _parse_window_datetime(end, field="end", report_tz=report_tz)
        except (AlignmentFailedError, ValueError, TypeError):
            return None
        return e - s

    # Validation 2: window starts on a reset boundary (raw inclusive start).
    for label, window in (("current", cur_window), ("baseline", base_window)):
        start_raw = window.get("start")
        if not isinstance(start_raw, str):
            continue
        start_dt = _parse_window_datetime(start_raw, field="start", report_tz=report_tz)
        truncated = _truncate_bucket_date(start_dt.date(), grain=reset_grain)
        expected_boundary = datetime.combine(truncated, time.min)
        if start_dt != expected_boundary:
            return AnalysisError(
                message=(
                    f"compare(grain_to_date) requires the {label} window to start on "
                    f"a {reset_grain} reset boundary."
                ),
                hint=(
                    f"Re-observe the {label} frame starting at a {reset_grain} boundary "
                    f"(e.g. the first day of the {reset_grain})."
                ),
                context={
                    "kind": "GrainToDateBoundaryRequired",
                    "frame": label,
                    "reset_grain": reset_grain,
                    "window_start": start_raw,
                    "expected_boundary": expected_boundary.isoformat(),
                },
            )

    # Validation 3: window spans at most one reset period.
    for label, window in (("current", cur_window), ("baseline", base_window)):
        span = _elapsed_span(window)
        if span is None:
            continue
        # Use window start (already boundary-validated) truncated to the reset
        # grain, then advance one reset period to get the next boundary.
        start_raw = window.get("start")
        if not isinstance(start_raw, str):
            continue
        end_raw = window.get("end")
        if not isinstance(end_raw, str):
            continue
        start_dt = _parse_window_datetime(start_raw, field="start", report_tz=report_tz)
        end_dt = _parse_window_datetime(end_raw, field="end", report_tz=report_tz)
        period_start = _truncate_bucket_date(start_dt.date(), grain=reset_grain)
        next_period = _advance_bucket_date(period_start, grain=reset_grain)
        next_boundary = datetime.combine(next_period, time.min)
        if end_dt > next_boundary:
            return AnalysisError(
                message=(
                    f"compare(grain_to_date) requires the {label} window to span at most "
                    f"one {reset_grain} reset period; window end {end_raw!r} is after "
                    f"the next reset boundary {next_boundary.isoformat()!r}."
                ),
                hint=(
                    "Observe a single reset period per frame (e.g. one month for MTD). "
                    "Multi-period cumulative compares are ambiguous; re-observe the base "
                    "flow metric and aggregate periods separately."
                ),
                context={
                    "kind": "GrainToDateMultiPeriod",
                    "frame": label,
                    "reset_grain": reset_grain,
                    "window_span_seconds": span.total_seconds(),
                    "next_reset_boundary": next_boundary.isoformat(),
                },
            )

    # Scalar elapsed-span check: current elapsed span == baseline elapsed span.
    # Only applies to scalar frames (no query grain); time_series frames use
    # ordinal alignment, which produces baseline_tail_buckets for length
    # differences instead of rejecting them.
    if cur_query_grain is None and base_query_grain is None:
        cur_span = _elapsed_span(cur_window)
        base_span = _elapsed_span(base_window)
        if cur_span is not None and base_span is not None and cur_span != base_span:
            cur_seconds = cur_span.total_seconds()
            base_seconds = base_span.total_seconds()
            return AnalysisError(
                message=(
                    "compare(grain_to_date) requires both frames to cover the same elapsed "
                    f"window span; current spans {cur_seconds} seconds, baseline spans "
                    f"{base_seconds} seconds."
                ),
                hint=(
                    "Re-observe so both windows cover the same elapsed span (e.g. 3 days "
                    "into the month for both current and baseline)."
                ),
                context={
                    "kind": "GrainToDateElapsedSpanMismatch",
                    "current_elapsed_seconds": cur_seconds,
                    "baseline_elapsed_seconds": base_seconds,
                },
            )
    return None


def validate_compare(
    current: MetricFrame,
    baseline: MetricFrame,
    *,
    alignment: AlignmentPolicy,
    report_tz: str | None = None,
) -> list[AnalysisError]:
    """Shape/policy compatibility for compare; returns the first issue or []."""
    from marivo.analysis.intents._window_pairs import _panel_grains
    from marivo.analysis.intents.compare import (
        _dimension_columns,
        _observe_report_tz,
        _requested_dimension_ids,
        _time_axis_identity,
    )

    # Compare uses the effective anchor-dispatched gate. Compatible derived
    # cumulative wrappers reuse the trailing / grain_to_date validations;
    # all_history and blocked wrappers stay gated.
    issue = cumulative_compare_issue(current, baseline, report_tz=report_tz)
    if issue is not None:
        return [issue]
    anchor = cumulative_compare_anchor(current.meta.cumulative)
    if (
        isinstance(anchor, tuple)
        and anchor
        and anchor[0] == "grain_to_date"
        and (alignment.kind != "window_bucket" or alignment.mode != "ordinal_bucket")
    ):
        return [
            AnalysisError(
                message=(
                    "compare(grain_to_date) requires ordinal window-bucket alignment; "
                    f"got kind={alignment.kind!r}, mode={alignment.mode!r}."
                ),
                hint=(
                    "Use AlignmentPolicy(kind='window_bucket', mode='ordinal_bucket') "
                    "for period-position comparison."
                ),
                context={
                    "kind": "GrainToDateAlignmentPolicyUnsupported",
                    "alignment_kind": alignment.kind,
                    "alignment_mode": alignment.mode,
                },
            )
        ]
    if current.meta.semantic_kind != baseline.meta.semantic_kind:
        return [
            SemanticKindMismatchError(
                message=(
                    "compare requires matching semantic_kind, got "
                    f"{current.meta.semantic_kind!r} and {baseline.meta.semantic_kind!r}"
                ),
            )
        ]
    kind = current.meta.semantic_kind
    if kind in {"segmented", "panel"}:
        current_dimensions = _dimension_columns(current)
        baseline_dimensions = _dimension_columns(baseline)
        if current_dimensions != baseline_dimensions:
            return [
                SegmentDimensionMismatchError(
                    message="compare requires matching segment dimension columns",
                    context={
                        "kind": "SegmentDimensionMismatch",
                        "current_dimensions": current_dimensions,
                        "baseline_dimensions": baseline_dimensions,
                    },
                )
            ]
        current_dimension_ids = _requested_dimension_ids(current)
        baseline_dimension_ids = _requested_dimension_ids(baseline)
        if current_dimension_ids != baseline_dimension_ids:
            return [
                SegmentDimensionMismatchError(
                    message="compare requires matching requested dimension identities",
                    context={
                        "kind": "SegmentDimensionIdentityMismatch",
                        "current_dimensions": current_dimension_ids,
                        "baseline_dimensions": baseline_dimension_ids,
                    },
                )
            ]
    if kind in {"time_series", "panel"}:
        current_time_dimension = _time_axis_identity(current)
        baseline_time_dimension = _time_axis_identity(baseline)
        if (
            current_time_dimension is not None
            and baseline_time_dimension is not None
            and current_time_dimension != baseline_time_dimension
        ):
            return [
                AlignmentFailedError(
                    message="compare requires matching explicit time dimension identities",
                    context={
                        "kind": "TimeDimensionIdentityMismatch",
                        "current_time_dimension": current_time_dimension,
                        "baseline_time_dimension": baseline_time_dimension,
                    },
                )
            ]
        current_report_tz = _observe_report_tz(current)
        baseline_report_tz = _observe_report_tz(baseline)
        if current_report_tz != baseline_report_tz:
            return [
                AlignmentFailedError(
                    message="compare requires matching observation report timezones",
                    context={
                        "kind": "ReportTimezoneMismatch",
                        "current_report_tz": current_report_tz,
                        "baseline_report_tz": baseline_report_tz,
                    },
                )
            ]
    if kind == "panel":
        current_grain, baseline_grain = _panel_grains(current, baseline)
        if current_grain != baseline_grain:
            return [
                PanelGrainMismatchError(
                    message="panel compare requires matching time grain",
                    context={
                        "kind": "PanelGrainMismatch",
                        "current_grain": current_grain,
                        "baseline_grain": baseline_grain,
                    },
                )
            ]
    if kind == "segmented" and alignment.kind != "window_bucket":
        return [
            AlignmentPolicyNotApplicableError(
                message="segmented compare supports only window_bucket alignment",
                context={
                    "kind": "AlignmentPolicyNotApplicable",
                    "semantic_kind": "segmented",
                    "alignment_kind": alignment.kind,
                },
            )
        ]
    if kind == "scalar" and alignment.kind != "window_bucket":
        return [
            SemanticKindMismatchError(
                message="calendar-backed compare alignment requires time_series MetricFrames",
                context={
                    "kind": "CalendarAlignRequiresTimeSeries",
                    "expected_kind": "time_series",
                    "got_kind": {
                        "current": current.meta.semantic_kind,
                        "baseline": baseline.meta.semantic_kind,
                    },
                },
            )
        ]
    current_comparable = current.meta.comparable_value_semantics
    baseline_comparable = baseline.meta.comparable_value_semantics
    if current_comparable is None or baseline_comparable is None:
        return [
            SemanticKindMismatchError(
                message="compare requires complete persisted comparable value semantics.",
                hint="Re-observe both inputs under the current artifact contract.",
                context={
                    "current_has_comparable_semantics": current_comparable is not None,
                    "baseline_has_comparable_semantics": baseline_comparable is not None,
                },
            )
        ]
    if current_comparable.fingerprint != baseline_comparable.fingerprint:
        return [
            SemanticKindMismatchError(
                message=(
                    "compare requires equal persisted comparable value semantics; got "
                    f"{current.meta.metric_id!r} and {baseline.meta.metric_id!r}"
                ),
                context={
                    "current_comparable_fingerprint": current_comparable.fingerprint,
                    "baseline_comparable_fingerprint": baseline_comparable.fingerprint,
                },
            )
        ]
    return []


def validate_decompose_columns(
    frame: DeltaFrame,
    axis_id: str,
    *,
    source_df: pd.DataFrame,
) -> list[AnalysisError]:
    """Column-level decompose checks (axis resolves, delta numeric, panel axis)."""
    from marivo.analysis.intents._derived import require_numeric_column
    from marivo.analysis.intents.decompose import (
        _bucket_column_for_panel,
        _effective_component_axis_column,
        _panel_dimension_columns,
    )

    available_columns = [str(column) for column in source_df.columns]
    normalized_axis = axis_id.rsplit(".", 1)[-1]
    axis_column = _effective_component_axis_column(frame, axis_id, available_columns)
    if axis_column is None:
        return [
            SemanticKindMismatchError(
                message="decompose axis column does not exist in the DeltaFrame",
                hint=(
                    f"Use axis=session.catalog.get('dimension.<dimension_id>').ref for {normalized_axis!r} "
                    "if that column exists in the DeltaFrame."
                ),
                context={
                    "requested_axis": axis_id,
                    "normalized_axis": normalized_axis,
                    "available_columns": available_columns,
                },
            )
        ]

    try:
        require_numeric_column(source_df, "delta", purpose="decompose")
    except SemanticKindMismatchError as numeric_error:
        return [numeric_error]

    if frame.meta.semantic_kind == "panel":
        bucket_column = _bucket_column_for_panel(frame)
        dim_columns = _panel_dimension_columns(frame)
        if axis_column not in dim_columns:
            return [
                AxisNotInPanelDimensionsError(
                    message="decompose axis is not a panel dimension",
                    context={
                        "axis": axis_column,
                        "available_dimensions": dim_columns,
                    },
                )
            ]
        if bucket_column not in source_df.columns:
            return [
                SemanticKindMismatchError(
                    message="decompose panel bucket column does not exist in the DeltaFrame",
                    context={"bucket_column": bucket_column, "columns": list(source_df.columns)},
                )
            ]
    return []


def validate_decompose_axes_columns(
    frame: DeltaFrame,
    axis_ids: list[str],
    *,
    source_df: pd.DataFrame,
) -> list[AnalysisError]:
    """Column-level decompose checks for multiple axes (aggregates per-axis issues)."""
    errors: list[AnalysisError] = []
    for axis_id in axis_ids:
        errors.extend(validate_decompose_columns(frame, axis_id, source_df=source_df))
    return errors
