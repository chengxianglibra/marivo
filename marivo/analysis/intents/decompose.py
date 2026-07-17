"""Decompose DeltaFrames into AttributionFrames."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from typing import Any

import numpy as np
import pandas as pd

from marivo.analysis.errors import (
    AnalysisError,
    AnalysisRepair,
    AttributionAdditivityError,
    ComponentDecompositionError,
    CumulativeFrameUnsupportedError,
    SemanticKindMismatchError,
)
from marivo.analysis.followups import BlockingIssue
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.component import (
    ComponentFrame,
    resolve_role_column_name,
    resolve_role_columns,
)
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.intents._attribution_mode import AttributionMode, validate_attribution_mode
from marivo.analysis.intents._derived import (
    ensure_frame_in_session,
    persist_attribution_frame,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis.intents._validate import (
    raise_first,
    validate_decompose_axes_columns,
    validate_decompose_columns,
)
from marivo.analysis.semantic_inputs import DimensionInput, normalize_dimension_boundary
from marivo.analysis.session._load import load_frame
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.introspection.live.model import LiveHelpTarget


def _bucket_column_for_panel(frame: DeltaFrame) -> str:
    axes = frame.meta.alignment.get("axes", {})
    if isinstance(axes, dict):
        for axis in axes.values():
            if isinstance(axis, dict) and axis.get("role") == "time":
                column = axis.get("column")
                if isinstance(column, str) and column:
                    return column
    return "bucket_start"


def _panel_dimension_columns(frame: DeltaFrame) -> list[str]:
    axes = frame.meta.alignment.get("axes", {})
    if not isinstance(axes, dict):
        return []
    columns: list[str] = []
    for axis in axes.values():
        if not isinstance(axis, dict) or axis.get("role") != "dimension":
            continue
        column = axis.get("column")
        if isinstance(column, str) and column:
            columns.append(column)
    return sorted(columns)


def _normalize_axis_boundary(session: Session, axis: DimensionInput) -> str:
    return normalize_dimension_boundary(session.catalog, axis, argument="axis")


def _resolve_axis_column(frame: DeltaFrame, axis_id: str, columns: list[str]) -> str | None:
    requested = axis_id
    if requested in columns:
        return requested

    axes = frame.meta.alignment.get("axes", {})
    if isinstance(axes, dict):
        for axis_id, axis_meta in axes.items():
            if not isinstance(axis_meta, dict):
                continue
            column = axis_meta.get("column")
            if not isinstance(column, str) or column not in columns:
                continue
            ref = axis_meta.get("ref")
            time_dim = axis_meta.get("time_dimension")
            candidates = [str(axis_id), column]
            if isinstance(ref, str):
                candidates.append(ref)
            if isinstance(time_dim, str):
                candidates.append(time_dim)
                # Also match the leaf of a dotted requested axis
                # against time_dimension (e.g. "sales.orders.created_at"
                # matches time_dimension="created_at").
                requested_leaf = requested.rsplit(".", 1)[-1]
                if requested_leaf == time_dim:
                    candidates.append(requested)
            if requested in candidates:
                return column

    axis_leaf = requested.rsplit(".", 1)[-1]
    if axis_leaf in columns:
        return axis_leaf
    return None


def _effective_component_axis_column(
    frame: DeltaFrame,
    axis_id: str,
    columns: list[str],
) -> str | None:
    resolved = _resolve_axis_column(frame, axis_id, columns)
    if resolved is not None:
        return resolved
    axis_leaf = axis_id.rsplit(".", 1)[-1]
    if frame.meta.semantic_kind == "time_series" and axis_leaf in (
        "bucket_start",
        "bucket_start_a",
    ):
        if "bucket_start" in columns:
            return "bucket_start"
        if "bucket_start_a" in columns:
            return "bucket_start_a"
    if axis_leaf == "bucket_start" and "bucket_start_a" in columns:
        return "bucket_start_a"
    return None


def _component_bucket_column(frame: DeltaFrame, columns: list[str]) -> str | None:
    if frame.meta.semantic_kind not in {"time_series", "panel"}:
        return None
    if "bucket_start" in columns:
        return "bucket_start"
    if "bucket_start_a" in columns:
        return "bucket_start_a"
    return None


def _load_delta_component_frame(frame: DeltaFrame, *, session: Session) -> ComponentFrame | None:
    if frame.meta.component_ref is None:
        return None
    loaded = load_frame(frame.meta.component_ref, session=session)
    if not isinstance(loaded, ComponentFrame):
        raise ComponentDecompositionError(
            message="delta component_ref did not resolve to a ComponentFrame",
            context={
                "delta_ref": frame.ref,
                "component_ref": frame.meta.component_ref,
                "loaded_kind": loaded.meta.kind,
            },
        )
    return loaded


_NON_LINEAR_FOLD_RECOMMENDED_PATH = (
    "Use a component-aware derived ratio or weighted-average metric for mix "
    "attribution, or attribute numerator and denominator separately and "
    "synthesize the ratio externally."
)


def _non_linear_fold_labels(frame: DeltaFrame) -> list[str]:
    fold = getattr(frame.meta, "fold", None)
    component_folds = getattr(frame.meta, "component_folds", [])
    entries: list[dict[str, Any]] = []
    if isinstance(fold, dict):
        entries.append(fold)
    entries.extend(item for item in component_folds if isinstance(item, dict))
    non_linear: list[str] = []
    for entry in entries:
        label = entry.get("time_fold")
        if not label or str(label) == "derived":
            continue
        fold_kind = entry.get("fold_kind")
        # Structured fold_kind (populated by observe): only 'mean' is linear.
        # Legacy payload without fold_kind: only an exact 'mean' label is linear,
        # avoiding the old startswith('mean') prefix match silently passing
        # labels like 'mean_weighted'.
        is_linear = (fold_kind == "mean") if fold_kind is not None else (str(label) == "mean")
        if not is_linear:
            non_linear.append(str(label))
    return non_linear


def _component_allows_non_linear_fold(component: ComponentFrame | None) -> bool:
    return component is not None and component.meta.composition_kind in {
        "ratio",
        "weighted_average",
    }


def _raise_non_linear_fold_error(frame: DeltaFrame, fold_labels: list[str]) -> None:
    raise ComponentDecompositionError(
        message=(
            "decompose cannot sum non-linear sampled time fold deltas by axis; "
            "component-aware ratio or weighted-average deltas can use mix attribution"
        ),
        context={
            "reason": "non_linear_time_fold",
            "delta_ref": frame.ref,
            "time_folds": fold_labels,
            "supported_component_kinds": ["ratio", "weighted_average"],
            "recommended_path": _NON_LINEAR_FOLD_RECOMMENDED_PATH,
        },
    )


def _mean_fold_coverage_warning(frame: DeltaFrame, session: Session) -> list[BlockingIssue]:
    """Warn (non-blocking) when a mean-fold delta decomposes segments whose
    per-window sample counts differ.

    Sum-of-segment-means equals overall-mean-of-sums only when every segment
    has the same sample count; missing samples (device up/down, collection
    gaps) make the contribution decomposition approximate. The warning is
    best-effort: it requires the current/baseline source frames to be
    loadable and to carry a coverage_summary (hand-built test deltas without
    real source frames are skipped silently).
    """
    fold = getattr(frame.meta, "fold", None)
    if not isinstance(fold, dict):
        return []
    fold_kind = fold.get("fold_kind")
    if fold_kind is not None:
        is_mean = fold_kind == "mean"
    else:
        is_mean = str(fold.get("time_fold", "")) == "mean"
    if not is_mean:
        return []

    summaries: dict[str, dict[str, Any]] = {}
    for side, ref in (
        ("current", frame.meta.source_current_ref),
        ("baseline", frame.meta.source_baseline_ref),
    ):
        try:
            loaded = load_frame(ref, session=session)
        except AnalysisError:
            return []
        summary = getattr(loaded.meta, "coverage_summary", None)
        if not isinstance(summary, dict):
            return []
        summaries[side] = summary

    def _uneven(summary: dict[str, Any]) -> bool:
        try:
            return float(summary["min"]) < float(summary["avg"])
        except (TypeError, ValueError, KeyError):
            return False

    if not (_uneven(summaries["current"]) or _uneven(summaries["baseline"])):
        return []

    return [
        BlockingIssue(
            issue_id=f"mean_fold_uneven_coverage:{frame.ref}",
            kind="comparability",
            severity="warning",
            source_refs=[frame.ref],
            message=(
                "mean-fold decomposition is approximate: per-segment sample "
                "counts differ across the window"
            ),
            payload={
                "reason": "mean_fold_uneven_coverage",
                "current_coverage_summary": summaries["current"],
                "baseline_coverage_summary": summaries["baseline"],
            },
        )
    ]


def _status_time_dimension(frame: DeltaFrame) -> str | None:
    if frame.meta.status_time_dimension is not None:
        return frame.meta.status_time_dimension
    fold = frame.meta.fold
    if isinstance(fold, dict):
        value = fold.get("status_time_dimension")
        if isinstance(value, str) and value:
            return value
    return None


def _raise_attribution_additivity_error(
    frame: DeltaFrame,
    *,
    axes: list[str],
    reason: str,
    status_time_dimension: str | None = None,
    component: ComponentFrame | None = None,
) -> None:
    if status_time_dimension is None:
        status_time_dimension = _status_time_dimension(frame)
    if reason == "missing_additivity_metadata":
        message = "decompose requires persisted additivity metadata before summing deltas by axis"
        action = (
            "Re-run observe and compare with the current semantic model so the DeltaFrame "
            "carries additivity metadata, then retry attribute."
        )
    elif reason == "semi_additive_time_axis":
        message = "decompose cannot sum a semi-additive metric delta over its status time axis"
        action = (
            "Attribute over non-time dimensions only, or model additive components and "
            "attribute those components separately."
        )
    else:
        message = "decompose cannot sum a non-additive metric delta by axis"
        action = (
            "Model the metric as a component-aware ratio or weighted average, or attribute "
            "its additive numerator and denominator separately."
        )
    composition_kind: str | None = (
        component.meta.composition_kind if component is not None else None
    )
    if composition_kind is None and isinstance(frame.meta.composition, dict):
        persisted_kind = frame.meta.composition.get("kind")
        if isinstance(persisted_kind, str):
            composition_kind = persisted_kind
    raise AttributionAdditivityError(
        message=message,
        expected="an additive delta or a component-aware ratio/weighted-average delta",
        received=(
            f"additivity={frame.meta.additivity!r}, aggregation={frame.meta.aggregation!r}, "
            f"composition_kind={composition_kind!r}"
        ),
        location="session.attribute",
        repair=AnalysisRepair(
            kind="retry",
            action=action,
            help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
        ),
        context={
            "reason": reason,
            "delta_ref": frame.ref,
            "metric": frame.meta.metric_id,
            "metric_id": frame.meta.metric_id,
            "additivity": frame.meta.additivity,
            "aggregation": frame.meta.aggregation,
            "axes": axes,
            "status_time_dimension": status_time_dimension,
            "composition_kind": composition_kind,
            "supported_component_kinds": ["ratio", "weighted_average"],
            "recommended_path": action,
        },
    )


def _validate_attribution_additivity(
    frame: DeltaFrame,
    *,
    axes: list[str],
    component: ComponentFrame | None,
) -> None:
    if _component_allows_non_linear_fold(component):
        return
    if frame.meta.additivity == "additive":
        return
    if frame.meta.additivity == "semi_additive":
        status_time_dimension = _status_time_dimension(frame)
        if status_time_dimension is None:
            _raise_attribution_additivity_error(
                frame,
                axes=axes,
                reason="missing_additivity_metadata",
                component=component,
            )
        if status_time_dimension not in axes:
            return
        _raise_attribution_additivity_error(
            frame,
            axes=axes,
            reason="semi_additive_time_axis",
            status_time_dimension=status_time_dimension,
            component=component,
        )
    _raise_attribution_additivity_error(
        frame,
        axes=axes,
        reason=(
            "missing_additivity_metadata"
            if frame.meta.additivity is None
            else "non_additive_metric"
        ),
        component=component,
    )


def _validate_attribution_semantics(
    frame: DeltaFrame,
    *,
    axes: list[str],
    session: Session,
) -> ComponentFrame | None:
    """Validate persisted attribution semantics before any materialization."""
    component = _load_delta_component_frame(frame, session=session)
    non_linear_fold_labels = _non_linear_fold_labels(frame)
    if non_linear_fold_labels and not _component_allows_non_linear_fold(component):
        _raise_non_linear_fold_error(frame, non_linear_fold_labels)
    _validate_attribution_additivity(frame, axes=axes, component=component)
    return component


def _component_role_column_name(component: ComponentFrame, role: str) -> str:
    return resolve_role_column_name(component.meta.components, role)


def _component_measure_role(component: ComponentFrame) -> str:
    if component.meta.composition_kind == "ratio":
        return _component_role_column_name(component, "denominator")
    if component.meta.composition_kind == "weighted_average":
        return _component_role_column_name(component, "weight")
    raise ComponentDecompositionError(
        message="unsupported component composition kind",
        context={"composition_kind": component.meta.composition_kind},
    )


def _component_value_role(component: ComponentFrame) -> str:
    """Return the value-role column name for the mix math.

    ratio uses 'numerator', weighted_average uses 'value'.
    """
    if component.meta.composition_kind == "ratio":
        return "numerator"
    if component.meta.composition_kind == "weighted_average":
        return "value"
    raise ComponentDecompositionError(
        message="unsupported component composition kind for value role",
        context={"composition_kind": component.meta.composition_kind},
    )


def _component_method(component: ComponentFrame) -> str:
    if component.meta.composition_kind == "ratio":
        return "ratio_mix"
    if component.meta.composition_kind == "weighted_average":
        return "weighted_mix"
    raise ComponentDecompositionError(
        message="unsupported component composition kind",
        context={"composition_kind": component.meta.composition_kind},
    )


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numerator = pd.to_numeric(numerator, errors="coerce")
    denominator = pd.to_numeric(denominator, errors="coerce")
    return pd.Series(
        np.where(denominator.notna() & (denominator != 0), numerator / denominator, np.nan),
        index=numerator.index,
    )


def _infer_delta_value_column_name(component: ComponentFrame) -> str:
    """Infer the metric-name value column from the component delta frame columns.

    Looks for a ``current_<name>`` column whose ``<name>`` is not a declared
    decomposition component metric name.
    """
    known_component_names = set(resolve_role_columns(component.meta.components))
    for column in component._df.columns:
        if column.startswith("current_"):
            name = column[len("current_") :]
            if name not in known_component_names:
                return name
    raise ComponentDecompositionError(
        message="component delta frame has no metric value column",
        context={"component_ref": component.ref},
    )


def _component_mix_output_for_df(
    *,
    df: pd.DataFrame,
    component: ComponentFrame,
    axis_column: str,
) -> pd.DataFrame:
    share_role = _component_measure_role(component)
    numerator_name = _component_role_column_name(component, _component_value_role(component))
    value_name = _infer_delta_value_column_name(component)
    required = [
        axis_column,
        f"current_{numerator_name}",
        f"baseline_{numerator_name}",
        f"current_{share_role}",
        f"baseline_{share_role}",
        f"current_{value_name}",
        f"baseline_{value_name}",
    ]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ComponentDecompositionError(
            message="component-aware decompose requires component delta columns",
            context={"component_ref": component.ref, "missing_columns": missing},
        )
    output = df[required].copy()
    current_basis = pd.to_numeric(output[f"current_{share_role}"], errors="coerce")
    baseline_basis = pd.to_numeric(output[f"baseline_{share_role}"], errors="coerce")
    current_total = current_basis.sum(skipna=True)
    baseline_total = baseline_basis.sum(skipna=True)
    if not np.isfinite(current_total) or current_total == 0:
        current_share = pd.Series(np.nan, index=output.index)
    else:
        current_share = current_basis / current_total
    if not np.isfinite(baseline_total) or baseline_total == 0:
        baseline_share = pd.Series(np.nan, index=output.index)
    else:
        baseline_share = baseline_basis / baseline_total
    current_value = _safe_divide(output[f"current_{numerator_name}"], current_basis)
    baseline_value = _safe_divide(output[f"baseline_{numerator_name}"], baseline_basis)
    output["contribution"] = current_share * current_value - baseline_share * baseline_value
    output["value_effect"] = current_share * (current_value - baseline_value)
    output["mix_effect"] = (current_share - baseline_share) * baseline_value
    output["residual"] = output["contribution"] - output["value_effect"] - output["mix_effect"]
    valid = output["contribution"].notna()
    if not bool(valid.any()):
        raise ComponentDecompositionError(
            message="component-aware decompose could not form any valid contribution rows",
            context={"component_ref": component.ref, "share_role": share_role},
        )
    total = float(output.loc[valid, "contribution"].sum())
    output["pct_contribution"] = np.where(
        valid & (total != 0),
        output["contribution"] / total,
        np.nan,
    )
    output["current_share"] = current_share
    output["baseline_share"] = baseline_share
    output = output.reindex(
        output["contribution"].abs().sort_values(ascending=False).index
    ).reset_index(drop=True)
    output["rank"] = range(1, len(output) + 1)
    return output[
        [
            axis_column,
            "contribution",
            "pct_contribution",
            "value_effect",
            "mix_effect",
            "residual",
            f"current_{numerator_name}",
            f"baseline_{numerator_name}",
            f"current_{share_role}",
            f"baseline_{share_role}",
            f"current_{value_name}",
            f"baseline_{value_name}",
            "current_share",
            "baseline_share",
            "rank",
        ]
    ]


def _component_linear_output_for_df(
    *,
    df: pd.DataFrame,
    component: ComponentFrame,
    axis_column: str,
) -> pd.DataFrame:
    """Additive attribution: each component term contributes sign*(current-baseline)."""
    out = df[[axis_column]].copy()
    total = pd.Series(0.0, index=df.index)
    for sign, ref_id in component.meta.linear_terms:
        role = next(
            (r for r, cid in component.meta.components.items() if cid == ref_id),
            None,
        )
        if role is None:
            continue
        col = _component_role_column_name(component, role)
        cur_key = f"current_{col}"
        base_key = f"baseline_{col}"
        if cur_key not in df.columns or base_key not in df.columns:
            continue
        cur = pd.to_numeric(df[cur_key], errors="coerce")
        base = pd.to_numeric(df[base_key], errors="coerce")
        contrib = (cur - base) * (1.0 if sign == "+" else -1.0)
        total = total.add(contrib, fill_value=0.0)
    out["contribution"] = total
    out["value_effect"] = total  # additive: all contribution is value-effect
    out["mix_effect"] = 0.0
    out["residual"] = 0.0
    valid = out["contribution"].notna()
    total_val = float(out.loc[valid, "contribution"].sum()) if valid.any() else 0.0
    out["pct_contribution"] = np.where(
        valid & (total_val != 0),
        out["contribution"] / total_val,
        np.nan,
    )
    out = out.reindex(out["contribution"].abs().sort_values(ascending=False).index).reset_index(
        drop=True
    )
    out["rank"] = range(1, len(out) + 1)
    return out[
        [
            axis_column,
            "contribution",
            "pct_contribution",
            "value_effect",
            "mix_effect",
            "residual",
            "rank",
        ]
    ]


def _component_mix_output(
    *,
    component: ComponentFrame,
    axis_column: str,
    bucket_column: str | None = None,
) -> pd.DataFrame:
    df = component._dataframe_copy()
    if bucket_column is None:
        return _component_mix_output_for_df(
            df=df,
            component=component,
            axis_column=axis_column,
        )

    if bucket_column not in df.columns:
        raise ComponentDecompositionError(
            message="component-aware panel decompose requires a bucket column",
            context={"component_ref": component.ref, "bucket_column": bucket_column},
        )

    pieces: list[pd.DataFrame] = []
    for bucket_value, bucket_df in df.groupby(bucket_column, dropna=False, sort=True):
        piece = _component_mix_output_for_df(
            df=bucket_df,
            component=component,
            axis_column=axis_column,
        )
        piece.insert(0, bucket_column, bucket_value)
        pieces.append(piece)
    if not pieces:
        raise ComponentDecompositionError(
            message="component-aware decompose could not form any bucket groups",
            context={"component_ref": component.ref, "bucket_column": bucket_column},
        )
    return pd.concat(pieces, ignore_index=True)


def _component_joint_mix_output_for_df(
    *,
    df: pd.DataFrame,
    component: ComponentFrame,
    axis_columns: list[str],
) -> pd.DataFrame:
    """Compute mix effects after aggregating components by a joint axis tuple."""
    share_role = _component_measure_role(component)
    numerator_name = _component_role_column_name(component, _component_value_role(component))
    value_name = _infer_delta_value_column_name(component)
    numeric_columns = [
        f"current_{numerator_name}",
        f"baseline_{numerator_name}",
        f"current_{share_role}",
        f"baseline_{share_role}",
    ]
    required = [*axis_columns, *numeric_columns]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ComponentDecompositionError(
            message="component-aware decompose requires component delta columns",
            context={"component_ref": component.ref, "missing_columns": missing},
        )
    output = (
        df[required]
        .groupby(axis_columns, dropna=False, sort=False)[numeric_columns]
        .sum(min_count=1)
        .reset_index()
    )
    current_basis = pd.to_numeric(output[f"current_{share_role}"], errors="coerce")
    baseline_basis = pd.to_numeric(output[f"baseline_{share_role}"], errors="coerce")
    current_total = current_basis.sum(skipna=True)
    baseline_total = baseline_basis.sum(skipna=True)
    current_share = (
        current_basis / current_total
        if np.isfinite(current_total) and current_total != 0
        else pd.Series(np.nan, index=output.index)
    )
    baseline_share = (
        baseline_basis / baseline_total
        if np.isfinite(baseline_total) and baseline_total != 0
        else pd.Series(np.nan, index=output.index)
    )
    current_value = _safe_divide(output[f"current_{numerator_name}"], current_basis)
    baseline_value = _safe_divide(output[f"baseline_{numerator_name}"], baseline_basis)
    output[f"current_{value_name}"] = current_value
    output[f"baseline_{value_name}"] = baseline_value
    output["contribution"] = current_share * current_value - baseline_share * baseline_value
    output["value_effect"] = current_share * (current_value - baseline_value)
    output["mix_effect"] = (current_share - baseline_share) * baseline_value
    output["residual"] = output["contribution"] - output["value_effect"] - output["mix_effect"]
    valid = output["contribution"].notna()
    if not bool(valid.any()):
        raise ComponentDecompositionError(
            message="component-aware decompose could not form any valid contribution rows",
            context={"component_ref": component.ref, "share_role": share_role},
        )
    total = float(output.loc[valid, "contribution"].sum())
    output["pct_contribution"] = np.where(
        valid & (total != 0), output["contribution"] / total, np.nan
    )
    output["current_share"] = current_share
    output["baseline_share"] = baseline_share
    output = output.reindex(
        output["contribution"].abs().sort_values(ascending=False).index
    ).reset_index(drop=True)
    output["rank"] = range(1, len(output) + 1)
    return output[
        [
            *axis_columns,
            "contribution",
            "pct_contribution",
            "value_effect",
            "mix_effect",
            "residual",
            f"current_{numerator_name}",
            f"baseline_{numerator_name}",
            f"current_{share_role}",
            f"baseline_{share_role}",
            f"current_{value_name}",
            f"baseline_{value_name}",
            "current_share",
            "baseline_share",
            "rank",
        ]
    ]


def _component_joint_linear_output_for_df(
    *,
    df: pd.DataFrame,
    component: ComponentFrame,
    axis_columns: list[str],
) -> pd.DataFrame:
    """Compute additive component effects after aggregating by a joint axis tuple."""
    component_columns = [
        _component_role_column_name(component, role)
        for _, ref_id in component.meta.linear_terms
        for role, component_ref in component.meta.components.items()
        if component_ref == ref_id
    ]
    numeric_columns = [
        column
        for name in component_columns
        for column in (f"current_{name}", f"baseline_{name}")
        if column in df.columns
    ]
    grouped = (
        df[[*axis_columns, *numeric_columns]]
        .groupby(axis_columns, dropna=False, sort=False)[numeric_columns]
        .sum(min_count=1)
        .reset_index()
    )
    out = grouped[axis_columns].copy()
    total = pd.Series(0.0, index=grouped.index)
    for sign, ref_id in component.meta.linear_terms:
        role = next(
            (
                role
                for role, component_ref in component.meta.components.items()
                if component_ref == ref_id
            ),
            None,
        )
        if role is None:
            continue
        column = _component_role_column_name(component, role)
        current_key = f"current_{column}"
        baseline_key = f"baseline_{column}"
        if current_key not in grouped.columns or baseline_key not in grouped.columns:
            continue
        total = total.add(
            (
                pd.to_numeric(grouped[current_key], errors="coerce")
                - pd.to_numeric(grouped[baseline_key], errors="coerce")
            )
            * (1.0 if sign == "+" else -1.0),
            fill_value=0.0,
        )
    out["contribution"] = total
    out["value_effect"] = total
    out["mix_effect"] = 0.0
    out["residual"] = 0.0
    total_value = float(out["contribution"].sum())
    out["pct_contribution"] = np.where(total_value != 0, out["contribution"] / total_value, np.nan)
    out = out.reindex(out["contribution"].abs().sort_values(ascending=False).index).reset_index(
        drop=True
    )
    out["rank"] = range(1, len(out) + 1)
    return out[
        [
            *axis_columns,
            "contribution",
            "pct_contribution",
            "value_effect",
            "mix_effect",
            "residual",
            "rank",
        ]
    ]


def _component_multi_axis_output(
    *,
    component: ComponentFrame,
    axis_columns: list[str],
    mode: AttributionMode,
    bucket_column: str | None,
) -> pd.DataFrame:
    """Produce joint or hierarchy component attribution, preserving panel buckets."""
    df = component._dataframe_copy()
    if component.meta.composition_kind == "linear":

        def joint_for_df(values: pd.DataFrame, axes: list[str]) -> pd.DataFrame:
            return _component_joint_linear_output_for_df(
                df=values, component=component, axis_columns=axes
            )
    else:

        def joint_for_df(values: pd.DataFrame, axes: list[str]) -> pd.DataFrame:
            return _component_joint_mix_output_for_df(
                df=values, component=component, axis_columns=axes
            )

    def output_for_df(values: pd.DataFrame) -> pd.DataFrame:
        if mode == "joint":
            return joint_for_df(values, axis_columns)
        pieces: list[pd.DataFrame] = []
        for level in range(1, len(axis_columns) + 1):
            prefix = axis_columns[:level]
            piece = joint_for_df(values, prefix)
            effect_columns = [column for column in piece.columns if column not in prefix]
            for axis_column in axis_columns[level:]:
                piece[axis_column] = pd.NA
            piece.insert(0, "path", piece[prefix].astype(str).agg(" > ".join, axis=1))
            piece.insert(0, "driver", piece[axis_columns[level - 1]])
            piece.insert(0, "axis", axis_columns[level - 1])
            piece.insert(0, "level", level)
            pieces.append(
                piece[["level", "axis", "driver", "path", *axis_columns, *effect_columns]]
            )
        return pd.concat(pieces, ignore_index=True)

    if bucket_column is None:
        return output_for_df(df)
    if bucket_column not in df.columns:
        raise ComponentDecompositionError(
            message="component-aware panel decompose requires a bucket column",
            context={"component_ref": component.ref, "bucket_column": bucket_column},
        )
    pieces = []
    for bucket_value, bucket_df in df.groupby(bucket_column, dropna=False, sort=True):
        piece = output_for_df(bucket_df)
        piece.insert(0, bucket_column, bucket_value)
        pieces.append(piece)
    if not pieces:
        raise ComponentDecompositionError(
            message="component-aware decompose could not form any bucket groups",
            context={"component_ref": component.ref, "bucket_column": bucket_column},
        )
    return pd.concat(pieces, ignore_index=True)


def _normalize_axes_boundary(
    session: Session,
    axes: list[DimensionInput] | None,
    axis: DimensionInput | None,
) -> list[str]:
    if axes is None and axis is not None:
        axes = [axis]
    if not axes:
        raise SemanticKindMismatchError(
            message="decompose requires at least one axis",
            context={"argument": "axes"},
        )
    axis_ids = [_normalize_axis_boundary(session, item) for item in axes]
    if len(set(axis_ids)) != len(axis_ids):
        raise SemanticKindMismatchError(
            message="decompose axes must be distinct",
            context={"argument": "axes", "reason": "duplicate_axes", "axes": axis_ids},
        )
    return axis_ids


def _axis_columns_for_delta(
    frame: DeltaFrame,
    axis_ids: list[str],
    *,
    source_df: pd.DataFrame,
) -> list[str]:
    available_columns = [str(column) for column in source_df.columns]
    axis_columns: list[str] = []
    for axis_id in axis_ids:
        axis_column = _effective_component_axis_column(frame, axis_id, available_columns)
        if axis_column is None:
            raise_first(validate_decompose_columns(frame, axis_id, source_df=source_df))
        assert axis_column is not None  # validated above; needed for type narrowing
        axis_columns.append(axis_column)
    if len(set(axis_columns)) != len(axis_columns):
        raise SemanticKindMismatchError(
            message="decompose axes must resolve to distinct columns",
            context={
                "argument": "axes",
                "reason": "duplicate_axis_columns",
                "axis_columns": axis_columns,
            },
        )
    return axis_columns


def _ordered_hierarchy_output(
    df: pd.DataFrame,
    *,
    axis_columns: list[str],
    value_column: str,
    bucket_column: str | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    bucket_values: list[object] = [None]
    if bucket_column is not None:
        bucket_values = list(df[bucket_column].drop_duplicates())

    for bucket_value in bucket_values:
        bucket_df = df if bucket_column is None else df[df[bucket_column] == bucket_value]
        denominator = float(bucket_df[value_column].sum())
        for level in range(1, len(axis_columns) + 1):
            group_columns = axis_columns[:level]
            grouped = (
                bucket_df.groupby(group_columns, dropna=False)[value_column]
                .sum()
                .reset_index()
                .rename(columns={value_column: "contribution"})
            )
            grouped["_abs_contribution"] = grouped["contribution"].abs()
            grouped = grouped.sort_values(
                "_abs_contribution",
                ascending=False,
                kind="mergesort",
            ).reset_index(drop=True)
            for rank_val, (_, row) in enumerate(grouped.iterrows(), start=1):
                path_parts = [str(row[column]) for column in group_columns]
                contribution = float(row["contribution"])
                output_row: dict[str, object] = {
                    "level": level,
                    "axis": axis_columns[level - 1],
                    "driver": row[axis_columns[level - 1]],
                    "path": " > ".join(path_parts),
                    "contribution": contribution,
                    "pct_contribution": contribution / denominator if denominator != 0 else np.nan,
                    "rank": rank_val,
                }
                if bucket_column is not None:
                    output_row[bucket_column] = bucket_value
                rows.append(output_row)

    columns = ["level", "axis", "driver", "path", "contribution", "pct_contribution", "rank"]
    if bucket_column is not None:
        columns = [bucket_column, *columns]
    output = pd.DataFrame(rows)
    if output.empty:
        output = pd.DataFrame(columns=columns)
    return output[columns]


def _joint_sum_output_for_df(
    df: pd.DataFrame,
    *,
    axis_columns: list[str],
    value_column: str,
) -> pd.DataFrame:
    """Aggregate an additive delta once for every complete axis combination."""
    output = (
        df.groupby(axis_columns, dropna=False, sort=False)[value_column]
        .sum()
        .reset_index()
        .rename(columns={value_column: "contribution"})
    )
    output["value_effect"] = output["contribution"]
    output["mix_effect"] = 0.0
    output["residual"] = 0.0
    total = float(output["contribution"].sum())
    output["pct_contribution"] = np.where(total != 0, output["contribution"] / total, np.nan)
    output = output.reindex(
        output["contribution"].abs().sort_values(ascending=False).index
    ).reset_index(drop=True)
    output["rank"] = range(1, len(output) + 1)
    return output[
        [
            *axis_columns,
            "contribution",
            "pct_contribution",
            "value_effect",
            "mix_effect",
            "residual",
            "rank",
        ]
    ]


def _multi_axis_joint_sum_output(
    df: pd.DataFrame,
    *,
    axis_columns: list[str],
    value_column: str,
    bucket_column: str | None = None,
) -> pd.DataFrame:
    """Apply joint additive attribution independently for each panel bucket."""
    if bucket_column is None:
        return _joint_sum_output_for_df(df, axis_columns=axis_columns, value_column=value_column)
    pieces: list[pd.DataFrame] = []
    for bucket_value, bucket_df in df.groupby(bucket_column, dropna=False, sort=True):
        piece = _joint_sum_output_for_df(
            bucket_df, axis_columns=axis_columns, value_column=value_column
        )
        piece.insert(0, bucket_column, bucket_value)
        pieces.append(piece)
    if not pieces:
        columns = [
            bucket_column,
            *axis_columns,
            "contribution",
            "pct_contribution",
            "value_effect",
            "mix_effect",
            "residual",
            "rank",
        ]
        return pd.DataFrame(columns=columns)
    return pd.concat(pieces, ignore_index=True)


def _multi_axis_hierarchy_output(
    df: pd.DataFrame,
    *,
    axis_columns: list[str],
    value_column: str,
    bucket_column: str | None = None,
) -> pd.DataFrame:
    """Emit additive hierarchy rows while retaining every requested axis column."""
    rows: list[dict[str, object]] = []
    bucket_values: list[object] = [None]
    if bucket_column is not None:
        bucket_values = list(df[bucket_column].drop_duplicates())
    for bucket_value in bucket_values:
        bucket_df = df if bucket_column is None else df[df[bucket_column] == bucket_value]
        denominator = float(bucket_df[value_column].sum())
        for level in range(1, len(axis_columns) + 1):
            prefix = axis_columns[:level]
            grouped = (
                bucket_df.groupby(prefix, dropna=False)[value_column]
                .sum()
                .reset_index()
                .rename(columns={value_column: "contribution"})
            )
            grouped = grouped.sort_values(
                "contribution", key=lambda values: values.abs(), ascending=False, kind="mergesort"
            ).reset_index(drop=True)
            for rank, (_, row) in enumerate(grouped.iterrows(), start=1):
                contribution = float(row["contribution"])
                output_row: dict[str, object] = {
                    "level": level,
                    "axis": axis_columns[level - 1],
                    "driver": row[axis_columns[level - 1]],
                    "path": " > ".join(str(row[column]) for column in prefix),
                    "contribution": contribution,
                    "pct_contribution": contribution / denominator if denominator != 0 else np.nan,
                    "value_effect": contribution,
                    "mix_effect": 0.0,
                    "residual": 0.0,
                    "rank": rank,
                }
                for axis_column in axis_columns:
                    output_row[axis_column] = row[axis_column] if axis_column in prefix else pd.NA
                if bucket_column is not None:
                    output_row[bucket_column] = bucket_value
                rows.append(output_row)
    columns = [
        "level",
        "axis",
        "driver",
        "path",
        *axis_columns,
        "contribution",
        "pct_contribution",
        "value_effect",
        "mix_effect",
        "residual",
        "rank",
    ]
    if bucket_column is not None:
        columns = [bucket_column, *columns]
    output = pd.DataFrame(rows)
    if output.empty:
        output = pd.DataFrame(columns=columns)
    return output[columns]


def decompose(
    frame: DeltaFrame,
    *,
    axes: list[DimensionInput] | None = None,
    axis: DimensionInput | None = None,
    mode: AttributionMode | None = None,
    session: Session | None = None,
    _intent: str = "decompose",
    _analysis_purpose: str | None = None,
    _params_extra: dict[str, object] | None = None,
) -> AttributionFrame:
    session = resolve_session(session)
    ensure_session_writable(session)
    if not isinstance(frame, DeltaFrame):
        raise SemanticKindMismatchError(message="decompose requires a DeltaFrame input")
    if frame.meta.cumulative is not None:
        raise CumulativeFrameUnsupportedError(
            intent=_intent,
            frame_ref=frame.ref,
            metric_id=frame.meta.metric_id,
            cumulative=frame.meta.cumulative,
        )
    axis_ids = _normalize_axes_boundary(session, axes, axis)
    validated_mode = validate_attribution_mode(axis_ids, mode, intent="decompose")
    params_extra = dict(_params_extra or {})
    ensure_frame_in_session(frame, session=session, label="decompose frame")
    if frame.meta.semantic_kind not in {"scalar", "time_series", "segmented", "panel"}:
        raise SemanticKindMismatchError(
            message=f"decompose does not support semantic_kind={frame.meta.semantic_kind!r}",
        )

    component = _validate_attribution_semantics(frame, axes=axis_ids, session=session)

    coverage_warnings = _mean_fold_coverage_warning(frame, session)

    started_at = datetime.now(UTC)
    started = monotonic()
    source_df = frame._dataframe_copy()
    value_column = require_numeric_column(source_df, "delta", purpose="decompose")
    raise_first(validate_decompose_axes_columns(frame, axis_ids, source_df=source_df))
    axis_columns = _axis_columns_for_delta(frame, axis_ids, source_df=source_df)

    # Component-aware path: ratio/weighted mix or linear additive attribution.
    if component is not None:
        component_columns = [str(column) for column in component._dataframe_copy().columns]
        component_axis_columns: list[str] = []
        missing_component_axes: list[str] = []
        for axis_id, axis_column in zip(axis_ids, axis_columns, strict=True):
            component_axis_column = _effective_component_axis_column(
                frame, axis_id, component_columns
            )
            if component_axis_column is None and axis_column in component_columns:
                component_axis_column = axis_column
            if component_axis_column is None:
                missing_component_axes.append(axis_id)
            else:
                component_axis_columns.append(component_axis_column)
        if missing_component_axes:
            raise ComponentDecompositionError(
                message="component-aware decompose could not resolve every requested axis",
                context={
                    "delta_ref": frame.ref,
                    "component_ref": component.ref,
                    "axes": axis_ids,
                    "missing_axes": missing_component_axes,
                    "available_component_columns": component_columns,
                },
            )
        bucket_column = None
        if frame.meta.semantic_kind == "panel":
            bucket_column = _component_bucket_column(frame, component_columns)
            if bucket_column is None:
                raise ComponentDecompositionError(
                    message="component-aware panel decompose requires a bucket column",
                    context={"delta_ref": frame.ref, "component_ref": component.ref},
                )
        if validated_mode is None and component.meta.composition_kind == "linear":
            output = _component_linear_output_for_df(
                df=component._dataframe_copy(),
                component=component,
                axis_column=component_axis_columns[0],
            )
            method = "sum"
        elif validated_mode is None:
            output = _component_mix_output(
                component=component,
                axis_column=component_axis_columns[0],
                bucket_column=bucket_column,
            )
            method = _component_method(component)
        else:
            output = _component_multi_axis_output(
                component=component,
                axis_columns=component_axis_columns,
                mode=validated_mode,
                bucket_column=bucket_column,
            )
            method = (
                "sum"
                if component.meta.composition_kind == "linear"
                else _component_method(component)
            )
        driver_field = (
            component_axis_columns[0]
            if validated_mode is None
            else "path"
            if validated_mode == "hierarchy"
            else None
        )
        params: dict[str, Any] = {
            "source_ref": frame.ref,
            "component_ref": component.ref,
            "axes": axis_ids,
            "axis_columns": component_axis_columns,
            "measure_column": "delta",
            "driver_field": driver_field,
            "value_column": "delta",
            "contribution_column": "contribution",
            "method": method,
        }
        if validated_mode is not None:
            params["mode"] = validated_mode
        params.update(params_extra)
        return persist_attribution_frame(
            session=session,
            df=output,
            intent=_intent,
            params=params,
            sources=[frame],
            metric_ids=[frame.meta.metric_id],
            attribution_kind="decomposition",
            driver_field=driver_field,
            value_column="delta",
            contribution_column="contribution",
            method=method,
            semantic_kind=frame.meta.semantic_kind,
            semantic_model=frame.meta.semantic_model,
            started_at=started_at,
            started_monotonic=started,
            analysis_purpose=_analysis_purpose,
            extra_blocking_issues=coverage_warnings,
        )

    if validated_mode is not None:
        bucket_column = (
            _bucket_column_for_panel(frame) if frame.meta.semantic_kind == "panel" else None
        )
        if validated_mode == "joint":
            output = _multi_axis_joint_sum_output(
                source_df,
                axis_columns=axis_columns,
                value_column=value_column,
                bucket_column=bucket_column,
            )
            driver_field = None
            method = "sum"
        else:
            output = _multi_axis_hierarchy_output(
                source_df,
                axis_columns=axis_columns,
                value_column=value_column,
                bucket_column=bucket_column,
            )
            driver_field = "path"
            method = "ordered_hierarchy_sum"
        params = {
            "source_ref": frame.ref,
            "axes": axis_ids,
            "axis_columns": axis_columns,
            "measure_column": value_column,
            "bucket_column": bucket_column,
            "driver_field": driver_field,
            "value_column": value_column,
            "contribution_column": "contribution",
            "method": method,
            "mode": validated_mode,
        }
        params.update(params_extra)
        return persist_attribution_frame(
            session=session,
            df=output,
            intent=_intent,
            params=params,
            sources=[frame],
            metric_ids=[frame.meta.metric_id],
            attribution_kind="decomposition",
            driver_field=driver_field,
            value_column=value_column,
            contribution_column="contribution",
            method=method,
            semantic_kind=frame.meta.semantic_kind,
            semantic_model=frame.meta.semantic_model,
            started_at=started_at,
            started_monotonic=started,
            analysis_purpose=_analysis_purpose,
            extra_blocking_issues=coverage_warnings,
        )

    if frame.meta.semantic_kind == "panel":
        bucket_column = _bucket_column_for_panel(frame)
        output = _ordered_hierarchy_output(
            source_df,
            axis_columns=axis_columns,
            value_column=value_column,
            bucket_column=bucket_column,
        )
        params = {
            "source_ref": frame.ref,
            "axes": axis_ids,
            "axis_columns": axis_columns,
            "measure_column": value_column,
            "bucket_column": bucket_column,
            "driver_field": "path",
            "value_column": value_column,
            "contribution_column": "contribution",
            "method": "ordered_hierarchy_sum",
        }
        params.update(params_extra)
        return persist_attribution_frame(
            session=session,
            df=output,
            intent=_intent,
            params=params,
            sources=[frame],
            metric_ids=[frame.meta.metric_id],
            attribution_kind="decomposition",
            driver_field="path",
            value_column=value_column,
            contribution_column="contribution",
            method="ordered_hierarchy_sum",
            semantic_kind=frame.meta.semantic_kind,
            semantic_model=frame.meta.semantic_model,
            started_at=started_at,
            started_monotonic=started,
            analysis_purpose=_analysis_purpose,
            extra_blocking_issues=coverage_warnings,
        )

    output = _ordered_hierarchy_output(
        source_df,
        axis_columns=axis_columns,
        value_column=value_column,
    )
    params = {
        "source_ref": frame.ref,
        "axes": axis_ids,
        "axis_columns": axis_columns,
        "measure_column": value_column,
        "driver_field": "path",
        "value_column": value_column,
        "contribution_column": "contribution",
        "method": "ordered_hierarchy_sum",
    }
    params.update(params_extra)
    return persist_attribution_frame(
        session=session,
        df=output,
        intent=_intent,
        params=params,
        sources=[frame],
        metric_ids=[frame.meta.metric_id],
        attribution_kind="decomposition",
        driver_field="path",
        value_column=value_column,
        contribution_column="contribution",
        method="ordered_hierarchy_sum",
        semantic_kind=frame.meta.semantic_kind,
        semantic_model=frame.meta.semantic_model,
        started_at=started_at,
        started_monotonic=started,
        analysis_purpose=_analysis_purpose,
        extra_blocking_issues=coverage_warnings,
    )
