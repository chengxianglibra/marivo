"""Frame.summary() returns a FrameSummary Pydantic model with stable shape."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import BaseModel, ValidationError

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, FrameSummary
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep


def _make_meta(
    *,
    row_count: int = 3,
    lineage: Lineage | None = None,
) -> BaseFrameMeta:
    return BaseFrameMeta(
        kind="metric_frame",
        ref="frame_test123",
        session_id="s_abc",
        project_root="/tmp/proj",
        produced_by_job="job_abc",
        created_at=datetime(2026, 5, 24, tzinfo=UTC),
        row_count=row_count,
        byte_size=0,
        lineage=lineage or Lineage(),
    )


def test_frame_summary_is_pydantic_model() -> None:
    assert issubclass(FrameSummary, BaseModel)


def test_summary_reports_shape_and_columns() -> None:
    df = pd.DataFrame({"bucket": ["a", "b", "c"], "value": [1.0, 2.0, None]})
    frame = BaseFrame(_df=df, meta=_make_meta())
    s = frame.summary()
    assert s.kind == "metric_frame"
    assert s.ref == "frame_test123"
    assert s.row_count == 3
    assert s.columns == ["bucket", "value"]
    assert s.null_ratios == pytest.approx({"bucket": 0.0, "value": 1 / 3})
    assert s.produced_by_job == "job_abc"


def test_summary_disambiguates_duplicate_column_names_by_position() -> None:
    df = pd.DataFrame(
        [
            [1.0, None, None, 4.0],
            [None, 2.0, 3.0, None],
        ],
        columns=["value", "value", 1, "1"],
    )
    frame = BaseFrame(_df=df, meta=_make_meta(row_count=2))

    s = frame.summary()

    assert s.columns == ["value", "value#2", "1", "1#2"]
    assert list(s.null_ratios) == s.columns
    assert s.null_ratios == pytest.approx(
        {
            "value": 0.5,
            "value#2": 0.5,
            "1": 0.5,
            "1#2": 0.5,
        },
    )


def test_summary_disambiguates_generated_suffix_collisions() -> None:
    df = pd.DataFrame(
        [
            [1.0, None, None],
            [None, 2.0, 3.0],
        ],
        columns=["value", "value#2", "value"],
    )
    frame = BaseFrame(_df=df, meta=_make_meta(row_count=2))

    s = frame.summary()

    assert s.columns == ["value", "value#2", "value#3"]
    assert list(s.null_ratios) == s.columns
    assert s.null_ratios == pytest.approx(
        {
            "value": 0.5,
            "value#2": 0.5,
            "value#3": 0.5,
        },
    )


def test_summary_row_count_uses_actual_frame_length() -> None:
    df = pd.DataFrame({"x": [1, 2]})
    frame = BaseFrame(_df=df, meta=_make_meta(row_count=99))

    assert frame.summary().row_count == 2


def test_repr_header_includes_kind_ref_and_shape() -> None:
    df = pd.DataFrame({"bucket": ["a"], "value": [1.0]})
    meta = _make_meta()
    frame = BaseFrame(_df=df, meta=meta)
    r = repr(frame)
    assert r.startswith("<BaseFrame")
    assert "ref=frame_test123" in r
    assert "rows=3" in r
    assert "call .show() to inspect" in r


def test_render_shows_head5_when_dataframe_has_more_rows() -> None:
    df = pd.DataFrame({"bucket": list("abcdefg"), "value": list(range(7))})
    frame = BaseFrame(_df=df, meta=_make_meta())
    r = frame.render()
    rendered_buckets = {
        line.split()[0]
        for line in r.splitlines()
        if line.strip()
        and not line.startswith(
            ("BaseFrame", "status:", "columns:", "preview:", "available:", "-", "...")
        )
    }
    assert {"a", "b", "c"}.issubset(rendered_buckets)
    # Render preview is bounded to the first five rows.
    assert {"f", "g"}.isdisjoint(rendered_buckets)


def test_render_skips_truncation_hint_when_short() -> None:
    df = pd.DataFrame({"x": [1, 2]})
    frame = BaseFrame(_df=df, meta=_make_meta())
    r = frame.render()
    assert "more rows" not in r


def test_render_bounds_wide_dataframe_and_shows_truncated_columns() -> None:
    df = pd.DataFrame(
        {f"col_{idx}": [f"visible_row_{row}_col_{idx}" for row in range(4)] for idx in range(12)},
    )
    frame = BaseFrame(_df=df, meta=_make_meta(row_count=4))

    r = frame.render()

    assert "col_0" in r
    assert "col_7" in r
    assert "col_8" not in r
    assert "col_11" not in r


def test_repr_empty_wide_dataframe_returns_one_liner() -> None:
    df = pd.DataFrame(columns=[f"empty_col_{idx}" for idx in range(12)])
    frame = BaseFrame(_df=df, meta=_make_meta(row_count=0))

    r = repr(frame)

    assert r.count("\n") == 0
    assert "call .show() to inspect" in r


def test_render_shows_columns_in_output() -> None:
    long_column = "long_column_name_" + ("x" * 80)
    long_value = "long_cell_value_" + ("y" * 120)
    df = pd.DataFrame({long_column: [long_value]})
    frame = BaseFrame(_df=df, meta=_make_meta(row_count=1))

    r = frame.render()

    assert "long_column_name_" in r
    assert "long_cell_value_" in r


def test_repr_html_returns_none() -> None:
    df = pd.DataFrame(
        {f"html_col_{idx}": [f"html_row_{row}_col_{idx}" for row in range(6)] for idx in range(10)},
    )
    frame = BaseFrame(_df=df, meta=_make_meta(row_count=6))

    html = frame._repr_html_()

    assert html is None


def test_summary_includes_lineage_one_liner() -> None:
    df = pd.DataFrame({"x": [1]})
    frame = BaseFrame(_df=df, meta=_make_meta())
    s = frame.summary()
    assert s.lineage_oneliner == "(empty)"


def test_summary_formats_lineage_step_intents() -> None:
    df = pd.DataFrame({"x": [1]})
    lineage = Lineage(
        steps=[
            LineageStep(
                intent="observe",
                job_ref="job_1",
                inputs=[],
                params_digest="a",
            ),
            LineageStep(
                intent="compare",
                job_ref="job_2",
                inputs=["frame_1"],
                params_digest="b",
            ),
        ],
    )
    frame = BaseFrame(_df=df, meta=_make_meta(row_count=1, lineage=lineage))

    assert frame.summary().lineage_oneliner == "observe -> compare"


def test_frame_summary_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        FrameSummary(
            kind="metric_frame",
            ref="frame_test123",
            row_count=1,
            columns=["x"],
            null_ratios={"x": 0.0},
            produced_by_job=None,
            lineage_oneliner="(empty)",
            extra_field=True,
        )


def test_metric_frame_advertises_next_intents() -> None:
    from marivo.analysis.frames.metric import MetricFrame

    intents = MetricFrame._NEXT_INTENTS
    assert "compare" in intents
    assert "discover" in intents
    assert "transform" in intents


def test_delta_frame_advertises_next_intents() -> None:
    assert DeltaFrame._NEXT_INTENTS == ("decompose", "discover", "transform")


def test_candidate_set_advertises_next_intents() -> None:
    from marivo.analysis.frames.candidate import CandidateSet

    assert CandidateSet._NEXT_INTENTS == ("select",)


def test_terminal_frame_has_empty_next_intents() -> None:
    from marivo.analysis.frames.attribution import AttributionFrame
    from marivo.analysis.frames.forecast import ForecastFrame
    from marivo.analysis.frames.hypothesis import HypothesisTestResult

    assert AttributionFrame._NEXT_INTENTS == ()
    assert ForecastFrame._NEXT_INTENTS == ()
    assert HypothesisTestResult._NEXT_INTENTS == ()


def _make_hypothesis_meta():
    from marivo.analysis.frames.hypothesis import HypothesisTestResultMeta

    return HypothesisTestResultMeta(
        kind="hypothesis_test_result",
        ref="frame_hypothesis123",
        session_id="s_abc",
        project_root="/tmp/proj",
        produced_by_job="job_test1",
        created_at=datetime(2026, 6, 18, tzinfo=UTC),
        row_count=2,
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="observe",
                    job_ref="job_observe1",
                    inputs=[],
                    params_digest="a",
                ),
                LineageStep(
                    intent="test",
                    job_ref="job_test1",
                    inputs=["frame_cur", "frame_base"],
                    params_digest="b",
                ),
            ],
        ),
        source_refs=["frame_cur", "frame_base"],
        metric_ids=["sales.revenue", "sales.revenue"],
        semantic_kinds=["panel", "panel"],
        semantic_models=["sales", "sales"],
        hypothesis="mean_changed",
        method="paired_t",
        alignment={"kind": "window_bucket"},
        sampling={"pairing": "window_bucket"},
        alpha=0.05,
        result_shape="per_segment",
        segment_dimensions=["country"],
        rejected_count=1,
        not_enough_data_count=1,
    )


def test_hypothesis_test_summary_returns_typed_agent_result(capsys) -> None:
    from marivo.analysis.frames.base import FrameSummary
    from marivo.analysis.frames.hypothesis import (
        HypothesisTestResult,
        HypothesisTestResultSummary,
    )
    from marivo.render import AgentResult

    df = pd.DataFrame(
        {
            "segment": ["US", "CA"],
            "p_value": [0.01, None],
            "rejected": [True, False],
            "reason_code": ["ok", "insufficient_pairs"],
        },
    )
    frame = HypothesisTestResult(_df=df, meta=_make_hypothesis_meta())

    s = frame.summary()

    assert type(s) is HypothesisTestResultSummary
    assert not isinstance(s, FrameSummary)
    assert isinstance(s, AgentResult)
    assert s.kind == "hypothesis_test_result"
    assert s.ref == "frame_hypothesis123"
    assert s.metric_ids == ["sales.revenue", "sales.revenue"]
    assert s.hypothesis == "mean_changed"
    assert s.method == "paired_t"
    assert s.alpha == 0.05
    assert s.result_shape == "per_segment"
    assert s.segment_dimensions == ["country"]
    assert s.rejected_count == 1
    assert s.not_enough_data_count == 1
    assert s.row_count == 2
    assert s.lineage_oneliner == "observe -> test"

    r = repr(s)
    assert r == (
        "<HypothesisTestResultSummary ref=frame_hypothesis123 "
        "hypothesis=mean_changed method=paired_t rejected=1; call .show() to inspect>"
    )
    assert "\n" not in r

    rendered = s.render()
    assert rendered.startswith(
        "HypothesisTestResultSummary ref=frame_hypothesis123 "
        "hypothesis=mean_changed method=paired_t rejected=1",
    )
    assert (
        "status: alpha=0.05 shape=per_segment rows=2 not_enough_data=1 lineage=observe -> test"
    ) in rendered
    assert "- .render()" in rendered
    assert "- .show()" in rendered
    assert not rendered.endswith("\n")

    assert s.show() is None
    captured = capsys.readouterr()
    assert captured.out == rendered + "\n"


def _make_association_meta(
    *,
    correlation: float = 0.9627,
    aligned_row_count: int = 12,
    dropped_row_count: int = 0,
):
    from marivo.analysis.frames.association import AssociationResultMeta

    return AssociationResultMeta(
        kind="association_result",
        ref="frame_assoc123",
        session_id="s_abc",
        project_root="/tmp/proj",
        produced_by_job="job_corr1",
        created_at=datetime(2026, 6, 9, tzinfo=UTC),
        row_count=1,
        byte_size=0,
        source_refs=["frame_a", "frame_b"],
        metric_ids=["sales.revenue", "marketing.spend"],
        semantic_kinds=["time_series", "time_series"],
        semantic_models=["sales", "marketing"],
        method="pearson",
        alignment={"kind": "window_bucket"},
        lag_policy={"mode": "single", "offset": 0},
        aligned_row_count=aligned_row_count,
        dropped_row_count=dropped_row_count,
        correlation=correlation,
    )


def test_association_summary_includes_correlation(capsys) -> None:
    from marivo.analysis.frames.association import (
        AssociationResult,
        AssociationResultSummary,
    )
    from marivo.render import AgentResult

    df = pd.DataFrame({"correlation": [0.9627]})
    frame = AssociationResult(_df=df, meta=_make_association_meta())
    s = frame.summary()

    assert isinstance(s, AssociationResultSummary)
    assert isinstance(s, AgentResult)
    assert s.kind == "association_result"
    assert s.correlation == pytest.approx(0.9627)
    assert s.method == "pearson"
    assert s.metric_ids == ["sales.revenue", "marketing.spend"]
    assert s.aligned_row_count == 12
    assert s.dropped_row_count == 0

    r = repr(s)
    assert r == (
        "<AssociationResultSummary ref=frame_assoc123 method=pearson "
        "r=0.96; call .show() to inspect>"
    )
    assert "\n" not in r

    rendered = s.render()
    assert rendered.startswith("AssociationResultSummary ref=frame_assoc123 method=pearson r=0.96")
    assert "status: r=0.96 method=pearson aligned=12 dropped=0" in rendered
    assert "- .render()" in rendered
    assert "- .show()" in rendered
    assert not rendered.endswith("\n")

    assert s.show() is None
    captured = capsys.readouterr()
    assert captured.out == rendered + "\n"


def test_association_summary_is_not_generic_frame_summary() -> None:
    from marivo.analysis.frames.association import (
        AssociationResult,
        AssociationResultSummary,
    )
    from marivo.analysis.frames.base import FrameSummary

    df = pd.DataFrame({"correlation": [0.5]})
    frame = AssociationResult(_df=df, meta=_make_association_meta(correlation=0.5))
    s = frame.summary()

    assert type(s) is AssociationResultSummary
    assert not isinstance(s, FrameSummary)


def test_association_repr_includes_identity() -> None:
    from marivo.analysis.frames.association import AssociationResult

    df = pd.DataFrame({"correlation": [0.9627]})
    frame = AssociationResult(_df=df, meta=_make_association_meta())
    r = repr(frame)

    assert "AssociationResult" in r
    assert "ref=frame_assoc123" in r
    assert "method=pearson" in r
    assert "r=0.96" in r
    assert "rows=1" in r
    assert "call .show() to inspect" in r


def test_association_repr_empty_frame() -> None:
    from marivo.analysis.frames.association import AssociationResult

    df = pd.DataFrame(columns=["correlation"])
    frame = AssociationResult(_df=df, meta=_make_association_meta())
    r = repr(frame)

    assert r.startswith("<AssociationResult")
    assert "call .show() to inspect" in r


# ---------------------------------------------------------------------------
# DeltaFrame render / preview tests
# ---------------------------------------------------------------------------


def _make_delta_meta(
    *,
    row_count: int = 5,
    semantic_kind: str = "panel",
    unit: str | None = None,
) -> DeltaFrameMeta:
    return DeltaFrameMeta(
        kind="delta_frame",
        ref="frame_delta123",
        session_id="s_abc",
        project_root="/tmp/proj",
        produced_by_job="job_cmp1",
        created_at=datetime(2026, 6, 18, tzinfo=UTC),
        row_count=row_count,
        byte_size=0,
        metric_id="sales.revenue",
        source_current_ref="frame_cur",
        source_baseline_ref="frame_base",
        alignment={"kind": "window_bucket"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        unit=unit,
    )


def _make_delta_df(n: int = 5) -> pd.DataFrame:
    """Return a delta DataFrame with pct_change sorted ascending (worst first)."""
    return pd.DataFrame(
        {
            "cluster": [f"seg_{i}" for i in range(n)],
            "current": [100.0 + i * 10 for i in range(n)],
            "baseline": [100.0] * n,
            "delta": [float(i * 10) for i in range(n)],
            "pct_change": [float(i) / 10 for i in range(n)],
            "pct_change_status": ["computed"] * n,
        },
    )


def test_delta_render_sorts_by_abs_pct_change_descending() -> None:
    df = _make_delta_df(5)
    frame = DeltaFrame(_df=df, meta=_make_delta_meta())

    rendered = frame.render()
    lines = rendered.splitlines()

    # Find the data rows (between preview: and available:)
    data_start = next(i for i, line in enumerate(lines) if line.startswith("preview"))
    data_end = next(i for i, line in enumerate(lines) if line.startswith("available:"))
    data_lines = lines[data_start + 1 : data_end]
    # Filter out truncation hints
    data_lines = [line for line in data_lines if not line.startswith("...")]

    # pct_change values are 0.0, 0.1, 0.2, 0.3, 0.4 → sorted by |abs| desc
    # so first should be seg_4 (0.4), last should be seg_0 (0.0)
    assert "seg_4" in data_lines[0]
    assert "seg_0" in data_lines[-1]


def test_delta_render_shows_20_rows_when_data_exceeds_limit() -> None:
    df = _make_delta_df(30)
    frame = DeltaFrame(_df=df, meta=_make_delta_meta(row_count=30))

    rendered = frame.render()
    lines = rendered.splitlines()

    data_start = next(i for i, line in enumerate(lines) if line.startswith("preview"))
    data_end = next(i for i, line in enumerate(lines) if line.startswith("available:"))
    data_lines = [line for line in lines[data_start + 1 : data_end] if not line.startswith("...")]

    assert len(data_lines) == 20


def test_delta_render_preview_label_includes_total_row_count() -> None:
    df = _make_delta_df(50)
    frame = DeltaFrame(_df=df, meta=_make_delta_meta(row_count=50))

    rendered = frame.render()

    assert "preview (top 20 of 50 rows):" in rendered


def test_delta_render_preview_label_plain_when_fewer_rows_than_limit() -> None:
    df = _make_delta_df(5)
    frame = DeltaFrame(_df=df, meta=_make_delta_meta())

    rendered = frame.render()

    # When rows <= 20, just show "preview:" (no "top N of M" qualifier)
    assert "preview:" in rendered
    assert "top " not in rendered


def test_delta_render_handles_nan_and_inf_pct_change() -> None:
    df = pd.DataFrame(
        {
            "cluster": ["big", "nan_row", "inf_row", "from_zero", "small"],
            "current": [150.0, float("nan"), 100.0, 10.0, 105.0],
            "baseline": [100.0, 100.0, 100.0, 0.0, 100.0],
            "delta": [50.0, float("nan"), 0.0, 10.0, 5.0],
            "pct_change": [0.5, float("nan"), float("inf"), float("inf"), 0.05],
            "pct_change_status": [
                "computed",
                "not_computable",
                "from_zero_growth",
                "from_zero_growth",
                "computed",
            ],
        },
    )
    frame = DeltaFrame(_df=df, meta=_make_delta_meta())

    rendered = frame.render()
    lines = rendered.splitlines()

    data_start = next(i for i, line in enumerate(lines) if line.startswith("preview"))
    data_end = next(i for i, line in enumerate(lines) if line.startswith("available:"))
    data_lines = [line for line in lines[data_start + 1 : data_end] if not line.startswith("...")]

    # "big" (|pct_change|=0.5) should be first
    assert "big" in data_lines[0]
    # Rows with NaN/inf pct_change and no usable delta should be at the bottom
    bottom_clusters = {line.split()[0] for line in data_lines[-2:]}
    assert "nan_row" in bottom_clusters or "inf_row" in bottom_clusters


def test_delta_preview_returns_sorted_rows() -> None:
    df = _make_delta_df(5)
    frame = DeltaFrame(_df=df, meta=_make_delta_meta())

    preview = frame.preview(limit=3)

    assert preview.is_truncated is True
    assert preview.returned_row_count == 3
    # Rows should be sorted by |pct_change| descending: seg_4, seg_3, seg_2
    assert preview.rows[0]["cluster"] == "seg_4"
    assert preview.rows[1]["cluster"] == "seg_3"
    assert preview.rows[2]["cluster"] == "seg_2"


def test_delta_render_without_pct_change_columns_does_not_crash() -> None:
    """A delta frame without pct_change/delta columns falls back to original order."""
    df = pd.DataFrame(
        {
            "cluster": ["A", "B", "C"],
            "value": [1.0, 2.0, 3.0],
        },
    )
    frame = DeltaFrame(_df=df, meta=_make_delta_meta())

    rendered = frame.render()
    assert "DeltaFrame" in rendered
    assert "preview:" in rendered
