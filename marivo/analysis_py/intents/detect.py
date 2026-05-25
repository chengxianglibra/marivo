"""Detect anomalies in MetricFrames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from numbers import Real
from time import monotonic
from typing import Literal

import numpy as np

from marivo.analysis_py.errors import SemanticKindMismatchError
from marivo.analysis_py.frames.attribution import AttributionFrame
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.intents._derived import (
    ensure_frame_in_session,
    persist_attribution_frame,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis_py.session.core import Session, ensure_session_writable

_RESERVED_OUTPUT_COLUMNS = frozenset({"score", "is_anomaly", "direction", "threshold"})


def detect(
    frame: MetricFrame,
    *,
    value: str | None = None,
    method: Literal["zscore"] = "zscore",
    threshold: float = 3.0,
    session: Session | None = None,
) -> AttributionFrame:
    session = resolve_session(session)
    ensure_session_writable(session)
    if not isinstance(frame, MetricFrame):
        raise SemanticKindMismatchError(message="detect requires a MetricFrame input")
    ensure_frame_in_session(frame, session=session, label="detect frame")
    if method != "zscore":
        raise SemanticKindMismatchError(message=f"unsupported detect method {method!r}")
    if isinstance(threshold, bool) or not isinstance(threshold, Real):
        raise SemanticKindMismatchError(message="detect threshold must be a positive finite number")
    threshold_value = float(threshold)
    if not np.isfinite(threshold_value) or threshold_value <= 0:
        raise SemanticKindMismatchError(message="detect threshold must be a positive finite number")

    started_at = datetime.now(UTC)
    started = monotonic()
    output = frame.to_pandas()
    collisions = sorted(_RESERVED_OUTPUT_COLUMNS.intersection(output.columns))
    if collisions:
        raise SemanticKindMismatchError(
            message="detect output columns collide with input columns",
            details={"collisions": collisions},
        )
    value_column = require_numeric_column(output, value, purpose="detect")
    series = output[value_column]
    non_null = series.dropna()
    if len(non_null) < 2:
        scores = np.zeros(len(output))
    else:
        std = float(non_null.std(ddof=0))
        if std == 0:
            scores = np.zeros(len(output))
        else:
            mean = float(non_null.mean())
            scores = ((series - mean) / std).fillna(0).to_numpy()

    output["score"] = scores
    output["is_anomaly"] = np.abs(scores) >= threshold_value
    output["direction"] = np.where(
        output["is_anomaly"],
        np.where(scores > 0, "high", "low"),
        "normal",
    )
    output["threshold"] = threshold_value

    params = {
        "source_ref": frame.ref,
        "value": value,
        "method": method,
        "threshold": threshold_value,
    }
    return persist_attribution_frame(
        session=session,
        df=output,
        intent="detect",
        params=params,
        sources=[frame],
        metric_ids=[frame.meta.metric_id],
        attribution_kind="anomaly",
        driver_field=None,
        value_column=value_column,
        contribution_column=None,
        method=method,
        semantic_kind=frame.meta.semantic_kind,
        semantic_model=frame.meta.semantic_model,
        started_at=started_at,
        started_monotonic=started,
    )
