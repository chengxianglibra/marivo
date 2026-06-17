"""BaseFrameMeta + BaseFrame: thin pandas wrapper with explicit boundaries."""

from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import BaseModel, ValidationError

from marivo.analysis.errors import FrameMutationError, FrameReadError
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, FramePreview
from marivo.analysis.lineage import Lineage


def _meta(**overrides) -> BaseFrameMeta:
    defaults = {
        "kind": "metric_frame",
        "ref": "frame_abc12345",
        "session_id": "sess_a3b21c89",
        "project_root": "/tmp/proj",
        "produced_by_job": "job_e7c4f8a1",
        "created_at": datetime(2026, 5, 24, 10, 23, 11, tzinfo=UTC),
        "row_count": 2,
        "byte_size": 128,
        "lineage": Lineage(),
    }
    defaults.update(overrides)
    return BaseFrameMeta(**defaults)


def test_meta_construction_minimum_fields():
    meta = _meta()
    assert meta.ref == "frame_abc12345"
    assert meta.session_id == "sess_a3b21c89"
    assert meta.row_count == 2


def test_base_frame_meta_evidence_fields_default() -> None:
    meta = BaseFrameMeta(
        kind="metric_frame",
        ref="frame_abc",
        session_id="sess_1",
        project_root="/tmp",
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=10,
        byte_size=100,
    )
    assert meta.artifact_id is None
    assert meta.evidence_status == "unavailable"
    assert meta.blocking_issues == []
    assert meta.recommended_followups == []
    assert meta.quality is None
    assert meta.confidence_scope is None


def test_meta_kind_required():
    with pytest.raises(Exception):
        BaseFrameMeta()  # type: ignore[call-arg]


def test_frame_construction_wraps_df_and_meta():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    assert f.ref == "frame_abc12345"
    assert f.lineage is f.meta.lineage


def test_to_pandas_returns_copy():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    out = f.to_pandas()
    out.loc[0, "x"] = 999
    assert df.loc[0, "x"] == 1


def test_getitem_delegates_to_df():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    assert list(f["x"]) == [1, 2]


def test_frame_preview_is_pydantic_model():
    assert issubclass(FramePreview, BaseModel)


def test_preview_default_limit_returns_bounded_dto():
    df = pd.DataFrame({"x": list(range(12))})
    f = BaseFrame(_df=df, meta=_meta(row_count=12))
    preview = f.preview()
    assert preview.kind == "metric_frame"
    assert preview.ref == "frame_abc12345"
    assert preview.row_count == 12
    assert preview.returned_row_count == 10
    assert preview.columns == ["x"]
    assert preview.rows == [{"x": idx} for idx in range(10)]
    assert preview.is_truncated is True


def test_frame_preview_repr_is_bounded_agent_hint():
    df = pd.DataFrame({"x": list(range(12))})
    f = BaseFrame(_df=df, meta=_meta(row_count=12))
    preview = f.preview()

    r = repr(preview)

    assert r == (
        "<FramePreview ref=frame_abc12345 kind=metric_frame returned=10/12 "
        "truncated=True; call .show() to inspect>"
    )
    assert "\n" not in r


def test_frame_preview_render_and_show_are_bounded(capsys):
    df = pd.DataFrame({"x": list(range(12)), "y": [f"row-{idx}" for idx in range(12)]})
    f = BaseFrame(_df=df, meta=_meta(row_count=12))
    preview = f.preview()

    rendered = preview.render()

    assert rendered.startswith(
        "FramePreview ref=frame_abc12345 kind=metric_frame returned=10/12 truncated=True"
    )
    assert "columns: x | y" in rendered
    assert "preview:" in rendered
    assert "0 | row-0" in rendered
    assert "4 | row-4" in rendered
    assert "5 | row-5" not in rendered
    assert "... 7 more rows; call .preview(limit=...) or .to_pandas()" in rendered
    assert "- .rows (list[dict])" in rendered
    assert "- .columns" in rendered
    assert not rendered.endswith("\n")

    assert preview.show() is None
    captured = capsys.readouterr()
    assert captured.out == rendered + "\n"


def test_preview_custom_limit_matches_front_rows():
    df = pd.DataFrame({"x": [1, 2, 3, 4, 5]})
    f = BaseFrame(_df=df, meta=_meta())
    preview = f.preview(limit=2)
    assert preview.returned_row_count == 2
    assert preview.rows == [{"x": 1}, {"x": 2}]
    assert preview.is_truncated is True


def test_preview_not_truncated_when_limit_covers_frame():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    preview = f.preview(limit=5)
    assert preview.returned_row_count == 2
    assert preview.is_truncated is False


def test_preview_rejects_invalid_limits():
    df = pd.DataFrame({"x": [1]})
    f = BaseFrame(_df=df, meta=_meta())
    with pytest.raises(FrameReadError):
        f.preview(limit=0)
    with pytest.raises(FrameReadError):
        f.preview(limit=101)


def test_preview_disambiguates_duplicate_columns():
    df = pd.DataFrame([[1, 2, 3]], columns=["value", "value", "value#2"])
    f = BaseFrame(_df=df, meta=_meta(row_count=1))
    preview = f.preview(limit=1)
    assert preview.columns == ["value", "value#2", "value#2#2"]
    assert preview.rows == [{"value": 1, "value#2": 2, "value#2#2": 3}]


def test_preview_normalizes_missing_values():
    df = pd.DataFrame(
        {
            "float_nan": [float("nan")],
            "none": [None],
            "pd_na": [pd.NA],
            "pd_nat": [pd.NaT],
        },
    )
    f = BaseFrame(_df=df, meta=_meta(row_count=1))
    assert f.preview(limit=1).rows == [
        {
            "float_nan": None,
            "none": None,
            "pd_na": None,
            "pd_nat": None,
        },
    ]


def test_preview_empty_frame_returns_columns_and_no_rows():
    df = pd.DataFrame(columns=["x", "y"])
    f = BaseFrame(_df=df, meta=_meta(row_count=0))
    preview = f.preview(limit=5)
    assert preview.row_count == 0
    assert preview.returned_row_count == 0
    assert preview.columns == ["x", "y"]
    assert preview.rows == []
    assert preview.is_truncated is False


def test_frame_preview_forbids_extra_fields():
    with pytest.raises(ValidationError):
        FramePreview(
            kind="metric_frame",
            ref="frame_abc12345",
            row_count=1,
            returned_row_count=1,
            columns=["x"],
            rows=[{"x": 1}],
            is_truncated=False,
            extra_field=True,
        )


def test_frame_no_longer_exposes_head():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    assert not hasattr(f, "head")


def test_to_pandas_head_remains_available_for_pandas_workflows():
    df = pd.DataFrame({"x": [1, 2, 3]})
    f = BaseFrame(_df=df, meta=_meta(row_count=3))
    assert f.to_pandas().head(2).to_dict("records") == [{"x": 1}, {"x": 2}]


def test_shape_columns_len_iter():
    df = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
    f = BaseFrame(_df=df, meta=_meta())
    assert f.shape == (2, 2)
    assert f.columns == ["x", "y"]
    assert len(f) == 2
    rows = list(f)
    assert rows == ["x", "y"]


def test_setitem_raises_frame_mutation_error():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    with pytest.raises(FrameMutationError):
        f["x"] = [99, 99]


def test_arithmetic_raises_frame_mutation_error():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    with pytest.raises(FrameMutationError):
        f + 1


def test_repr_includes_kind_ref_and_row_count():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    r = repr(f)
    assert r.count("\n") == 0
    assert "BaseFrame" in r
    assert "ref=frame_abc12345" in r
    assert "rows=2" in r
    assert "call .show() to inspect" in r


def test_repr_is_one_line_cold_start_hint():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    r = repr(f)
    assert r.count("\n") == 0
    assert r.startswith("<BaseFrame")
    assert "call .show() to inspect" in r
    # No preview data rows should appear in repr
    assert "preview:" not in r


def test_repr_includes_ref_and_rows():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    r = repr(f)
    assert "ref=frame_abc12345" in r
    assert "rows=2" in r


def test_repr_html_returns_none():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    html = f._repr_html_()
    assert html is None


def test_render_returns_string_no_stdout(capsys):
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    result = f.render()
    captured = capsys.readouterr()
    assert isinstance(result, str)
    assert captured.out == ""


def test_render_does_not_end_with_newline():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    assert not f.render().endswith("\n")


def test_render_contains_identity_columns_preview_available():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    rendered = f.render()
    assert "BaseFrame" in rendered
    assert "frame_abc12345" in rendered
    assert "columns:" in rendered
    assert "preview:" in rendered
    assert "available:" in rendered


def test_render_available_never_empty():
    df = pd.DataFrame({"x": [1]})
    f = BaseFrame(_df=df, meta=_meta())
    rendered = f.render()
    lines = rendered.splitlines()
    avail_idx = next(i for i, ln in enumerate(lines) if ln == "available:")
    assert avail_idx < len(lines) - 1
    assert lines[avail_idx + 1].startswith("- ")


def test_render_includes_to_pandas_in_available():
    df = pd.DataFrame({"x": [1]})
    f = BaseFrame(_df=df, meta=_meta())
    assert ".to_pandas()" in f.render()


def test_show_prints_render_plus_newline(capsys):
    df = pd.DataFrame({"x": [1]})
    f = BaseFrame(_df=df, meta=_meta())
    result = f.show()
    captured = capsys.readouterr()
    assert result is None
    assert captured.out == f.render() + "\n"


def test_show_returns_none():
    df = pd.DataFrame({"x": [1]})
    f = BaseFrame(_df=df, meta=_meta())
    assert f.show() is None


def test_render_preview_bounded_at_five_rows():
    df = pd.DataFrame({"x": list(range(20))})
    f = BaseFrame(_df=df, meta=_meta(row_count=20))
    rendered = f.render()
    preview_lines = [
        ln
        for ln in rendered.splitlines()
        if ln
        and not ln.startswith(
            ("BaseFrame", "status:", "columns:", "preview:", "available:", "-", "...")
        )
    ]
    assert len(preview_lines) <= 5


def test_render_truncation_line_actionable():
    df = pd.DataFrame({"x": list(range(20))})
    f = BaseFrame(_df=df, meta=_meta(row_count=20))
    rendered = f.render()
    assert "more rows" in rendered
    assert ".preview(limit=...)" in rendered or ".to_pandas()" in rendered
