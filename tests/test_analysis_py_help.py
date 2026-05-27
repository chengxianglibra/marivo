"""mv.help() introspection."""

from __future__ import annotations

import inspect
import io
from contextlib import redirect_stdout

import marivo.analysis_py as mv
from marivo.analysis_py.errors import SemanticKindMismatchError
from marivo.analysis_py.intents.compare import compare as compare_fn


def _capture(symbol: str | None = None) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        mv.help(symbol)
    return buf.getvalue()


def test_top_level_help_lists_intents_and_helpers() -> None:
    out = _capture()
    assert "observe" in out
    assert "compare" in out
    assert "decompose" in out
    assert "discover" in out
    assert "detect" not in out
    assert "correlate" in out
    assert "session" in out
    assert "help" in out


def test_help_lists_discover_and_not_detect(capsys) -> None:
    mv.help()
    output = capsys.readouterr().out

    assert "mv.discover" in output
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
    assert "transform(" in transform_out
    assert "transform.topk" in transform_out
    assert "transform.rollup" in transform_out

    discover_out = _capture("discover")
    assert "discover(" in discover_out
    assert "discover.point_anomalies" in discover_out
    assert "discover.driver_axes" in discover_out


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
    assert "Base class for all analysis_py errors." not in out
    assert "MetricFrame" in out or "compare" in out


def test_help_for_unknown_symbol_explains_how_to_list() -> None:
    out = _capture("nonexistent_thing_xyz")
    assert "unknown symbol" in out.lower() or "not found" in out.lower()
    assert "mv.help()" in out


def test_help_lists_new_statistical_operators(capsys):
    mv.help()
    out = capsys.readouterr().out

    assert "mv.hypothesis_test" in out
    assert "mv.forecast" in out
    assert "mv.assess_quality" in out


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
    assert "calendar_bucket" in out
    assert "dow_aligned" in out
    assert "holiday_aligned" in out
    assert "holiday_and_dow_aligned" in out
    assert "calendar=" in out
    assert "no separate kind='ordinal'" in out
    assert "align by ordinal position" in out
