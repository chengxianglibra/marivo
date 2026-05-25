"""Decompose DeltaFrames into AttributionFrames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from time import monotonic

import numpy as np

from marivo.analysis_py.errors import SemanticKindMismatchError
from marivo.analysis_py.frames.attribution import AttributionFrame
from marivo.analysis_py.frames.delta import DeltaFrame
from marivo.analysis_py.intents._derived import (
    ensure_frame_in_session,
    persist_attribution_frame,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis_py.refs import DimensionRef
from marivo.analysis_py.session.core import Session, ensure_session_writable


def decompose(
    frame: DeltaFrame,
    *,
    axis: DimensionRef,
    value: str = "delta",
    session: Session | None = None,
) -> AttributionFrame:
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
    if frame.meta.semantic_kind == "panel":
        raise SemanticKindMismatchError(
            message="decompose does not support panel delta frames in v1.1",
        )
    if frame.meta.semantic_kind not in {"scalar", "time_series", "segmented"}:
        raise SemanticKindMismatchError(
            message=f"decompose does not support semantic_kind={frame.meta.semantic_kind!r}",
        )

    started_at = datetime.now(UTC)
    started = monotonic()
    source_df = frame.to_pandas()
    value_column = require_numeric_column(source_df, value, purpose="decompose")
    axis_column = axis.id

    if axis_column not in source_df.columns:
        raise SemanticKindMismatchError(
            message="decompose axis column does not exist in the DeltaFrame",
            details={"axis": axis_column, "columns": list(source_df.columns)},
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
        "value": value,
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
