"""Decompose DeltaFrames into AttributionFrames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from time import monotonic

import numpy as np
import pandas as pd

from marivo.analysis.errors import (
    ComponentDecompositionError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.component import (
    ComponentFrame,
    resolve_role_column_name,
    resolve_role_columns,
)
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.intents._derived import (
    ensure_frame_in_session,
    persist_attribution_frame,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis.intents._validate import raise_first, validate_decompose_columns
from marivo.analysis.semantic_inputs import DimensionInput, normalize_dimension_boundary
from marivo.analysis.session._load import load_frame
from marivo.analysis.session.core import Session, ensure_session_writable


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
            details={
                "delta_ref": frame.ref,
                "component_ref": frame.meta.component_ref,
                "loaded_kind": loaded.meta.kind,
            },
        )
    return loaded


def _component_role_column_name(component: ComponentFrame, role: str) -> str:
    return resolve_role_column_name(component.meta.components, role)


def _component_measure_role(component: ComponentFrame) -> str:
    if component.meta.composition_kind == "ratio":
        return _component_role_column_name(component, "denominator")
    if component.meta.composition_kind == "weighted_average":
        return _component_role_column_name(component, "weight")
    raise ComponentDecompositionError(
        message="unsupported component composition kind",
        details={"composition_kind": component.meta.composition_kind},
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
        details={"composition_kind": component.meta.composition_kind},
    )


def _component_method(component: ComponentFrame) -> str:
    if component.meta.composition_kind == "ratio":
        return "ratio_mix"
    if component.meta.composition_kind == "weighted_average":
        return "weighted_mix"
    raise ComponentDecompositionError(
        message="unsupported component composition kind",
        details={"composition_kind": component.meta.composition_kind},
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
        details={"component_ref": component.ref},
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
            details={"component_ref": component.ref, "missing_columns": missing},
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
            details={"component_ref": component.ref, "share_role": share_role},
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
    df = component.to_pandas()
    if bucket_column is None:
        return _component_mix_output_for_df(
            df=df,
            component=component,
            axis_column=axis_column,
        )

    if bucket_column not in df.columns:
        raise ComponentDecompositionError(
            message="component-aware panel decompose requires a bucket column",
            details={"component_ref": component.ref, "bucket_column": bucket_column},
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
            details={"component_ref": component.ref, "bucket_column": bucket_column},
        )
    return pd.concat(pieces, ignore_index=True)


def decompose(
    frame: DeltaFrame,
    *,
    axis: DimensionInput,
    session: Session | None = None,
    _intent: str = "decompose",
    _params_extra: dict[str, object] | None = None,
) -> AttributionFrame:
    session = resolve_session(session)
    ensure_session_writable(session)
    if not isinstance(frame, DeltaFrame):
        raise SemanticKindMismatchError(message="decompose requires a DeltaFrame input")
    axis_id = _normalize_axis_boundary(session, axis)
    params_extra = dict(_params_extra or {})
    ensure_frame_in_session(frame, session=session, label="decompose frame")
    if frame.meta.semantic_kind not in {"scalar", "time_series", "segmented", "panel"}:
        raise SemanticKindMismatchError(
            message=f"decompose does not support semantic_kind={frame.meta.semantic_kind!r}",
        )

    # Gate: reject non-linear sampled time folds that cannot be decomposed.
    fold = getattr(frame.meta, "fold", None)
    component_folds = getattr(frame.meta, "component_folds", [])
    fold_labels = [fold.get("time_fold")] if isinstance(fold, dict) else []
    fold_labels.extend(item.get("time_fold") for item in component_folds if isinstance(item, dict))
    if any(label and not str(label).startswith("mean") for label in fold_labels):
        raise ComponentDecompositionError(
            message="decompose does not support non-linear sampled time folds",
            details={
                "reason": "non_linear_time_fold",
                "delta_ref": frame.ref,
                "time_folds": fold_labels,
            },
        )

    started_at = datetime.now(UTC)
    started = monotonic()
    source_df = frame.to_pandas()
    raise_first(validate_decompose_columns(frame, axis_id, source_df=source_df))
    value_column = require_numeric_column(source_df, "delta", purpose="decompose")
    available_columns = [str(column) for column in source_df.columns]
    axis_column = _effective_component_axis_column(frame, axis_id, available_columns)

    if axis_column is None:
        raise_first(validate_decompose_columns(frame, axis_id, source_df=source_df))

    assert axis_column is not None  # validated above; needed for type narrowing

    # Component-aware path: ratio/weighted mix or linear additive attribution.
    component = _load_delta_component_frame(frame, session=session)
    if component is not None:
        component_columns = [str(column) for column in component.to_pandas().columns]
        component_axis_column = _effective_component_axis_column(frame, axis_id, component_columns)
        if component_axis_column is None:
            component_axis_column = axis_column
        bucket_column = None
        if frame.meta.semantic_kind == "panel":
            bucket_column = _component_bucket_column(frame, component_columns)
            if bucket_column is None:
                raise ComponentDecompositionError(
                    message="component-aware panel decompose requires a bucket column",
                    details={"delta_ref": frame.ref, "component_ref": component.ref},
                )
        if component.meta.composition_kind == "linear":
            output = _component_linear_output_for_df(
                df=component.to_pandas(),
                component=component,
                axis_column=component_axis_column,
            )
            method = "sum"
        else:
            output = _component_mix_output(
                component=component,
                axis_column=component_axis_column,
                bucket_column=bucket_column,
            )
            method = _component_method(component)
        axis_column = component_axis_column
        params = {
            "source_ref": frame.ref,
            "component_ref": component.ref,
            "axis": {"semantic_id": axis_id},
            "measure_column": "delta",
            "driver_field": axis_column,
            "value_column": "delta",
            "contribution_column": "contribution",
            "method": method,
            "mode": params_extra.get("mode", "flat"),
            "axes": params_extra.get("axes", [axis_id]),
        }
        return persist_attribution_frame(
            session=session,
            df=output,
            intent=_intent,
            params=params,
            sources=[frame],
            metric_ids=[frame.meta.metric_id],
            attribution_kind="decomposition",
            driver_field=axis_column,
            value_column="delta",
            contribution_column="contribution",
            method=method,
            semantic_kind=frame.meta.semantic_kind,
            semantic_model=frame.meta.semantic_model,
            started_at=started_at,
            started_monotonic=started,
        )

    if frame.meta.semantic_kind == "panel":
        bucket_column = _bucket_column_for_panel(frame)

        grouped = (
            source_df.groupby([bucket_column, axis_column], dropna=False)[value_column]
            .sum()
            .reset_index()
            .rename(columns={value_column: "contribution"})
        )
        bucket_total = grouped.groupby(bucket_column, dropna=False)["contribution"].transform("sum")
        grouped["pct_contribution"] = np.where(
            bucket_total != 0,
            grouped["contribution"] / bucket_total,
            np.nan,
        )
        grouped["_abs_contribution"] = grouped["contribution"].abs()
        grouped = grouped.sort_values(
            [bucket_column, "_abs_contribution"],
            ascending=[True, False],
            kind="mergesort",
        ).reset_index(drop=True)
        grouped["rank"] = grouped.groupby(bucket_column, dropna=False).cumcount() + 1
        output = grouped[[bucket_column, axis_column, "contribution", "pct_contribution", "rank"]]
        params = {
            "source_ref": frame.ref,
            "axis": {"semantic_id": axis_id},
            "measure_column": value_column,
            "bucket_column": bucket_column,
            "driver_field": axis_column,
            "value_column": value_column,
            "contribution_column": "contribution",
            "method": "sum",
            "mode": params_extra.get("mode", "flat"),
            "axes": params_extra.get("axes", [axis_id]),
        }
        return persist_attribution_frame(
            session=session,
            df=output,
            intent=_intent,
            params=params,
            sources=[frame],
            metric_ids=[frame.meta.metric_id],
            attribution_kind="decomposition",
            driver_field=axis_column,
            value_column=value_column,
            contribution_column="contribution",
            method="sum",
            semantic_kind=frame.meta.semantic_kind,
            semantic_model=frame.meta.semantic_model,
            started_at=started_at,
            started_monotonic=started,
        )

    grouped = source_df.groupby(axis_column, dropna=False)[value_column].sum().reset_index()
    grouped["contribution"] = grouped[value_column]
    total = float(grouped["contribution"].sum())
    grouped["pct_contribution"] = np.where(
        total != 0,
        grouped["contribution"] / total,
        np.nan,
    )
    grouped = grouped.reindex(
        grouped["contribution"].abs().sort_values(ascending=False).index
    ).reset_index(drop=True)
    grouped["rank"] = range(1, len(grouped) + 1)
    output = grouped
    driver_field = axis_column

    params = {
        "source_ref": frame.ref,
        "axis": {"semantic_id": axis_id},
        "measure_column": value_column,
        "mode": params_extra.get("mode", "flat"),
        "axes": params_extra.get("axes", [axis_id]),
    }
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
        method="sum",
        semantic_kind=frame.meta.semantic_kind,
        semantic_model=frame.meta.semantic_model,
        started_at=started_at,
        started_monotonic=started,
    )
