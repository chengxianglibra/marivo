"""Compare two MetricFrames into a DeltaFrame."""
# mypy: disable-error-code=import-untyped

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, cast

import numpy as np
import pandas as pd

from marivo.analysis_py.errors import (
    AlignmentFailedError,
    CrossSessionFrameError,
    SemanticKindMismatchError,
)
from marivo.analysis_py.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.lineage import Lineage, LineageStep
from marivo.analysis_py.session.attach import active as session_active
from marivo.analysis_py.session.core import Session, ensure_session_writable
from marivo.analysis_py.session.persistence import write_frame_to_disk, write_job_record

EXPECTED_METRIC_FRAME_KIND = "metric_frame"


def _gen_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _display_kind(kind: str) -> str:
    return "".join(part.capitalize() for part in kind.split("_"))


def _frame_kind(frame: object) -> str | None:
    meta = getattr(frame, "meta", None)
    kind = getattr(meta, "kind", None)
    return kind if isinstance(kind, str) and kind else None


def _require_metric_frame(label: str, frame: object) -> MetricFrame:
    got_kind = _frame_kind(frame)
    if isinstance(frame, MetricFrame) and got_kind == EXPECTED_METRIC_FRAME_KIND:
        return frame
    if got_kind is None:
        got_kind = type(frame).__name__
    raise SemanticKindMismatchError(
        message=(
            f"compare(a, b) expected MetricFrame for `{label}`, got {_display_kind(got_kind)}."
        ),
        details={
            "parameter": label,
            "expected_kind": EXPECTED_METRIC_FRAME_KIND,
            "got_kind": got_kind,
        },
    )


def compare(
    a: MetricFrame,
    b: MetricFrame,
    *,
    align: Literal["bucket", "sample", "segment_key"] = "bucket",
    compare_type: Literal["yoy", "qoq", "mom", "wow", "custom"] = "custom",
    session: Session | None = None,
) -> DeltaFrame:
    if session is None:
        session = session_active()
    ensure_session_writable(session)
    a = _require_metric_frame("a", a)
    b = _require_metric_frame("b", b)
    for label, source_frame in (("a", a), ("b", b)):
        if source_frame.meta.session_id != session.id:
            raise CrossSessionFrameError(
                message=(
                    f"compare argument '{label}' belongs to session "
                    f"{source_frame.meta.session_id!r}, not {session.id!r}"
                ),
            )
    if a.meta.metric_id != b.meta.metric_id:
        raise SemanticKindMismatchError(
            message=f"compare requires the same metric, got {a.meta.metric_id!r} and {b.meta.metric_id!r}",
        )
    if a.meta.semantic_kind != b.meta.semantic_kind:
        raise SemanticKindMismatchError(
            message=(
                "compare requires matching semantic_kind, got "
                f"{a.meta.semantic_kind!r} and {b.meta.semantic_kind!r}"
            ),
        )

    started_at = datetime.now(UTC)
    started = monotonic()
    df = _align_and_compute(a.to_pandas(), b.to_pandas(), align=align)
    if df.empty:
        raise AlignmentFailedError(message=f"alignment '{align}' produced no rows")
    finished_at = datetime.now(UTC)

    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    params = {
        "source_a_ref": a.ref,
        "source_b_ref": b.ref,
        "align": align,
        "compare_type": compare_type,
    }
    digest = f"sha256:{hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()}"
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        created_at=finished_at,
        row_count=len(df),
        byte_size=0,
        lineage=Lineage.compose(
            a.lineage,
            b.lineage,
            new_step=LineageStep(
                intent="compare",
                job_ref=job_ref,
                inputs=[a.ref, b.ref],
                params_digest=digest,
            ),
        ),
        metric_id=a.meta.metric_id,
        source_a_ref=a.ref,
        source_b_ref=b.ref,
        compare_type=compare_type,
        align=align,
        calendar_info=None,
        semantic_kind=a.meta.semantic_kind,
        semantic_model=a.meta.semantic_model,
    )
    output_frame = DeltaFrame(_df=df, meta=meta)
    output_frame.meta = cast("DeltaFrameMeta", write_frame_to_disk(session.layout, output_frame))
    write_job_record(
        session.layout,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "compare",
            "params": params,
            "input_frame_refs": [a.ref, b.ref],
            "output_frame_ref": frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": session.semantic_project.root,
            "semantic_model": a.meta.semantic_model,
        },
    )
    return output_frame


def _align_and_compute(a_df: pd.DataFrame, b_df: pd.DataFrame, *, align: str) -> pd.DataFrame:
    if align == "sample":
        return _sample_align(a_df, b_df)
    if align in {"bucket", "segment_key"}:
        if len(a_df.columns) == 1 and len(b_df.columns) == 1:
            return _sample_align(a_df, b_df)
        key = a_df.columns[0]
        merged = pd.merge(a_df, b_df, on=key, suffixes=("_a", "_b"))
        value_cols_a = [col for col in merged.columns if col.endswith("_a")]
        value_cols_b = [col for col in merged.columns if col.endswith("_b")]
        if not value_cols_a or not value_cols_b:
            raise AlignmentFailedError(
                message=f"align='{align}' could not find paired value columns"
            )
        current = merged[value_cols_a[0]].to_numpy()
        baseline = merged[value_cols_b[0]].to_numpy()
        return pd.DataFrame(
            {
                key: merged[key],
                "current": current,
                "baseline": baseline,
                "delta": current - baseline,
                "pct_change": np.where(baseline != 0, (current - baseline) / baseline, np.nan),
            }
        )
    raise AlignmentFailedError(message=f"unknown align mode '{align}'")


def _sample_align(a_df: pd.DataFrame, b_df: pd.DataFrame) -> pd.DataFrame:
    n = min(len(a_df), len(b_df))
    current = a_df.reset_index(drop=True).iloc[:n, 0].to_numpy()
    baseline = b_df.reset_index(drop=True).iloc[:n, 0].to_numpy()
    return pd.DataFrame(
        {
            "current": current,
            "baseline": baseline,
            "delta": current - baseline,
            "pct_change": np.where(baseline != 0, (current - baseline) / baseline, np.nan),
        }
    )
