from __future__ import annotations

import pandas as pd
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import (
    AlignmentPolicyNotApplicableError,
    SegmentDimensionMismatchError,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents.compare import compare
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import CalendarRef


@pytest.fixture(autouse=True)
def _session_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def _segmented_metric(session, rows, *, dimension: str = "region") -> MetricFrame:
    return MetricFrame.from_dataframe(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes={dimension: {"role": "dimension", "column": dimension}},
        measure={"name": "value"},
        semantic_kind="segmented",
        semantic_model="sales",
        session=session,
    )


def test_compare_segmented_outer_join_preserves_one_sided_segments():
    s = session_attach.get_or_create(name="demo")
    current = _segmented_metric(
        s,
        [
            {"region": "NORTH", "value": 100.0},
            {"region": "SOUTH", "value": 80.0},
        ],
    )
    baseline = _segmented_metric(s, [{"region": "NORTH", "value": 70.0}])

    out = compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    assert out.meta.semantic_kind == "segmented"
    assert out.meta.alignment["segment_info"] == {
        "segment_count": 2,
        "a_only_segments_count": 1,
        "b_only_segments_count": 0,
    }
    assert out.meta.alignment["axes"] == current.meta.axes
    df = out.to_pandas()
    assert list(df.columns) == ["region", "current", "baseline", "delta", "pct_change"]
    north, south = df.to_dict(orient="records")
    assert north == {
        "region": "NORTH",
        "current": 100.0,
        "baseline": 70.0,
        "delta": 30.0,
        "pct_change": pytest.approx(30.0 / 70.0),
    }
    assert south["region"] == "SOUTH"
    assert south["current"] == 80.0
    assert pd.isna(south["baseline"])
    assert pd.isna(south["delta"])
    assert pd.isna(south["pct_change"])


def test_compare_segmented_null_metric_values_do_not_count_as_one_sided_segments():
    s = session_attach.get_or_create(name="demo")
    current = _segmented_metric(s, [{"region": "NORTH", "value": None}])
    baseline = _segmented_metric(s, [{"region": "NORTH", "value": 70.0}])

    out = compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    assert out.meta.alignment["segment_info"] == {
        "segment_count": 1,
        "a_only_segments_count": 0,
        "b_only_segments_count": 0,
    }
    row = out.to_pandas().iloc[0]
    assert row["region"] == "NORTH"
    assert pd.isna(row["current"])
    assert row["baseline"] == 70.0
    assert pd.isna(row["delta"])
    assert pd.isna(row["pct_change"])


def test_compare_segmented_rejects_non_window_bucket_alignment():
    s = session_attach.get_or_create(name="demo")
    current = _segmented_metric(s, [{"region": "NORTH", "value": 100.0}])
    baseline = _segmented_metric(s, [{"region": "NORTH", "value": 70.0}])

    with pytest.raises(AlignmentPolicyNotApplicableError):
        compare(
            current,
            baseline,
            alignment=AlignmentPolicy(
                kind="dow_aligned",
                calendar=CalendarRef("cn_holidays"),
                period="month",
            ),
            session=s,
        )


def test_compare_segmented_rejects_dimension_mismatch():
    s = session_attach.get_or_create(name="demo")
    current = _segmented_metric(s, [{"region": "NORTH", "value": 100.0}], dimension="region")
    baseline = _segmented_metric(s, [{"channel": "WEB", "value": 70.0}], dimension="channel")

    with pytest.raises(SegmentDimensionMismatchError):
        compare(current, baseline, session=s)
