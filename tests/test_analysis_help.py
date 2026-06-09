"""mv.help() introspection."""

from __future__ import annotations

import inspect
import io
import json
from contextlib import redirect_stdout
from typing import Any, cast

from pytest import CaptureFixture

import marivo.analysis as mv
from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.intents.compare import compare as compare_fn

_HELP_ONLY_ENTRIES = {
    "observe",
    "compare",
    "decompose",
    "discover",
    "transform",
    "correlate",
    "forecast",
    "assess_quality",
    "hypothesis_test",
    "alignment",
    "calendar",
    "select",
}


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
    assert "calendar" in out
    assert "help" in out


def test_help_lists_discover_and_not_detect(capsys: CaptureFixture[str]) -> None:
    mv.help()
    output = capsys.readouterr().out

    assert "discover" in output
    assert "mv.detect" not in output


def test_detect_is_not_exported() -> None:
    assert "detect" not in mv.__all__
    assert not hasattr(mv, "detect")


def test_execution_operators_remain_help_only() -> None:
    assert "observe" not in mv.__all__
    assert "compare" not in mv.__all__
    assert not hasattr(mv, "observe")
    assert not hasattr(mv, "compare")

    out = _capture()
    assert "help:observe" in out
    assert "help:compare" in out
    assert "mv.observe" not in out
    assert "mv.compare" not in out


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
        assert first_doc_line not in out
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
    assert "unknown help target" in out.lower()
    assert "help()" in out


def test_help_lists_new_statistical_operators(capsys: CaptureFixture[str]) -> None:
    mv.help()
    out = capsys.readouterr().out

    assert "hypothesis_test" in out
    assert "forecast" in out
    assert "assess_quality" in out


def test_help_describes_new_statistical_operators(capsys: CaptureFixture[str]) -> None:
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
    assert "align by ordinal bucket position" in out
    assert "Calendar alignment output columns" in out
    assert "period_week_offset" in out
    assert "holiday_ordinal" in out
    assert "workday_ordinal" in out
    assert "baseline_date" in out


def test_help_calendar_prints_file_schema_and_entry_example() -> None:
    out = _capture("calendar")
    assert ".marivo/calendar/<name>.json" in out
    assert '"date": "2026-05-01"' in out
    assert '"holiday_id": "labor-day"' in out
    assert "adjusted_workdays" in out
    assert '"timezone"' not in out
    assert "Calendar files define dates only" in out
    assert "use holiday_id rather than name/label" in out


def test_help_json_top_level_is_canonical_and_prints_json(
    capsys: CaptureFixture[str],
) -> None:
    result = mv.help(format="json")
    captured = capsys.readouterr()

    assert captured.out != ""
    assert json.loads(captured.out) == result
    assert isinstance(result, dict)
    assert result["schema_version"] == "1"
    assert result["surface"] == "marivo.analysis"
    assert result["kind"] == "surface"
    entries = cast("list[dict[str, Any]]", result["entries"])
    assert {entry["name"] for entry in entries} == set(mv.__all__) | _HELP_ONLY_ENTRIES


def test_help_json_load_frame_uses_own_docstring_only() -> None:
    result = mv.help("load_frame", format="json")

    assert isinstance(result, dict)
    assert result["kind"] == "callable"
    assert result["symbol"] == "load_frame"
    assert "load_frame(" in cast("str", result["signature"])
    doc = cast("str", result["doc"])
    assert "Load a persisted analysis frame" in doc
    assert "Load persisted analysis frames." not in doc


def test_help_topics_json_have_structured_content() -> None:
    expected_keys = {
        "discover": "objectives",
        "select": "fields_by_shape",
        "transform": "ops",
        "alignment": "variants",
        "calendar": "schema",
    }

    for symbol, key in expected_keys.items():
        result = mv.help(symbol, format="json")
        assert isinstance(result, dict)
        assert result["kind"] == "topic"
        content = cast("dict[str, Any]", result["content"])
        assert key in content


def test_help_json_metric_frame_descriptor_lists_methods_and_workflow() -> None:
    result = mv.help("MetricFrame", format="json")

    assert isinstance(result, dict)
    assert result["kind"] == "frame"
    methods = {entry["name"] for entry in cast("list[dict[str, Any]]", result["methods"])}
    assert {"to_pandas", "components", "as_time_series"} <= methods
    assert result["next_intents"]
    assert result["constructed_by"]


def test_help_json_frame_method_descriptor() -> None:
    result = mv.help("MetricFrame.components", format="json")

    assert isinstance(result, dict)
    assert result["kind"] == "callable"
    assert result["symbol"] == "MetricFrame.components"
    assert "MetricFrame.components(" in cast("str", result["signature"])
    assert "Load the linked ComponentFrame" in cast("str", result["doc"])


def test_help_rejects_unknown_format() -> None:
    try:
        mv.help(format="yaml")  # type: ignore[arg-type]
    except ValueError as exc:
        assert str(exc) == "format must be 'text' or 'json'"
    else:
        raise AssertionError("mv.help should reject unsupported formats")


def test_help_json_report_artifact_has_fields_and_no_validator_methods() -> None:
    result = mv.help("MarivoReportArtifact", format="json")
    assert isinstance(result, dict)
    assert "fields" in result
    field_names = {f["name"] for f in result["fields"]}
    assert "manifest" in field_names
    assert "report_spec" in field_names
    assert "datasets" in field_names
    method_names = {m["name"] for m in result.get("methods", [])}
    assert "validate_dataset_keys" not in method_names


def test_help_json_report_manifest_has_fields() -> None:
    result = mv.help("ReportManifest", format="json")
    assert isinstance(result, dict)
    assert "fields" in result
    field_names = {f["name"] for f in result["fields"]}
    assert "kind" in field_names
    assert "report_id" in field_names
