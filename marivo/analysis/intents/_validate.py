"""Pre-submit validators for analysis intents (no backend execution).

Each validator reads only frame metadata + policy and returns the first
incompatibility as a one-element list of constructed AnalysisError instances
(or [] when valid), mirroring the intents' fail-fast raise. Adapters support
both fail-fast raising and structured ValidationIssue conversion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from marivo.analysis.errors import (
    AlignmentPolicyNotApplicableError,
    AnalysisError,
    AxisNotInPanelDimensionsError,
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
            details=error.details,
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
        details={
            "intent": intent,
            "expected_arity": 1,
            "got_arity": len(metric_ids),
            "metrics": metric_ids,
        },
    )


def validate_compare(
    current: MetricFrame,
    baseline: MetricFrame,
    *,
    alignment: AlignmentPolicy,
) -> list[AnalysisError]:
    """Shape/policy compatibility for compare; returns the first issue or []."""
    from marivo.analysis.intents._window_pairs import _panel_grains
    from marivo.analysis.intents.compare import _dimension_columns

    if current.meta.metric_id != baseline.meta.metric_id:
        return [
            SemanticKindMismatchError(
                message=(
                    "compare requires the same metric, got "
                    f"{current.meta.metric_id!r} and {baseline.meta.metric_id!r}"
                ),
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
                    details={
                        "kind": "SegmentDimensionMismatch",
                        "current_dimensions": current_dimensions,
                        "baseline_dimensions": baseline_dimensions,
                    },
                )
            ]
    if kind == "panel":
        current_grain, baseline_grain = _panel_grains(current, baseline)
        if current_grain != baseline_grain:
            return [
                PanelGrainMismatchError(
                    message="panel compare requires matching time grain",
                    details={
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
                details={
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
                details={
                    "kind": "CalendarAlignRequiresTimeSeries",
                    "expected_kind": "time_series",
                    "got_kind": {
                        "current": current.meta.semantic_kind,
                        "baseline": baseline.meta.semantic_kind,
                    },
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
                details={
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
                    details={
                        "axis": axis_column,
                        "available_dimensions": dim_columns,
                    },
                )
            ]
        if bucket_column not in source_df.columns:
            return [
                SemanticKindMismatchError(
                    message="decompose panel bucket column does not exist in the DeltaFrame",
                    details={"bucket_column": bucket_column, "columns": list(source_df.columns)},
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
