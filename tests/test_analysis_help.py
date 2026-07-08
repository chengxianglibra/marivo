"""mv.help() introspection."""

from __future__ import annotations

import inspect
import io
from contextlib import redirect_stdout
from typing import Any, cast

from pytest import CaptureFixture

import marivo.analysis as mv
from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.session.core import Session
from marivo.introspection.surface import render
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref


def _json_data(symbol: str | None = None) -> dict[str, Any]:
    """Return the JSON descriptor dict for a symbol using the internal render."""
    from marivo.analysis.help import _surface

    return cast("dict[str, Any]", render(_surface(), symbol, "json"))


_HELP_ONLY_ENTRIES = {
    "agent_surface",
    "observe",
    "compare",
    "attribute",
    "discover",
    "transform",
    "correlate",
    "forecast",
    "assess_quality",
    "hypothesis_test",
    "derive_metric_frame",
    "alignment",
    "calendar",
    "select",
    "cumulative_frame",
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
    assert "attribute" in out
    assert "decompose" not in out
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


def test_help_attribute_mentions_missing_axis_materialization_without_mode() -> None:
    out = _capture("attribute").lower()

    assert "missing" in out
    assert "materialize" in out
    assert "explicit" in out
    assert "mode=" not in out
    assert "recursive" not in out


def test_help_decompose_is_not_top_level_agent_default() -> None:
    out = _capture().lower()

    assert "attribute" in out
    assert "decompose" not in out


def test_help_for_intent_includes_signature_and_docstring() -> None:
    from marivo.analysis.intents.select import select as select_fn

    cases = [
        ("compare", Session.compare),
        ("observe", Session.observe),
        ("select", select_fn),
    ]
    for symbol, callable_obj in cases:
        out = _capture(symbol)
        assert "Signature:" in out, f"{symbol} help should include signature"
        assert f"{symbol}(" in out, f"{symbol} help should include callable name in signature"
        first_doc_line = (inspect.getdoc(callable_obj) or "").strip().splitlines()[0]
        assert first_doc_line, f"{symbol} callable should have a non-empty docstring"
        assert first_doc_line in out, f"{symbol} help should include first docstring line"


def test_help_attribute_mentions_component_mix_and_sampled_fold_boundary() -> None:
    out = _capture("attribute").lower()

    assert "component-aware ratio" in out
    assert "weighted-average" in out
    assert "non-linear sampled folds" in out


def test_help_for_session_intent_aliases_matches_canonical_target() -> None:
    intents = (
        "observe",
        "compare",
        "attribute",
        "correlate",
        "forecast",
        "assess_quality",
        "hypothesis_test",
        "derive_metric_frame",
    )

    for intent in intents:
        expected = _capture(intent)
        for alias in (
            f"mv.Session.{intent}",
            f"session.{intent}",
            f"mv.session.{intent}",
        ):
            out = _capture(alias)
            assert out == expected, alias
            assert "Unknown help target" not in out, alias
            assert "Signature:" in out, alias


def test_help_for_session_namespace_aliases_matches_canonical_target() -> None:
    for topic in ("discover", "transform"):
        expected = _capture(topic)
        for alias in (
            f"mv.Session.{topic}",
            f"session.{topic}",
            f"mv.session.{topic}",
        ):
            assert _capture(alias) == expected, alias


def test_help_for_observe_documents_empty_dimensions_as_no_axes() -> None:
    out = _capture("observe")

    assert "dimensions=None or dimensions=[] means no segment axes" in out


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
    original_doc = Session.compare.__doc__
    Session.compare.__doc__ = None

    try:
        out = _capture("compare")

        assert Session.compare.__doc__ is None
        module = inspect.getmodule(Session.compare)
        assert module is not None
        first_doc_line = (inspect.getdoc(module) or "").strip().splitlines()[0]
        assert first_doc_line not in out
    finally:
        Session.compare.__doc__ = original_doc


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
    assert "mv.window_bucket()" in out
    assert "dow_aligned" in out
    assert "mv.dow_aligned(calendar=mv.CalendarRef(...))" in out
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


def test_help_json_top_level_is_canonical() -> None:
    result = _json_data()

    assert isinstance(result, dict)
    assert result["schema_version"] == "1"
    assert result["surface"] == "marivo.analysis"
    assert result["kind"] == "surface"
    entries = cast("list[dict[str, Any]]", result["entries"])
    families = cast("list[dict[str, Any]]", result["families"])
    enumerated = {entry["name"] for entry in entries}
    folded = {name for fam in families for name in fam["members"]}
    assert enumerated.isdisjoint(folded)
    assert enumerated | folded == set(mv.__all__) | _HELP_ONLY_ENTRIES


def test_help_rejects_removed_load_frame_symbol() -> None:
    result = _json_data("load_frame")

    assert isinstance(result, dict)
    assert result["kind"] == "unknown"
    assert result["symbol"] == "load_frame"


def test_help_resolves_core_runtime_and_result_types() -> None:
    expected_kinds = {
        "Session": "class",
        "BaseFrameMeta": "class",
        "SessionSummary": "class",
        "JobSummary": "class",
        "Lineage": "class",
        "LineageStep": "class",
    }

    for symbol, expected_kind in expected_kinds.items():
        result = _json_data(symbol)
        assert result["kind"] == expected_kind, symbol
        assert result["symbol"] == symbol
        assert result["summary"], symbol


def test_help_topics_json_have_structured_content() -> None:
    expected_keys = {
        "agent_surface": "core_operators",
        "discover": "objectives",
        "select": "fields_by_shape",
        "transform": "ops",
        "alignment": "variants",
        "calendar": "schema",
    }

    for symbol, key in expected_keys.items():
        result = _json_data(symbol)
        assert isinstance(result, dict)
        assert result["kind"] == "topic"
        content = cast("dict[str, Any]", result["content"])
        assert key in content


def test_help_agent_surface_topic_teaches_phase3_boundaries() -> None:
    result = _json_data("agent_surface")

    assert result["kind"] == "topic"
    content = cast("dict[str, Any]", result["content"])
    operators = {
        item["operator"] for item in cast("list[dict[str, str]]", content["core_operators"])
    }
    assert operators == {
        "observe",
        "compare",
        "attribute",
        "discover.<objective>",
        "correlate",
        "hypothesis_test",
        "forecast",
        "derive_metric_frame",
        "assess_quality",
    }

    rendered = _capture("agent_surface")
    assert "contract().affordances" in rendered
    assert "mechanical compatibility" in rendered
    assert "not advisory endorsements from Marivo" in rendered
    assert "decompose" not in rendered


def test_help_agent_surface_topic_includes_catalog_discovery() -> None:
    result = _json_data("agent_surface")
    content = cast("dict[str, Any]", result["content"])
    discovery = cast("list[str]", content["catalog_discovery"])
    assert discovery, "catalog_discovery must be a non-empty list of example calls"
    joined = "\n".join(discovery)

    assert 'catalog.list("metric")' in joined
    assert 'catalog.list("dimension"' in joined
    assert 'catalog.get("metric.' in joined

    rendered = _capture("agent_surface")
    assert 'catalog.list("metric")' in rendered
    assert 'catalog.get("metric.' in rendered
    assert "observe" in rendered


def test_help_json_metric_frame_descriptor_lists_methods_and_workflow() -> None:
    result = _json_data("MetricFrame")

    assert isinstance(result, dict)
    assert result["kind"] == "frame"
    methods = {entry["name"] for entry in cast("list[dict[str, Any]]", result["methods"])}
    assert {"to_pandas", "components", "as_time_series"} <= methods
    assert result["constructed_by"]


def test_help_json_coverage_frame_descriptor() -> None:
    result = _json_data("CoverageFrame")

    assert isinstance(result, dict)
    assert result["kind"] == "frame"
    assert result["symbol"] == "CoverageFrame"
    assert result["summary"]
    assert result["constructed_by"] == "MetricFrame.coverage()"


def test_help_json_frame_method_descriptor() -> None:
    result = _json_data("MetricFrame.components")

    assert isinstance(result, dict)
    assert result["kind"] == "callable"
    assert result["symbol"] == "MetricFrame.components"
    assert "MetricFrame.components(" in cast("str", result["signature"])
    assert "Load the linked ComponentFrame" in cast("str", result["doc"])


# --- return type is always None ---


def test_mv_help_returns_none():
    result = mv.help()
    assert result is None


def test_mv_help_with_symbol_returns_none(capsys: CaptureFixture[str]):
    result = mv.help("observe")
    assert result is None


def test_ms_help_returns_none():
    import marivo.semantic as ms

    result = ms.help()
    assert result is None


def test_ms_help_with_symbol_returns_none(capsys: CaptureFixture[str]):
    import marivo.semantic as ms

    result = ms.help("metric")
    assert result is None


# --- prints bounded help ---


def test_mv_help_prints_something(capsys: CaptureFixture[str]):
    mv.help()
    captured = capsys.readouterr()
    assert len(captured.out) > 0


def test_mv_help_observe_prints_something(capsys: CaptureFixture[str]):
    mv.help("observe")
    captured = capsys.readouterr()
    assert len(captured.out) > 0


# --- canonical help target accepts string and None ---


def test_mv_help_none_target_is_top_level(capsys: CaptureFixture[str]):
    mv.help(None)
    captured = capsys.readouterr()
    assert "marivo.analysis" in captured.out


# --- help output stays within 80-line budget ---


def test_mv_help_output_within_80_lines(capsys: CaptureFixture[str]):
    mv.help("observe")
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) <= 80


def test_ms_help_output_within_80_lines(capsys: CaptureFixture[str]):
    import marivo.semantic as ms

    ms.help("metric")
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) <= 100


# --- semantic ref object support ---


def test_mv_help_accepts_metric_ref(capsys: CaptureFixture[str]):
    import pytest

    from marivo.semantic.errors import SemanticError

    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    with pytest.raises(Exception) as exc_info:
        mv.help(ref)
    assert isinstance(exc_info.value, SemanticError)


def test_mv_help_with_project_and_metric_ref(semantic_project_factory, capsys: CaptureFixture[str]):
    import pytest

    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n",
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "import marivo.datasource as md\n"
                "warehouse = md.ref('datasource.warehouse')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=ms.table('orders'))\n"
                "\n"
                "@ms.metric(entities=[orders], additivity='additive', "
                "name='revenue', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    from marivo.semantic.catalog import SemanticCatalog

    catalog = SemanticCatalog(project)
    domain = catalog.get("domain.sales")
    metric_ids = [ref.id for ref in catalog.list("metric", scope=f"domain.{domain.ref.id}").refs()]
    if not metric_ids:
        pytest.skip("no metrics in fixture")
    ref = make_ref(metric_ids[0], SemanticKind.METRIC)
    mv.help(ref, project=project)
    captured = capsys.readouterr()
    assert len(captured.out) > 0
    assert metric_ids[0].split(".")[-1] in captured.out or metric_ids[0] in captured.out


def test_mv_help_semantic_prefix_routes_to_semantic_surface(capsys: CaptureFixture[str]):
    mv.help("semantic.metric")
    captured = capsys.readouterr()
    assert "metric" in captured.out.lower()
    assert len(captured.out) > 0


# --- type-alias kind labels ---


def test_help_type_aliases_have_correct_kind() -> None:
    from marivo.analysis.help import _TYPE_ALIASES

    for name in _TYPE_ALIASES:
        result = _json_data(name)
        assert result["kind"] == "type-alias", f"{name}: expected type-alias, got {result['kind']}"


def test_help_type_alias_descriptor_has_signature() -> None:
    result = _json_data("AlignmentKind")
    assert "window_bucket" in cast("str", result["signature"])


def test_help_top_level_all_entries_have_summaries() -> None:
    result = _json_data()
    entries = cast("list[dict[str, Any]]", result["entries"])
    empty = [e["name"] for e in entries if not e["summary"]]
    assert not empty, f"entries with empty summary: {empty}"


# --- metric unit in help ---


def test_help_semantic_metric_ref_prints_unit(capsys, semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n",
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "import marivo.datasource as md\n"
                "warehouse = md.ref('datasource.warehouse')\n"
                "orders = ms.entity(name='orders', datasource=warehouse, "
                "source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', name='revenue', "
                " unit='CNY')\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
            "datasources/warehouse.py": (
                "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
            ),
        }
    )
    mv.help(make_ref("sales.revenue", SemanticKind.METRIC), project=project)
    out = capsys.readouterr().out
    assert "unit: CNY" in out


# --- sampled semi-additive observe help ---


def test_help_observe_mentions_sampled_fold_coverage(capsys) -> None:
    mv.help("observe")
    out = capsys.readouterr().out
    assert "sampled semi-additive" in out
    assert "coverage()" in out
    assert "re-run observe" in out


# --- affordance language replaces recommendation language ---


def test_help_no_longer_teaches_recommended_followups() -> None:
    full = _capture()
    session_help = _capture("Session")
    candidate_help = _capture("CandidateSet")
    agent_surface_help = _capture("agent_surface")

    combined = "\n".join([full, session_help, candidate_help, agent_surface_help]).lower()
    assert "recommended_followups" not in combined
    assert "recommended follow-up" not in combined
    assert "recommend follow" not in combined
    assert "contract().affordances" in combined


def test_help_json_frame_contract_uses_affordance_language() -> None:
    result = _json_data("MetricFrame")

    assert "next_intents" not in result
    rendered = str(result).lower()
    assert "recommended" not in rendered

    # Affordance language lives in the agent_surface topic, not in frame descriptors.
    agent_surface = _json_data("agent_surface")
    agent_rendered = str(agent_surface).lower()
    assert "affordance" in agent_rendered
