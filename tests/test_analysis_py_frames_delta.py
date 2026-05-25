"""DeltaFrame and DeltaFrameMeta."""

from datetime import UTC, datetime

import pandas as pd

from marivo.analysis_py.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis_py.lineage import Lineage


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def test_delta_frame_meta_kind_literal():
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref="frame_d_001",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_c",
        created_at=_now(),
        row_count=2,
        byte_size=128,
        lineage=Lineage(),
        metric_id="sales.revenue",
        source_a_ref="frame_a",
        source_b_ref="frame_b",
        alignment={"kind": "calendar_bucket"},
        semantic_kind="time_series",
        semantic_model="sales",
    )
    assert meta.kind == "delta_frame"
    assert meta.alignment["kind"] == "calendar_bucket"
    assert meta.source_a_ref == "frame_a"


def test_delta_frame_wraps_df_and_meta():
    df = pd.DataFrame(
        {
            "bucket": ["2026-07-01"],
            "current": [10.0],
            "baseline": [5.0],
            "delta": [5.0],
            "pct_change": [1.0],
        }
    )
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref="frame_d_001",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_c",
        created_at=_now(),
        row_count=1,
        byte_size=128,
        lineage=Lineage(),
        metric_id="sales.revenue",
        source_a_ref="frame_a",
        source_b_ref="frame_b",
        alignment={"kind": "calendar_bucket"},
        semantic_kind="time_series",
        semantic_model="sales",
    )
    d = DeltaFrame(_df=df, meta=meta)
    assert set(d.columns) >= {"current", "baseline", "delta", "pct_change"}
