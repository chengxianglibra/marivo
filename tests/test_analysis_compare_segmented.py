from __future__ import annotations

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    AlignmentPolicyNotApplicableError,
    SegmentDimensionMismatchError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents.compare import compare
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import CalendarRef
from tests.shared_fixtures import make_metric_frame


@pytest.fixture(autouse=True)
def _session_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def _segmented_metric(session, rows, *, dimension: str = "region") -> MetricFrame:
    return make_metric_frame(
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
    baseline = _segmented_metric(
        s,
        [
            {"region": "NORTH", "value": 70.0},
            {"region": "WEST", "value": 40.0},
        ],
    )

    out = compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    assert out.meta.semantic_kind == "segmented"
    assert out.meta.alignment["segment_info"] == {
        "segment_count": 3,
        "a_only_segments_count": 1,
        "b_only_segments_count": 1,
    }
    assert out.meta.alignment["axes"] == current.meta.axes
    df = out.to_pandas()
    assert list(df.columns) == [
        "region",
        "presence_status",
        "current",
        "baseline",
        "delta",
        "pct_change",
        "pct_change_status",
    ]
    by_region = {row["region"]: row for row in df.to_dict(orient="records")}
    assert by_region["NORTH"] == {
        "region": "NORTH",
        "presence_status": "matched",
        "current": 100.0,
        "baseline": 70.0,
        "delta": 30.0,
        "pct_change": pytest.approx(30.0 / 70.0),
        "pct_change_status": "computed",
    }
    assert by_region["SOUTH"]["region"] == "SOUTH"
    assert by_region["SOUTH"]["presence_status"] == "new"
    assert by_region["SOUTH"]["current"] == 80.0
    assert by_region["SOUTH"]["baseline"] == 0.0
    assert by_region["SOUTH"]["delta"] == 80.0
    assert pd.isna(by_region["SOUTH"]["pct_change"])
    assert by_region["SOUTH"]["pct_change_status"] == "from_zero_growth"
    assert by_region["WEST"] == {
        "region": "WEST",
        "presence_status": "churned",
        "current": 0.0,
        "baseline": 40.0,
        "delta": -40.0,
        "pct_change": pytest.approx(-1.0),
        "pct_change_status": "computed",
    }


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
    assert row["presence_status"] == "matched"
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


@pytest.mark.parametrize(
    "conflicting_name",
    [
        "current",
        "baseline",
        "delta",
        "pct_change",
        "pct_change_status",
        "presence_status",
    ],
)
def test_compare_segmented_rejects_dimension_colliding_with_protocol_column(
    conflicting_name: str,
) -> None:
    """A legal dimension named like a compare result protocol column must fail
    closed with a typed error instead of surfacing a pandas duplicate-column
    exception (issue #39).

    ``observe`` can produce such a segmented MetricFrame (the semantic layer
    allows these dimension names); only ``compare``'s align step renames the
    value column to ``current``/``baseline`` and appends ``delta``/``pct_change``
    protocol columns, so the collision surfaces there.
    """
    s = session_attach.get_or_create(name="demo")
    current = _segmented_metric(
        s,
        [{conflicting_name: "A", "value": 100.0}],
        dimension=conflicting_name,
    )
    baseline = _segmented_metric(
        s,
        [{conflicting_name: "A", "value": 70.0}],
        dimension=conflicting_name,
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    error = exc_info.value
    assert error._context["reason"] == "protocol_column_collision"
    assert conflicting_name in error._context["conflicting_columns"]
    assert error.location == "session.compare"
    assert error.repair is not None
    assert error.repair.kind == "semantic_authoring"
    # Failing closed before alignment must not leave compare job/frame residue.
    assert [job.intent for job in s.jobs() if job.intent == "compare"] == []
