"""mv.help() introspection."""

from __future__ import annotations

import inspect
import io
from contextlib import redirect_stdout

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.intents.compare import compare as compare_fn


def _capture(symbol: str | None = None) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        mv.help(symbol)
    return buf.getvalue()


def _capture_json(symbol: str | None = None) -> tuple[dict[str, object], str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = mv.help(symbol, format="json")
    assert isinstance(result, dict)
    return result, buf.getvalue()


def test_top_level_help_lists_intents_and_helpers() -> None:
    out = _capture()
    assert "observe" in out
    assert "compare" in out
    assert "decompose" in out
    assert "discover" in out
    assert "detect" not in out
    assert "correlate" in out
    assert "session" in out
    assert "calendar" in out
    assert "help" in out


def test_help_lists_discover_and_not_detect(capsys) -> None:
    mv.help()
    output = capsys.readouterr().out

    assert "session.discover" in output
    assert "mv.detect" not in output


def test_detect_is_not_exported() -> None:
    assert "detect" not in mv.__all__
    assert not hasattr(mv, "detect")


def test_help_for_intent_includes_signature_and_docstring() -> None:
    out = _capture("compare")
    assert "compare(" in out
    first_doc_line = (inspect.getdoc(compare_fn) or "").strip().splitlines()[0]
    assert first_doc_line, "compare should have a non-empty docstring"
    assert first_doc_line in out


def test_help_for_transform_and_discover_lists_namespace_methods() -> None:
    transform_out = _capture("transform")
    assert "session.transform op helper matrix" in transform_out
    assert "session.transform.topk" in transform_out
    assert "session.transform.rollup" in transform_out

    discover_out = _capture("discover")
    assert "session.discover objective helper matrix" in discover_out
    assert "session.discover.point_anomalies" in discover_out
    assert "session.discover.driver_axes" in discover_out


def test_help_for_intent_does_not_mutate_callable_docstring() -> None:
    original_doc = compare_fn.__doc__
    compare_fn.__doc__ = None

    try:
        out = _capture("compare")

        assert compare_fn.__doc__ is None
        module = inspect.getmodule(compare_fn)
        assert module is not None
        first_doc_line = (inspect.getdoc(module) or "").strip().splitlines()[0]
        assert first_doc_line in out
    finally:
        compare_fn.__doc__ = original_doc


def test_help_for_exception_class_resolves_by_name() -> None:
    out = _capture("SemanticKindMismatchError")
    assert "SemanticKindMismatchError" in out
    assert "MetricFrame" in out or "compare" in out


def test_help_for_exception_class_does_not_use_inherited_base_docstring() -> None:
    assert SemanticKindMismatchError.__doc__ is None

    out = _capture("SemanticKindMismatchError")

    assert "SemanticKindMismatchError" in out
    assert "Base class for all analysis errors." not in out
    assert "MetricFrame" in out or "compare" in out


def test_help_for_unknown_symbol_explains_how_to_list() -> None:
    out = _capture("nonexistent_thing_xyz")
    assert "unknown symbol" in out.lower() or "not found" in out.lower()
    assert "mv.help()" in out


def test_help_lists_new_statistical_operators(capsys):
    mv.help()
    out = capsys.readouterr().out

    assert "session.hypothesis_test" in out
    assert "session.forecast" in out
    assert "session.assess_quality" in out


def test_help_describes_new_statistical_operators(capsys):
    for name in ("hypothesis_test", "forecast", "assess_quality"):
        mv.help(name)
        assert name in capsys.readouterr().out


def test_help_discover_prints_objective_matrix() -> None:
    out = _capture("discover")
    assert "objective" in out
    assert "point_anomalies" in out
    assert "metric_frame" in out
    assert "driver_axes" in out
    assert "search_space" in out
    assert "delta_frame" in out


def test_help_select_prints_field_by_shape_matrix() -> None:
    out = _capture("select")
    assert "attribute-by-shape matrix" in out
    assert "driver_axis" in out
    assert "axis" in out
    assert "point_anomaly" in out


def test_help_transform_prints_op_matrix() -> None:
    out = _capture("transform")
    assert "topk" in out
    assert "rollup" in out
    assert "drop_axes" in out
    assert "limit" in out


def test_help_alignment_prints_variants() -> None:
    out = _capture("alignment")
    assert "window_bucket" in out
    assert "dow_aligned" in out
    assert "holiday_aligned" in out
    assert "holiday_and_dow_aligned" in out
    assert "calendar=" in out
    assert "no separate kind='ordinal'" in out
    assert "align by ordinal position" in out


def test_help_calendar_prints_file_schema_and_entry_example() -> None:
    out = _capture("calendar")
    assert ".marivo/calendar/<name>.json" in out
    assert '"date": "2026-05-01"' in out
    assert '"holiday_id": "labor-day"' in out
    assert "adjusted_workdays" in out
    assert "use holiday_id rather than name/label" in out


# --- format="json" tests ---


def test_mv_help_json_top_level_returns_dict() -> None:
    result, stdout = _capture_json()
    assert stdout == ""
    assert result["schema_version"] == "1"
    assert result["surface"] == "marivo.analysis"
    assert isinstance(result["entries"], list)
    assert len(result["entries"]) > 0
    assert "matrix_topics" in result
    assert "discover" in result["matrix_topics"]


def test_mv_help_json_discover_returns_structured() -> None:
    result, stdout = _capture_json("discover")
    assert stdout == ""
    assert result["kind"] == "matrix_topic"
    assert result["symbol"] == "discover"
    objectives = result["objectives"]
    assert isinstance(objectives, list)
    assert len(objectives) > 0
    first = objectives[0]
    assert "objective" in first
    assert "shape" in first
    assert "compatibility" in first
    assert "required_kwargs" in first


def test_mv_help_json_select_returns_structured() -> None:
    result, stdout = _capture_json("select")
    assert stdout == ""
    assert result["kind"] == "matrix_topic"
    assert "fields_by_shape" in result
    assert isinstance(result["fields_by_shape"], dict)


def test_mv_help_json_transform_returns_structured() -> None:
    result, stdout = _capture_json("transform")
    assert stdout == ""
    assert result["kind"] == "matrix_topic"
    ops = result["ops"]
    assert isinstance(ops, list)
    assert len(ops) > 0
    assert "op" in ops[0]
    assert "required_kwargs" in ops[0]


def test_mv_help_json_alignment_returns_structured() -> None:
    result, stdout = _capture_json("alignment")
    assert stdout == ""
    assert result["kind"] == "matrix_topic"
    variants = result["variants"]
    assert isinstance(variants, list)
    assert len(variants) == 4
    assert variants[0]["kind"] == "window_bucket"
    assert variants[0]["requires_calendar"] is False
    assert "behavior_notes" in result


def test_mv_help_json_calendar_returns_structured() -> None:
    result, stdout = _capture_json("calendar")
    assert stdout == ""
    assert result["kind"] == "matrix_topic"
    assert result["location"] == ".marivo/calendar/<name>.json"
    assert isinstance(result["top_level_schema"], dict)
    assert isinstance(result["entry_schema"], dict)
    assert isinstance(result["example"], dict)
    assert "notes" in result


def test_mv_help_json_callable_symbol() -> None:
    result, stdout = _capture_json("compare")
    assert stdout == ""
    assert result["kind"] == "callable"
    assert "compare" in result["signature"]
    assert isinstance(result["doc"], str)


def test_mv_help_json_class_symbol() -> None:
    result, stdout = _capture_json("SemanticKindMismatchError")
    assert stdout == ""
    assert result["kind"] == "class"


def test_mv_help_json_unknown_symbol() -> None:
    result, stdout = _capture_json("nonexistent_xyz")
    assert stdout == ""
    assert "error" in result


def test_mv_help_json_returns_none_on_text() -> None:
    assert mv.help() is None


def test_mv_help_json_does_not_print(capsys) -> None:
    mv.help(format="json")
    assert capsys.readouterr().out == ""


def test_mv_help_invalid_format_raises() -> None:
    with pytest.raises(ValueError, match="format must be"):
        mv.help(format="yaml")


def test_mv_help_text_not_in_all() -> None:
    assert "help_text" not in mv.__all__
