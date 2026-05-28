"""Decompose DeltaFrames into AttributionFrames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from time import monotonic

import numpy as np
import pandas as pd

from marivo.analysis.errors import (
    AxisNotInPanelDimensionsError,
    ComponentDecompositionError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.types import TriggeredByFollowup
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.component import ComponentFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.intents._derived import (
    ensure_frame_in_session,
    persist_attribution_frame,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis.refs import DimensionRef
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


def _resolve_axis_column(frame: DeltaFrame, axis: DimensionRef, columns: list[str]) -> str | None:
    requested = axis.id
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
            candidates = [str(axis_id), column]
            if isinstance(ref, str):
                candidates.append(ref)
            if requested in candidates:
                return column

    normalized = requested.rsplit(".", 1)[-1]
    if normalized in columns:
        return normalized
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


def _component_measure_role(component: ComponentFrame) -> str:
    if component.meta.decomposition_kind == "ratio":
        return "denominator"
    if component.meta.decomposition_kind == "weighted_average":
        return "weight"
    raise ComponentDecompositionError(
        message="unsupported component decomposition kind",
        details={"decomposition_kind": component.meta.decomposition_kind},
    )


def _component_method(component: ComponentFrame) -> str:
    if component.meta.decomposition_kind == "ratio":
        return "ratio_mix"
    if component.meta.decomposition_kind == "weighted_average":
        return "weighted_mix"
    raise ComponentDecompositionError(
        message="unsupported component decomposition kind",
        details={"decomposition_kind": component.meta.decomposition_kind},
    )


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numerator = pd.to_numeric(numerator, errors="coerce")
    denominator = pd.to_numeric(denominator, errors="coerce")
    return pd.Series(
        np.where(denominator.notna() & (denominator != 0), numerator / denominator, np.nan),
        index=numerator.index,
    )


def _component_mix_output(
    *,
    component: ComponentFrame,
    axis_column: str,
) -> pd.DataFrame:
    df = component.to_pandas()
    share_role = _component_measure_role(component)
    required = [
        axis_column,
        "current_numerator",
        "baseline_numerator",
        f"current_{share_role}",
        f"baseline_{share_role}",
        "current_metric_value",
        "baseline_metric_value",
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
    current_value = _safe_divide(output["current_numerator"], current_basis)
    baseline_value = _safe_divide(output["baseline_numerator"], baseline_basis)
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
            "current_numerator",
            "baseline_numerator",
            f"current_{share_role}",
            f"baseline_{share_role}",
            "current_metric_value",
            "baseline_metric_value",
            "current_share",
            "baseline_share",
            "rank",
        ]
    ]


def decompose(
    frame: DeltaFrame,
    *,
    axis: DimensionRef,
    measure_column: str = "delta",
    session: Session | None = None,
    _triggered_by: TriggeredByFollowup | None = None,
) -> AttributionFrame:
    """Attribute a DeltaFrame's movement across a chosen segment axis.

    When to use: attribute a delta to dimension segments (why did revenue drop?).

    For ``panel`` deltas, ``axis`` must be one of the frame's segment dimensions.
    For ``time_series`` deltas, ``axis`` is the bucket-start column.

    Args:
        frame: A DeltaFrame produced by ``session.compare``.
        axis: The segment column to attribute over, wrapped in ``mv.DimensionRef``.
            Dotted ids such as ``"model.field"`` resolve to the persisted
            DeltaFrame column ``"field"`` when present.
        measure_column: Numeric column on the delta to attribute. Defaults to ``"delta"``.
        session: Defaults to the currently-attached session.

    Raises:
        SemanticKindMismatchError: ``frame`` is not a DeltaFrame, or ``axis`` is not a DimensionRef.
        AxisNotInPanelDimensionsError: ``axis`` is not a segment column of the panel.
        CrossSessionFrameError: ``frame`` belongs to a different session.

    Example:
        >>> delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))
        >>> attribution = session.decompose(delta, axis=mv.DimensionRef("country"))
        >>> attribution.summary()
    """
    session = resolve_session(session)
    ensure_session_writable(session)
    if not isinstance(frame, DeltaFrame):
        raise SemanticKindMismatchError(message="decompose requires a DeltaFrame input")
    if not isinstance(axis, DimensionRef):
        raise SemanticKindMismatchError(
            message="decompose requires axis=DimensionRef(...)",
            details={
                "expected_kind": "DimensionRef",
                "got_kind": type(axis).__name__,
            },
        )
    ensure_frame_in_session(frame, session=session, label="decompose frame")
    if frame.meta.semantic_kind not in {"scalar", "time_series", "segmented", "panel"}:
        raise SemanticKindMismatchError(
            message=f"decompose does not support semantic_kind={frame.meta.semantic_kind!r}",
        )

    started_at = datetime.now(UTC)
    started = monotonic()
    source_df = frame.to_pandas()
    value_column = require_numeric_column(source_df, measure_column, purpose="decompose")
    available_columns = [str(column) for column in source_df.columns]
    normalized_axis = axis.id.rsplit(".", 1)[-1]
    axis_column = _resolve_axis_column(frame, axis, available_columns)

    if axis_column is None:
        raise SemanticKindMismatchError(
            message="decompose axis column does not exist in the DeltaFrame",
            hint=(
                f"Use axis=mv.DimensionRef({normalized_axis!r}) if that column exists in "
                "the DeltaFrame."
            ),
            details={
                "requested_axis": axis.id,
                "normalized_axis": normalized_axis,
                "available_columns": available_columns,
            },
        )

    # Component-aware path: ratio/weighted mix attribution.
    component = _load_delta_component_frame(frame, session=session)
    if component is not None:
        if measure_column != "delta":
            raise ComponentDecompositionError(
                message=(
                    "component-aware decompose explains the main delta column only; "
                    "non-default measure_column is supported only for sum deltas"
                ),
                details={"measure_column": measure_column, "delta_ref": frame.ref},
            )
        output = _component_mix_output(component=component, axis_column=axis_column)
        method = _component_method(component)
        params = {
            "source_ref": frame.ref,
            "component_ref": component.ref,
            "axis": axis.model_dump(mode="json"),
            "measure_column": "delta",
            "driver_field": axis_column,
            "value_column": "delta",
            "contribution_column": "contribution",
            "method": method,
        }
        return persist_attribution_frame(
            session=session,
            df=output,
            intent="decompose",
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
        dim_columns = _panel_dimension_columns(frame)
        if axis_column not in dim_columns:
            raise AxisNotInPanelDimensionsError(
                message="decompose axis is not a panel dimension",
                details={
                    "axis": axis_column,
                    "available_dimensions": dim_columns,
                },
            )
        if bucket_column not in source_df.columns:
            raise SemanticKindMismatchError(
                message="decompose panel bucket column does not exist in the DeltaFrame",
                details={"bucket_column": bucket_column, "columns": list(source_df.columns)},
            )

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
            "axis": axis.model_dump(mode="json"),
            "measure_column": measure_column,
            "bucket_column": bucket_column,
            "driver_field": axis_column,
            "value_column": value_column,
            "contribution_column": "contribution",
            "method": "sum",
        }
        return persist_attribution_frame(
            session=session,
            df=output,
            intent="decompose",
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
        "axis": axis.model_dump(mode="json"),
        "measure_column": value_column,
    }
    return persist_attribution_frame(
        session=session,
        df=output,
        intent="decompose",
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
