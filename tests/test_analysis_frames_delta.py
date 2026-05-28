"""DeltaFrame and DeltaFrameMeta."""

from datetime import UTC, datetime

import pandas as pd

from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.lineage import Lineage


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
        source_current_ref="frame_a",
        source_baseline_ref="frame_b",
        alignment={"kind": "window_bucket"},
        semantic_kind="time_series",
        semantic_model="sales",
    )
    assert meta.kind == "delta_frame"
    assert meta.alignment["kind"] == "window_bucket"
    assert meta.source_current_ref == "frame_a"


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
        source_current_ref="frame_a",
        source_baseline_ref="frame_b",
        alignment={"kind": "window_bucket"},
        semantic_kind="time_series",
        semantic_model="sales",
    )
    d = DeltaFrame(_df=df, meta=meta)
    assert set(d.columns) >= {"current", "baseline", "delta", "pct_change"}


def test_delta_frame_meta_accepts_optional_normalization():
    from datetime import UTC, datetime

    from marivo.analysis.frames.delta import DeltaFrameMeta

    meta = DeltaFrameMeta(
        ref="frame_test",
        session_id="sess_test",
        project_root="/tmp/proj",
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=0,
        byte_size=0,
        metric_id="sales.revenue",
        source_current_ref="a",
        source_baseline_ref="b",
        alignment={"kind": "window_bucket"},
        semantic_kind="scalar",
        semantic_model="sales",
        normalization={"mode": "z_score", "baseline": None, "columns_affected": ["delta"]},
    )
    assert meta.normalization["mode"] == "z_score"
