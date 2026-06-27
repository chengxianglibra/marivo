"""Public deterministic attribution operator."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from time import monotonic
from typing import Literal

import numpy as np
import pandas as pd

from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.intents._derived import (
    ensure_frame_in_session,
    persist_attribution_frame,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis.intents.decompose import (
    _effective_component_axis_column,
    _normalize_axis_boundary,
    decompose,
)
from marivo.analysis.semantic_inputs import DimensionInput
from marivo.analysis.session.core import Session, ensure_session_writable

AttributeMode = Literal["flat", "nested", "recursive"]


def _resolve_axis_columns(
    frame: DeltaFrame,
    axes: list[DimensionInput],
    *,
    session: Session,
    columns: list[str],
) -> list[tuple[str, str]]:
    resolved: list[tuple[str, str]] = []
    seen_columns: dict[str, str] = {}
    for axis in axes:
        axis_id = _normalize_axis_boundary(session, axis)
        column = _effective_component_axis_column(frame, axis_id, columns)
        if column is None:
            raise SemanticKindMismatchError(
                message=f"attribute axis {axis_id!r} is not present in DeltaFrame",
                details={
                    "axis": axis_id,
                    "available_columns": columns,
                    "delta_ref": frame.ref,
                },
            )
        if column in seen_columns:
            raise SemanticKindMismatchError(
                message="attribute axes must resolve to distinct columns",
                details={
                    "conflicting_axis": axis_id,
                    "column": column,
                    "already_bound_axis": seen_columns[column],
                    "bound_axes": list(seen_columns.keys()),
                },
            )
        seen_columns[column] = axis_id
        resolved.append((axis_id, column))
    return resolved


def _is_missing(value: object) -> bool:
    """Return True for any pandas/NumPy NA sentinel.

    Handles None, float nan, pd.NA (nullable Int64), and pd.NaT (datetime)
    without relying on the narrow mypy overload of ``pd.isna`` for bare
    ``object``.
    """
    if value is None:
        return True
    if isinstance(value, float):
        return bool(np.isnan(value))
    return value is pd.NA or value is pd.NaT


def _format_path(values: tuple[object, ...]) -> str:
    parts: list[str] = []
    for value in values:
        if value is None or _is_missing(value):
            parts.append("<null>")
        else:
            parts.append(str(value))
    return " > ".join(parts)


def _nested_output(
    frame: DeltaFrame,
    axes: list[DimensionInput],
    *,
    mode: AttributeMode,
    session: Session,
) -> tuple[pd.DataFrame, list[str], list[str], str]:
    df = frame.to_pandas()
    value_column = require_numeric_column(df, "delta", purpose="attribute")
    axis_pairs = _resolve_axis_columns(
        frame,
        axes,
        session=session,
        columns=[str(column) for column in df.columns],
    )
    total = float(df[value_column].sum())
    rows: list[dict[str, object]] = []
    for level in range(1, len(axis_pairs) + 1):
        group_columns = [column for _, column in axis_pairs[:level]]
        grouped = (
            df.groupby(group_columns, dropna=False)[value_column]
            .sum()
            .reset_index()
            .rename(columns={value_column: "contribution"})
        )
        grouped["_abs_contribution"] = grouped["contribution"].abs()
        grouped = grouped.sort_values(
            ["_abs_contribution", *group_columns],
            ascending=[False, *([True] * len(group_columns))],
            kind="mergesort",
        ).reset_index(drop=True)
        _axis_id, axis_column = axis_pairs[level - 1]
        for rank_val, (_, row) in enumerate(grouped.iterrows(), start=1):
            path_values = tuple(row[column] for column in group_columns)
            contribution = float(row["contribution"])
            rows.append(
                {
                    "level": level,
                    "axis": axis_column,
                    "driver": row[axis_column],
                    "path": _format_path(path_values),
                    "contribution": contribution,
                    "pct_contribution": contribution / total if total else np.nan,
                    "rank": rank_val,
                }
            )
    return (
        pd.DataFrame(
            rows,
            columns=[
                "level",
                "axis",
                "driver",
                "path",
                "contribution",
                "pct_contribution",
                "rank",
            ],
        ),
        [axis_id for axis_id, _ in axis_pairs],
        [column for _, column in axis_pairs],
        f"{mode}_sum",
    )


def attribute(
    frame: DeltaFrame,
    *,
    axes: list[DimensionInput],
    mode: AttributeMode = "flat",
    session: Session | None = None,
) -> AttributionFrame:
    """Attribute a DeltaFrame's movement over explicit deterministic axes."""
    resolved_session = resolve_session(session)
    ensure_session_writable(resolved_session)
    if not isinstance(frame, DeltaFrame):
        raise SemanticKindMismatchError(message="attribute requires a DeltaFrame input")
    if mode not in {"flat", "nested", "recursive"}:
        raise SemanticKindMismatchError(
            message=f"unsupported attribute mode {mode!r}",
            details={"mode": mode, "supported": ["flat", "nested", "recursive"]},
        )
    if not axes:
        raise SemanticKindMismatchError(
            message="attribute requires at least one axis",
            details={"delta_ref": frame.ref},
        )
    if mode == "flat" and len(axes) != 1:
        raise SemanticKindMismatchError(
            message="flat attribute mode requires exactly one axis; use nested or recursive for multiple axes",
            details={
                "mode": mode,
                "axis_count": len(axes),
                "supported_multi_axis_modes": ["nested", "recursive"],
            },
        )
    if mode == "flat" and len(axes) == 1:
        axis_id = _normalize_axis_boundary(resolved_session, axes[0])
        return decompose(
            frame,
            axis=axes[0],
            session=resolved_session,
            _intent="attribute",
            _params_extra={"mode": mode, "axes": [axis_id]},
        )

    ensure_frame_in_session(frame, session=resolved_session, label="attribute frame")
    started_at = datetime.now(UTC)
    started = monotonic()
    output, axis_ids, axis_columns, method = _nested_output(
        frame,
        axes,
        mode=mode,
        session=resolved_session,
    )
    params = {
        "source_ref": frame.ref,
        "mode": mode,
        "axes": axis_ids,
        "axis_columns": axis_columns,
        "measure_column": "delta",
        "driver_field": "path",
        "value_column": "delta",
        "contribution_column": "contribution",
        "method": method,
    }
    return persist_attribution_frame(
        session=resolved_session,
        df=output,
        intent="attribute",
        params=params,
        sources=[frame],
        metric_ids=[frame.meta.metric_id],
        attribution_kind="decomposition",
        driver_field="path",
        value_column="delta",
        contribution_column="contribution",
        method=method,
        semantic_kind=frame.meta.semantic_kind,
        semantic_model=frame.meta.semantic_model,
        started_at=started_at,
        started_monotonic=started,
    )
