"""Decompose DeltaFrames into AttributionFrames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from time import monotonic

import numpy as np
import pandas as pd

from marivo.analysis_py.errors import SemanticKindMismatchError
from marivo.analysis_py.frames.attribution import AttributionFrame
from marivo.analysis_py.frames.delta import DeltaFrame
from marivo.analysis_py.intents._derived import (
    ensure_frame_in_session,
    first_non_numeric_column,
    persist_attribution_frame,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis_py.session.core import Session, ensure_session_writable


def decompose(
    frame: DeltaFrame,
    *,
    by: str | None = None,
    value: str = "delta",
    session: Session | None = None,
) -> AttributionFrame:
    session = resolve_session(session)
    ensure_session_writable(session)
    if not isinstance(frame, DeltaFrame):
        raise SemanticKindMismatchError(message="decompose requires a DeltaFrame input")
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

    if frame.meta.semantic_kind == "scalar":
        if by is not None:
            raise SemanticKindMismatchError(message="scalar decompose does not accept by")
        total = float(source_df[value_column].sum())
        output = pd.DataFrame(
            {
                "driver": ["total"],
                value_column: [total],
                "contribution": [total],
                "pct_contribution": [1.0],
                "rank": [1],
            }
        )
        driver_field = "driver"
    else:
        driver_candidate = by or first_non_numeric_column(source_df)
        if driver_candidate is None or driver_candidate not in source_df.columns:
            raise SemanticKindMismatchError(
                message="decompose could not resolve a grouping column",
                details={"by": by, "columns": list(source_df.columns)},
            )
        driver_field = driver_candidate
        grouped = source_df.groupby(driver_field, dropna=False)[value_column].sum().reset_index()
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

    params = {"source_ref": frame.ref, "by": by, "value": value}
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
