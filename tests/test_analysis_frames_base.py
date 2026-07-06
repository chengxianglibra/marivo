"""BaseFrameMeta + BaseFrame: thin pandas wrapper with explicit boundaries."""

import subprocess
import sys
from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import ValidationError

from marivo.analysis.errors import FrameMutationError
from marivo.analysis.frames._content_hash import stable_meta_payload
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
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
    frame = BaseFrame(_df=pd.DataFrame({"value": [1.0]}), meta=meta)

    assert meta.artifact_id is None
    assert meta.evidence_status == "unavailable"
    assert meta.blocking_issues == []
    assert meta.quality_summary is None
    assert frame.quality_summary is None
    assert meta.confidence_scope is None
    assert "quality" not in meta.model_dump()
    assert "recommended_followups" not in meta.model_dump()


def test_base_frame_meta_accepts_analysis_purpose_without_content_identity() -> None:
    meta = _meta(analysis_purpose="确认收入下降是否真实")

    assert meta.analysis_purpose == "确认收入下降是否真实"
    assert "analysis_purpose" not in stable_meta_payload(meta)


def test_render_includes_analysis_purpose_when_present() -> None:
    frame = BaseFrame(
        _df=pd.DataFrame({"value": [1.0]}),
        meta=_meta(analysis_purpose="确认收入下降是否真实"),
    )

    rendered = frame.render()

    assert "analysis_purpose: 确认收入下降是否真实" in rendered


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


def test_render_available_teaches_show_contract_to_pandas():
    df = pd.DataFrame({"x": [1]})
    f = BaseFrame(_df=df, meta=_meta())
    rendered = f.render()
    assert ".show()" in rendered
    assert ".contract()" in rendered
    assert ".to_pandas()" in rendered


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


def test_render_includes_all_small_rows_under_default_byte_budget():
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
    assert preview_lines == [str(value) for value in range(20)]
    assert "output truncated" not in rendered
    assert "more rows" not in rendered


def test_render_truncation_line_uses_byte_budget_marker():
    cap = 260
    df = pd.DataFrame(
        {
            "name": [f"row-{idx}" for idx in range(40)],
            "payload": ["payload-" + ("x" * 30) for _ in range(40)],
        }
    )
    f = BaseFrame(_df=df, meta=_meta(row_count=40))
    rendered = f.render(max_output_bytes=cap)
    assert len(rendered.encode()) <= cap
    assert f"output truncated at {cap} bytes" in rendered
    assert "omitted:" in rendered
    assert "preview" in rendered
    assert "rows" in rendered
    assert "pass max_output_bytes=None for full output" in rendered
    assert "more rows" not in rendered
    assert ".to_pandas()" in rendered


def test_base_frame_exposes_phase1_artifact_protocol() -> None:
    df = pd.DataFrame(
        {
            "bucket_start": ["2026-06-18", "2026-06-19"],
            "country": ["US", "CA"],
            "value": [10.0, 20.0],
        }
    )
    frame = BaseFrame(
        _df=df,
        meta=_meta(
            kind="metric_frame",
            ref="frame_protocol",
            content_hash="sha256:" + "1" * 64,
        ),
    )

    assert frame.kind == "metric_frame"
    assert frame.quality_summary is None
    assert frame.blocking_issues == []
    assert frame.state.materialization == "materialized"
    assert frame.state.content_hash == "sha256:" + "1" * 64

    contract = frame.contract()
    assert contract.kind == "metric_frame"
    assert contract.ref == "frame_protocol"
    assert contract.is_canonical is True
    assert contract.blocking_issues == []
    assert contract.affordances == []
    assert [(column.name, column.role) for column in contract.artifact_schema.columns] == [
        ("bucket_start", "time"),
        ("country", "dimension"),
        ("value", "value"),
    ]


def test_analysis_import_does_not_emit_artifact_contract_schema_shadow_warning() -> None:
    completed = subprocess.run(
        [sys.executable, "-W", "default", "-c", "import marivo.analysis"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert 'Field name "schema" in "ArtifactContract"' not in completed.stderr


def test_phase1_protocol_objects_are_closed_models() -> None:

    from marivo.analysis.frames.base import (
        ArtifactAffordance,
        ArtifactColumn,
        ArtifactContract,
        ArtifactParamTemplate,
        ArtifactPrecondition,
        ArtifactSchema,
        ArtifactState,
    )

    for model_cls, kwargs in [
        (
            ArtifactColumn,
            {"name": "value", "dtype": "float64", "nullable": False, "role": "value"},
        ),
        (
            ArtifactSchema,
            {
                "columns": [],
                "semantic_shape": None,
            },
        ),
        (
            ArtifactPrecondition,
            {"check": "has_rows", "status": "pass", "reason": None},
        ),
        (
            ArtifactParamTemplate,
            {"deterministic_slots": {"source": "frame_x"}, "judgment_slots": ["axis"]},
        ),
        (
            ArtifactAffordance,
            {
                "operator": "compare",
                "required_inputs": ["metric_frame"],
                "preconditions": [],
                "param_template": {
                    "deterministic_slots": {"left": "frame_x"},
                    "judgment_slots": ["right"],
                },
                "expected_output_family": "delta_frame",
            },
        ),
        (
            ArtifactContract,
            {
                "kind": "metric_frame",
                "ref": "frame_x",
                "is_canonical": True,
                "artifact_schema": ArtifactSchema(columns=[]),
                "blocking_issues": [],
                "affordances": [],
            },
        ),
        (
            ArtifactState,
            {"materialization": "materialized", "content_hash": None},
        ),
    ]:
        with pytest.raises(ValidationError):
            model_cls(**kwargs, unexpected=True)


def test_base_frame_contract_emits_affordances_for_non_empty_next_intents() -> None:
    class _ContractedFrame(BaseFrame):
        _NEXT_INTENTS: tuple[str, ...] = ("compare", "assess_quality")

    df = pd.DataFrame({"value": [1.0]})
    frame = _ContractedFrame(
        _df=df,
        meta=_meta(kind="metric_frame", ref="frame_contracted"),
    )

    contract = frame.contract()

    assert contract.is_canonical is True
    assert [a.operator for a in contract.affordances] == ["compare", "assess_quality"]
    assert contract.affordances[0].expected_output_family == "delta_frame"
    assert contract.affordances[1].expected_output_family == "quality_report"
    assert contract.affordances[0].required_inputs == ["metric_frame"]
    assert contract.affordances[0].param_template.deterministic_slots == {
        "source_ref": "frame_contracted"
    }
    assert contract.affordances[0].param_template.judgment_slots == []
