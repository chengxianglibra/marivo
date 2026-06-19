"""Frame.summary() returns a FrameSummary Pydantic model with stable shape."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import BaseModel, ValidationError

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, FrameSummary
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
    from marivo.analysis.frames.delta import DeltaFrame

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
