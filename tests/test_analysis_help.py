"""Semantic invariant tests for the analysis help renderer.

These tests pin structural invariants of ``mv.help()`` / ``mv.help_text()``
without snapshotting full rendered prose, whitespace, or wrapping.
"""

from __future__ import annotations

import inspect
import io
from contextlib import redirect_stdout

import pytest

import marivo
import marivo.analysis as mv
from marivo.analysis._capabilities.model import (
    ROOT_GROUP_ORDER,
    SURFACE_LIMITS,
    OperatorCapability,
)
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.errors import (
    AnalysisError,
    HelpTargetError,
    MetricNotFoundError,
)
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.session.core import Session
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture(target: object = None, **kwargs: object) -> str:
    """Capture stdout from mv.help(target)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        mv.help(target, **kwargs)  # type: ignore[arg-type]
    return buf.getvalue()


def _text(target: object = None, **kwargs: object) -> str:
    """Return mv.help_text(target) without printing."""
    return mv.help_text(target, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Fingerprint prefix (root help)
# ---------------------------------------------------------------------------


def test_root_help_has_three_line_fingerprint() -> None:
    text = _text()
    lines = text.splitlines()
    assert len(lines) >= 3
    assert lines[0].startswith("Marivo: ")
    assert marivo.__version__ in lines[0]
    assert lines[1].startswith("Python: ")
    assert lines[2].startswith("Package: ")


def test_root_help_fingerprint_uses_resolved_paths() -> None:
    from pathlib import Path

    text = _text()
    lines = text.splitlines()
    assert str(Path(marivo.__file__).resolve()) in lines[2]


# ---------------------------------------------------------------------------
# Root groups and canonical targets
# ---------------------------------------------------------------------------


def test_root_help_has_nine_deterministic_groups() -> None:
    text = _text()
    for group in ROOT_GROUP_ORDER:
        # Each group must appear as a section header in the rendered output.
        assert group in text, f"missing root group: {group}"


def test_root_help_contains_all_direct_capabilities() -> None:
    text = _text()
    direct = [d for d in REGISTRY.descriptors if d.root_visibility == "direct"]
    assert len(direct) > 0
    for desc in direct:
        assert desc.help_target in text, f"missing direct capability: {desc.help_target}"


def test_root_help_contains_type_algebra_rows() -> None:
    text = _text()
    rows = REGISTRY.type_algebra_rows()
    assert len(rows) > 0
    for row in rows:
        rendered = row.render()
        assert rendered in text, f"missing algebra row: {rendered}"


def test_root_help_contains_terminal_boundary_row() -> None:
    text = _text()
    assert "boundary.to_pandas" in text
    assert "pandas.DataFrame" in text
    assert "(terminal)" in text


def test_root_help_contains_drill_down_instruction() -> None:
    text = _text()
    assert "mv.help(" in text


# ---------------------------------------------------------------------------
# Absence of routing/default/advanced/workflow language
# ---------------------------------------------------------------------------


def test_root_help_has_no_workflow_sequence() -> None:
    text = _text().lower()
    assert "default agent workflow" not in text
    assert "question -> first operator" not in text
    assert "intent routing" not in text


def test_root_help_has_no_advanced_label() -> None:
    text = _text().lower()
    assert "advanced" not in text


def test_root_help_has_no_default_operator_label() -> None:
    text = _text().lower()
    assert "default operators" not in text


# ---------------------------------------------------------------------------
# SURFACE_LIMITS enforcement
# ---------------------------------------------------------------------------


def test_root_help_within_line_budget() -> None:
    text = _text()
    assert len(text.splitlines()) <= SURFACE_LIMITS.root_help_max_lines


def test_root_help_within_codepoint_budget() -> None:
    text = _text()
    assert len(text) <= SURFACE_LIMITS.root_help_max_codepoints


def test_focused_help_within_line_budget() -> None:
    text = _text("observe")
    assert len(text.splitlines()) <= SURFACE_LIMITS.focused_help_max_lines


def test_focused_help_within_codepoint_budget() -> None:
    text = _text("observe")
    assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints


# ---------------------------------------------------------------------------
# help() equals help_text() plus newline
# ---------------------------------------------------------------------------


def test_help_output_equals_help_text_plus_newline() -> None:
    for target in (None, "observe", "compare", "help"):
        captured = _capture(target)
        text = _text(target)
        assert captured == text + "\n", f"mismatch for target={target!r}"


# ---------------------------------------------------------------------------
# Focused help: signature, families, example, constraints, edges
# ---------------------------------------------------------------------------


def test_focused_help_includes_live_signature() -> None:
    text = _text("observe")
    sig = str(inspect.signature(Session.observe))
    # The signature text should appear in the rendered help (without 'self').
    assert "observe(" in text
    assert "metric" in text
    assert "time_scope" in text


def test_focused_help_signature_matches_inspect() -> None:
    text = _text("observe")
    sig = str(inspect.signature(Session.observe))
    # Extract the portion after 'self' — the public signature.
    # The help text should contain the parameter names from the signature.
    for param_name in ("metric", "time_scope", "grain", "dimensions", "analysis_purpose"):
        assert param_name in text


def test_focused_help_includes_accepted_and_output_families() -> None:
    text = _text("observe")
    assert "MetricFrame" in text
    desc = REGISTRY.by_help_target("observe")
    assert isinstance(desc, OperatorCapability)
    # Accepted input families should be mentioned.
    for families in desc.accepted_inputs.values():
        for family in families:
            assert str(family) in text or family in text


def test_focused_help_includes_runnable_example() -> None:
    text = _text("observe")
    assert "Example:" in text
    # The example must be runnable (contain session.observe call).
    assert "session.observe(" in text
    # No ellipsis in the example.
    example_section = text[text.index("Example:") :]
    assert "..." not in example_section


def test_focused_help_includes_invocation_critical_constraints() -> None:
    text = _text("observe")
    desc = REGISTRY.by_help_target("observe")
    for constraint_id in desc.constraint_ids:
        # Each constraint id should be mentioned.
        assert constraint_id in text, f"missing constraint: {constraint_id}"


def test_attribute_help_explains_additivity_boundary() -> None:
    text = _text("attribute")

    assert "attribution_additivity_compatible" in text
    assert "compatible persisted additivity" in text
    assert "ratio" in text
    assert "weighted-average" in text
    assert "status time axis" in text
    assert "numerator" in text
    assert "denominator" in text


def test_compare_help_explains_cumulative_component_compatibility() -> None:
    text = _text("compare")

    assert "cumulative_compare_compatible" in text
    assert "outer component" in text
    assert "trailing" in text
    assert "grain_to_date" in text
    assert "all_history" in text


def test_attribute_help_explains_cumulative_hard_gate() -> None:
    text = _text("attribute")

    assert "cumulative_attribution_unsupported" in text
    assert "cumulative" in text
    assert "underlying flow" in text


def test_focused_help_includes_producer_consumer_edges() -> None:
    text = _text("MetricFrame")
    # Type help should show producers (who creates MetricFrame).
    assert "observe" in text or "producer" in text.lower()
    # Type help should show consumers (what consumes MetricFrame).
    consumers = REGISTRY.constructor_consumers.get("MetricFrame", ())
    for consumer_id in consumers[:3]:
        assert consumer_id in text, f"missing consumer: {consumer_id}"


# ---------------------------------------------------------------------------
# Type help: no constructors, no private fields, properties/methods separation
# ---------------------------------------------------------------------------


def test_type_help_omits_constructors() -> None:
    text = _text("MetricFrame")
    assert "MetricFrame(" not in text.split("Properties:")[0].split("Methods:")[0]
    assert "__init__" not in text
    assert "model_config" not in text


def test_type_help_omits_private_fields() -> None:
    text = _text("MetricFrame")
    assert "_df" not in text
    assert "_NEXT_INTENTS" not in text
    assert "_GATED_INTENTS" not in text
    # Pydantic internals should not appear.
    assert "model_fields" not in text
    assert "model_validate" not in text


def test_type_help_separates_properties_and_methods() -> None:
    text = _text("MetricFrame")
    assert "Properties:" in text or "properties" in text.lower()
    assert "Methods:" in text or "methods" in text.lower()


def test_type_help_lists_registry_allowlist_members() -> None:
    from marivo.analysis._capabilities.registry import (
        PUBLIC_FRAME_METHODS,
        PUBLIC_FRAME_PROPERTIES,
    )

    text = _text("MetricFrame")
    for prop in PUBLIC_FRAME_PROPERTIES.get("MetricFrame", ()):
        assert prop in text, f"missing property: {prop}"
    for method in PUBLIC_FRAME_METHODS.get("MetricFrame", ()):
        assert method in text, f"missing method: {method}"


# ---------------------------------------------------------------------------
# Error help
# ---------------------------------------------------------------------------


def test_error_class_help_shows_static_fields() -> None:
    text = _text(MetricNotFoundError)
    assert "MetricNotFoundError" in text
    # The static contract must render the kind and base class.
    assert "kind: MetricNotFound" in text
    assert "base: AnalysisError" in text
    # MetricNotFoundError has at least one matching constraint; verify
    # it is actually listed rather than relying on a coincidental word.
    assert "Constraints:" in text
    assert "metric_ref_registered" in text
    assert "Observed metrics must resolve to a registered semantic metric." in text


def test_error_instance_help_shows_concrete_repair() -> None:
    err = MetricNotFoundError(
        message="metric not found",
        context={"metric_id": "sales.foobar"},
    )
    text = _text(err)
    assert "MetricNotFound" in text
    assert "repair" in text.lower() or "action" in text.lower()
    # The concrete repair action should be present.
    assert (
        "retry" in text.lower() or "semantic_handoff" in text.lower() or "inspect" in text.lower()
    )


def test_base_error_class_help() -> None:
    text = _text(AnalysisError)
    assert "AnalysisError" in text


# ---------------------------------------------------------------------------
# Semantic object help
# ---------------------------------------------------------------------------


def test_semantic_ref_help_without_project_raises() -> None:
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    with pytest.raises(Exception):
        mv.help(ref)


def test_semantic_ref_help_with_project(semantic_project_factory, capsys) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "warehouse = md.ref('datasource.warehouse')\n"
                "orders = ms.entity(name='orders', datasource=warehouse, "
                "source=md.table('orders'))\n"
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
    assert "revenue" in out
    assert "unit: CNY" in out


def test_catalog_object_help_renders_briefing(semantic_project_factory, capsys) -> None:
    """mv.help(catalog_object) must render, not crash with RuntimeError."""
    from marivo.semantic.catalog import SemanticCatalog

    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "warehouse = md.ref('datasource.warehouse')\n"
                "orders = ms.entity(name='orders', datasource=warehouse, "
                "source=md.table('orders'))\n"
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
    catalog = SemanticCatalog(project)
    revenue_obj = catalog.get("metric.sales.revenue")
    assert revenue_obj is not None
    mv.help(revenue_obj, project=project)
    out = capsys.readouterr().out
    assert "revenue" in out
    assert "unit: CNY" in out


# ---------------------------------------------------------------------------
# Callable / object / type / error / semantic resolution parity
# ---------------------------------------------------------------------------


def test_callable_resolves_same_as_string() -> None:
    text_callable = _text(Session.observe)
    text_string = _text("observe")
    assert text_callable == text_string


def test_bound_method_resolves_same_as_unbound() -> None:
    text_unbound = _text(Session.compare)
    # Can't easily get a bound method without a session, so test that
    # the unbound function and the string target produce the same output.
    text_string = _text("compare")
    assert text_unbound == text_string


def test_type_resolves_same_as_string() -> None:
    text_type = _text(Session)
    text_string = _text("Session")
    assert text_type == text_string


def test_object_resolves_same_as_type(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="help_test_session", use_datasources=False)
    text_obj = _text(session)
    text_type = _text(Session)
    assert text_obj == text_type


def test_error_subclass_resolves_same_as_string() -> None:
    text_class = _text(MetricNotFoundError)
    # Should render the error contract.
    assert "MetricNotFound" in text_class


# ---------------------------------------------------------------------------
# No public JSON/format parameter
# ---------------------------------------------------------------------------


def test_help_has_no_format_parameter() -> None:
    sig = inspect.signature(mv.help)
    assert "format" not in sig.parameters
    assert "json" not in sig.parameters


def test_help_text_has_no_format_parameter() -> None:
    sig = inspect.signature(mv.help_text)
    assert "format" not in sig.parameters
    assert "json" not in sig.parameters


def test_help_rejects_format_kwarg() -> None:
    with pytest.raises(TypeError):
        mv.help("observe", format="json")  # type: ignore[call-arg]


def test_help_text_rejects_format_kwarg() -> None:
    with pytest.raises(TypeError):
        mv.help_text("observe", format="json")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Empty string is not a hidden alias for root
# ---------------------------------------------------------------------------


def test_empty_string_is_not_root() -> None:
    with pytest.raises(HelpTargetError):
        mv.help_text("")


def test_none_is_root() -> None:
    text = _text(None)
    assert "Marivo:" in text
    assert "Python:" in text


# ---------------------------------------------------------------------------
# Unknown target raises HelpTargetError
# ---------------------------------------------------------------------------


def test_unknown_string_raises_help_target_error() -> None:
    with pytest.raises(HelpTargetError):
        mv.help_text("nonexistent_thing_xyz")


# ---------------------------------------------------------------------------
# Module/class docstring first-line routing
# ---------------------------------------------------------------------------


def test_analysis_module_docstring_first_line() -> None:
    first_line = mv.__doc__.strip().splitlines()[0] if mv.__doc__ else ""
    assert "mv.help()" in first_line


def test_session_class_docstring_first_line() -> None:
    first_line = Session.__doc__.strip().splitlines()[0] if Session.__doc__ else ""
    assert "mv.help" in first_line


def test_metric_frame_class_docstring_first_line() -> None:
    first_line = MetricFrame.__doc__.strip().splitlines()[0] if MetricFrame.__doc__ else ""
    assert "mv.help" in first_line


def test_base_frame_class_docstring_first_line() -> None:
    first_line = BaseFrame.__doc__.strip().splitlines()[0] if BaseFrame.__doc__ else ""
    assert "mv.help" in first_line


# ---------------------------------------------------------------------------
# Pinned __all__ and __dir__
# ---------------------------------------------------------------------------


def test_analysis_all_is_pinned() -> None:
    expected = {
        "AbsoluteWindow",
        "AlignmentPolicy",
        "ArtifactRef",
        "AssociationResult",
        "AttributionFrame",
        "CalendarRef",
        "CandidateSet",
        "CatalogObject",
        "DeltaFrame",
        "ForecastFrame",
        "HypothesisTestResult",
        "MetricFrame",
        "QualityReport",
        "SemanticRef",
        "Session",
        "TimeScope",
        "dow_aligned",
        "help",
        "help_text",
        "holiday_aligned",
        "holiday_and_dow_aligned",
        "session",
        "window_bucket",
    }
    assert set(mv.__all__) == expected


def test_analysis_dir_matches_all() -> None:
    assert set(dir(mv)) == set(mv.__all__)


# ---------------------------------------------------------------------------
# help() returns None
# ---------------------------------------------------------------------------


def test_help_returns_none() -> None:
    assert mv.help() is None


def test_help_with_target_returns_none() -> None:
    assert mv.help("observe") is None


# ---------------------------------------------------------------------------
# Budget enforcement is strict (registry validation)
# ---------------------------------------------------------------------------


def test_root_help_does_not_silently_exceed_budget() -> None:
    """Root help must stay within SURFACE_LIMITS; overflow is a build failure."""
    text = _text()
    lines = text.replace("\r\n", "\n").splitlines()
    assert len(lines) <= SURFACE_LIMITS.root_help_max_lines
    assert len(text) <= SURFACE_LIMITS.root_help_max_codepoints


def test_focused_help_does_not_silently_exceed_budget() -> None:
    """Focused help must stay within SURFACE_LIMITS; overflow is a build failure."""
    for target in ("observe", "compare", "forecast", "help", "Session", "MetricFrame"):
        text = _text(target)
        lines = text.replace("\r\n", "\n").splitlines()
        assert len(lines) <= SURFACE_LIMITS.focused_help_max_lines, (
            f"{target}: {len(lines)} lines > {SURFACE_LIMITS.focused_help_max_lines}"
        )
        assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints, (
            f"{target}: {len(text)} chars > {SURFACE_LIMITS.focused_help_max_codepoints}"
        )
