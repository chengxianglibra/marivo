"""Phase 2 pre-submit validators and adapters."""

from __future__ import annotations

import pandas as pd
import pytest

from marivo.analysis.errors import (
    AlignmentPolicyNotApplicableError,
    AxisNotInPanelDimensionsError,
    PanelGrainMismatchError,
    SegmentDimensionMismatchError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._validate import (
    raise_first,
    to_validation_issues,
    validate_decompose_columns,
)
from marivo.analysis.lineage import Lineage
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import CalendarRef
from marivo.analysis.validation import ValidationIssue


def test_validation_issue_carries_type_message_details():
    issue = ValidationIssue(
        intent="compare",
        error_type="SemanticKindMismatchError",
        message="boom",
        context={"kind": "X"},
    )
    assert issue.intent == "compare"
    assert issue.error_type == "SemanticKindMismatchError"
    assert issue.message == "boom"
    assert issue.context == {"kind": "X"}


def test_validation_issue_forbids_extra_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ValidationIssue(
            intent="compare",
            error_type="X",
            message="m",
            context={},
            surprise=True,
        )


def test_raise_first_raises_the_first_issue():
    err = SemanticKindMismatchError(message="first")
    with pytest.raises(SemanticKindMismatchError, match="first"):
        raise_first([err, SegmentDimensionMismatchError(message="second")])


def test_raise_first_is_noop_on_empty():
    assert raise_first([]) is None


def test_to_validation_issues_maps_each_error():
    errors = [
        SemanticKindMismatchError(message="m1", context={"a": 1}),
        SegmentDimensionMismatchError(message="m2", context={"kind": "SegmentDimensionMismatch"}),
    ]
    issues = to_validation_issues("compare", errors)
    assert [i.error_type for i in issues] == [
        "SemanticKindMismatchError",
        "SegmentDimensionMismatchError",
    ]
    assert issues[0].intent == "compare"
    assert issues[0].message == "m1"
    assert issues[0].context == {"a": 1}
    assert issues[1].context == {"kind": "SegmentDimensionMismatch"}


# ---------------------------------------------------------------------------
# validate_compare tests
# ---------------------------------------------------------------------------


def _now():
    from datetime import UTC, datetime

    return datetime(2026, 5, 24, tzinfo=UTC)


def _mf(
    *,
    metric_id: str = "sales.revenue",
    semantic_kind: str = "scalar",
    axes: dict | None = None,
) -> MetricFrame:
    meta = MetricFrameMeta(
        ref="frame_x",
        session_id="s",
        project_root="/p",
        produced_by_job=None,
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        metric_id=metric_id,
        axes=axes or {},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind=semantic_kind,
        semantic_model="sales",
    )
    return MetricFrame(_df=pd.DataFrame({"value": [1.0]}), meta=meta)


_WB = AlignmentPolicy(kind="window_bucket")


def test_validate_compare_ok_returns_empty():
    from marivo.analysis.intents._validate import validate_compare

    assert validate_compare(_mf(), _mf(), alignment=_WB) == []


def test_validate_compare_metric_id_mismatch():
    from marivo.analysis.intents._validate import validate_compare

    issues = validate_compare(_mf(metric_id="a.x"), _mf(metric_id="a.y"), alignment=_WB)
    assert len(issues) == 1
    assert isinstance(issues[0], SemanticKindMismatchError)
    assert "the same metric" in issues[0].message


def test_validate_compare_semantic_kind_mismatch():
    from marivo.analysis.intents._validate import validate_compare

    issues = validate_compare(
        _mf(semantic_kind="scalar"), _mf(semantic_kind="time_series"), alignment=_WB
    )
    assert isinstance(issues[0], SemanticKindMismatchError)
    assert "matching semantic_kind" in issues[0].message


def test_validate_compare_segment_dimension_mismatch():
    from marivo.analysis.intents._validate import validate_compare

    cur = _mf(semantic_kind="segmented", axes={"region": {"role": "dimension", "column": "region"}})
    base = _mf(
        semantic_kind="segmented", axes={"channel": {"role": "dimension", "column": "channel"}}
    )
    issues = validate_compare(cur, base, alignment=_WB)
    assert isinstance(issues[0], SegmentDimensionMismatchError)
    assert issues[0]._context["kind"] == "SegmentDimensionMismatch"


def test_validate_compare_panel_grain_mismatch():
    from marivo.analysis.intents._validate import validate_compare

    cur = _mf(
        semantic_kind="panel",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
    )
    base = _mf(
        semantic_kind="panel",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "week"}},
    )
    issues = validate_compare(cur, base, alignment=_WB)
    assert isinstance(issues[0], PanelGrainMismatchError)
    assert issues[0]._context["kind"] == "PanelGrainMismatch"


def test_validate_compare_segmented_requires_window_bucket():
    from marivo.analysis.intents._validate import validate_compare

    cur = _mf(semantic_kind="segmented", axes={"region": {"role": "dimension", "column": "region"}})
    base = _mf(
        semantic_kind="segmented", axes={"region": {"role": "dimension", "column": "region"}}
    )
    issues = validate_compare(
        cur,
        base,
        alignment=AlignmentPolicy(kind="dow_aligned", calendar=CalendarRef("cn_holidays")),
    )
    assert isinstance(issues[0], AlignmentPolicyNotApplicableError)
    assert issues[0]._context["alignment_kind"] == "dow_aligned"


def test_validate_compare_scalar_rejects_non_window_bucket():
    from marivo.analysis.intents._validate import validate_compare

    issues = validate_compare(
        _mf(semantic_kind="scalar"),
        _mf(semantic_kind="scalar"),
        alignment=AlignmentPolicy(kind="dow_aligned", calendar=CalendarRef("cn_holidays")),
    )
    assert isinstance(issues[0], SemanticKindMismatchError)
    assert issues[0]._context["kind"] == "CalendarAlignRequiresTimeSeries"


# ---------------------------------------------------------------------------
# validate_decompose_columns tests
# ---------------------------------------------------------------------------


def _delta(
    *,
    df: pd.DataFrame,
    semantic_kind: str = "segmented",
    alignment: dict | None = None,
) -> DeltaFrame:
    meta = DeltaFrameMeta(
        ref="frame_d",
        session_id="s",
        project_root="/p",
        produced_by_job=None,
        created_at=_now(),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(),
        metric_id="sales.revenue",
        source_current_ref="frame_cur",
        source_baseline_ref="frame_base",
        alignment=alignment or {},
        semantic_kind=semantic_kind,
        semantic_model="sales",
    )
    return DeltaFrame(_df=df, meta=meta)


def test_validate_decompose_ok_segmented_returns_empty():
    frame = _delta(df=pd.DataFrame({"region": ["n", "s"], "delta": [1.0, 2.0]}))
    issues = validate_decompose_columns(frame, "region", source_df=frame.to_pandas())
    assert issues == []


def test_validate_decompose_axis_column_missing():
    frame = _delta(df=pd.DataFrame({"region": ["n"], "delta": [1.0]}))
    issues = validate_decompose_columns(frame, "nonexistent", source_df=frame.to_pandas())
    assert isinstance(issues[0], SemanticKindMismatchError)
    assert "axis column does not exist" in issues[0].message
    assert issues[0]._context["requested_axis"] == "nonexistent"


def test_validate_decompose_delta_not_numeric():
    frame = _delta(df=pd.DataFrame({"region": ["n", "s"], "delta": ["x", "y"]}))
    issues = validate_decompose_columns(frame, "region", source_df=frame.to_pandas())
    assert isinstance(issues[0], SemanticKindMismatchError)
    assert "not numeric" in issues[0].message


def test_validate_decompose_panel_axis_not_a_dimension():
    df = pd.DataFrame({"bucket_start": ["d1", "d2"], "region": ["n", "s"], "delta": [1.0, 2.0]})
    frame = _delta(
        df=df,
        semantic_kind="panel",
        alignment={
            "axes": {
                "time": {"role": "time", "column": "bucket_start"},
                "region": {"role": "dimension", "column": "region"},
            }
        },
    )
    issues = validate_decompose_columns(frame, "bucket_start", source_df=frame.to_pandas())
    assert isinstance(issues[0], AxisNotInPanelDimensionsError)
    assert issues[0]._context["axis"] == "bucket_start"
    assert "region" in issues[0]._context["available_dimensions"]
