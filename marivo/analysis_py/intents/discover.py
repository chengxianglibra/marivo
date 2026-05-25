"""Discover candidate follow-ups from committed analysis artifacts."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from datetime import UTC, datetime
from numbers import Real
from time import monotonic
from typing import Any, Literal, cast

import numpy as np
import pandas as pd

from marivo.analysis_py.errors import SemanticKindMismatchError
from marivo.analysis_py.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.intents._derived import (
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis_py.lineage import LineageStep
from marivo.analysis_py.session.core import Session, ensure_session_writable
from marivo.analysis_py.session.persistence import write_frame_to_disk, write_job_record

_CANDIDATE_COLUMNS = [
    "candidate_id",
    "source_ref",
    "source_row_index",
    "value_column",
    "observed_value",
    "score",
    "direction",
    "threshold",
    "keys_json",
]
_CANDIDATE_DTYPES = {
    "candidate_id": "string",
    "source_ref": "string",
    "source_row_index": "int64",
    "value_column": "string",
    "observed_value": "float64",
    "score": "float64",
    "direction": "string",
    "threshold": "float64",
    "keys_json": "string",
}


def discover(
    source: MetricFrame,
    *,
    objective: Literal["point_anomalies"],
    strategy: Literal["zscore"] = "zscore",
    value: str | None = None,
    threshold: float = 3.0,
    session: Session | None = None,
) -> CandidateSet:
    session = resolve_session(session)
    ensure_session_writable(session)
    if not isinstance(source, MetricFrame):
        raise SemanticKindMismatchError(
            message="discover point_anomalies requires a MetricFrame input",
            details={"expected_kind": "metric_frame", "got_kind": type(source).__name__},
        )
    ensure_frame_in_session(source, session=session, label="discover source")
    if objective != "point_anomalies":
        raise SemanticKindMismatchError(
            message=f"unsupported discover objective {objective!r}",
            details={"expected_kind": "point_anomalies", "got_kind": str(objective)},
        )
    if strategy != "zscore":
        raise SemanticKindMismatchError(
            message=f"unsupported discover strategy {strategy!r}",
            details={"expected_kind": "zscore", "got_kind": str(strategy)},
        )
    if source.meta.semantic_kind not in {"time_series", "panel"}:
        raise SemanticKindMismatchError(
            message="discover point_anomalies requires time_series or panel MetricFrame",
            details={
                "expected_kind": "time_series|panel",
                "got_kind": source.meta.semantic_kind,
            },
        )
    threshold_value = _validate_threshold(threshold)

    started_at = datetime.now(UTC)
    started = monotonic()
    source_df = source.to_pandas()
    value_column = require_numeric_column(source_df, value, purpose="discover")
    candidates = _score_zscore_candidates(
        source_df,
        source_ref=source.ref,
        value_column=value_column,
        threshold=threshold_value,
    )
    params = {
        "source_ref": source.ref,
        "objective": objective,
        "strategy": strategy,
        "value": value,
        "threshold": threshold_value,
    }
    frame_ref = gen_ref("frame")
    job_ref = gen_ref("job")
    finished_at = datetime.now(UTC)
    meta = CandidateSetMeta(
        kind="candidate_set",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        created_at=finished_at,
        row_count=len(candidates),
        byte_size=0,
        lineage=compose_lineage(
            [source],
            step=LineageStep(
                intent="discover",
                job_ref=job_ref,
                inputs=[source.ref],
                params_digest=params_digest(params),
            ),
        ),
        source_ref=source.ref,
        objective=objective,
        strategy=strategy,
        metric_ids=[source.meta.metric_id],
        semantic_kind=source.meta.semantic_kind,
        semantic_model=source.meta.semantic_model,
        params=params,
    )
    frame = CandidateSet(_df=candidates, meta=meta)
    frame.meta = cast("CandidateSetMeta", write_frame_to_disk(session.layout, frame))
    write_job_record(
        session.layout,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "discover",
            "params": params,
            "input_frame_refs": [source.ref],
            "output_frame_ref": frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": session.semantic_project.root,
            "semantic_model": source.meta.semantic_model,
        },
    )
    return frame


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


def _score_zscore_candidates(
    source_df: pd.DataFrame,
    *,
    source_ref: str,
    value_column: str,
    threshold: float,
) -> pd.DataFrame:
    series = source_df[value_column]
    non_null = series.dropna()
    if len(non_null) < 2:
        scores = np.zeros(len(source_df))
    else:
        std = float(non_null.std(ddof=0))
        if std == 0:
            scores = np.zeros(len(source_df))
        else:
            mean = float(non_null.mean())
            scores = ((series - mean) / std).fillna(0).to_numpy()

    rows: list[dict[str, Any]] = []
    key_columns = [column for column in source_df.columns if column != value_column]
    for row_index, is_candidate in enumerate(np.abs(scores) >= threshold):
        if not bool(is_candidate):
            continue
        row = source_df.iloc[row_index]
        score = float(scores[row_index])
        rows.append(
            {
                "candidate_id": f"cand_{row_index}",
                "source_ref": source_ref,
                "source_row_index": row_index,
                "value_column": value_column,
                "observed_value": _json_scalar(row[value_column]),
                "score": score,
                "direction": "high" if score > 0 else "low",
                "threshold": threshold,
                "keys_json": json.dumps(
                    {
                        str(column): _json_scalar(row[column])
                        for column in key_columns
                        if pd.notna(row[column])
                    },
                    sort_keys=True,
                    default=str,
                ),
            },
        )
    return pd.DataFrame(rows, columns=_CANDIDATE_COLUMNS).astype(_CANDIDATE_DTYPES)


def _json_scalar(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value
