"""Semantic live-help target and render contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.introspection.live.model import SURFACE_LIMITS
from marivo.semantic.errors import SemanticHelpTargetError


def test_root_help_reveals_current_environment() -> None:
    text = ms.help_text()
    assert f"Marivo: {marivo.__version__}" in text
    assert f"Python: {sys.executable}" in text
    assert f"Package: {Path(marivo.__file__).resolve()}" in text


def test_root_help_within_line_budget() -> None:
    text = ms.help_text()
    assert text.count("\n") + 1 <= SURFACE_LIMITS.root_help_max_lines
    assert len(text) <= SURFACE_LIMITS.root_help_max_codepoints


def test_help_text_none_returns_root() -> None:
    text = ms.help_text()
    assert "marivo.semantic" in text
    assert "Capabilities:" in text


def test_help_text_empty_string_returns_root() -> None:
    text = ms.help_text("")
    assert "marivo.semantic" in text


def test_help_resolves_authoring_topic() -> None:
    text = ms.help_text("authoring")
    assert "authoring" in text


def test_render_root_help_is_bounded_and_has_fingerprint() -> None:
    from marivo.semantic._capabilities.render import render_root_help

    text = render_root_help()
    assert "marivo.semantic" in text
    assert text.count("\n") + 1 <= SURFACE_LIMITS.root_help_max_lines


def test_semantic_live_surface_resolves_registered_callable() -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    resolved = resolve_live_target("authoring", SEMANTIC_LIVE_SURFACE)
    assert resolved.surface == "semantic"


def test_semantic_live_surface_rejects_cross_surface_target() -> None:
    import marivo.analysis as mv
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    with pytest.raises(Exception):
        resolve_live_target(mv.Session, SEMANTIC_LIVE_SURFACE)


# ---------------------------------------------------------------------------
# Help target matrix — string, callable, type, error type, cross-surface
# rejections, unknown string, private object, no-runtime-effects.
# ---------------------------------------------------------------------------


def test_help_resolves_string_target() -> None:
    text = ms.help_text("load")
    assert "load" in text


def test_help_resolves_callable_target() -> None:
    text = ms.help_text(ms.load)
    assert "load" in text


def test_where_is_registered_help_target_and_count_teaches_filter() -> None:
    """ms.where is a public primitive and must be a registered help target; count
    and aggregate must teach filter=ms.where(...). See MR !29 review (help).
    """
    where_text = ms.help_text("where")
    assert "where" in where_text
    assert "ms.where" in where_text

    count_text = ms.help_text("count")
    assert "filter" in count_text.lower()
    assert "ms.where" in count_text

    aggregate_text = ms.help_text("aggregate")
    assert "filter" in aggregate_text.lower()
    assert "ms.where" in aggregate_text


def test_help_resolves_type_target() -> None:
    text = ms.help_text(ms.SemanticCatalog)
    assert "SemanticCatalog" in text


def test_help_resolves_error_type_target() -> None:
    from marivo.semantic.errors import SemanticLoadError

    text = ms.help_text(SemanticLoadError)
    assert "SemanticLoadError" in text


def test_help_rejects_cross_surface_callable() -> None:
    with pytest.raises(SemanticHelpTargetError) as exc_info:
        ms.help_text(md.inspect)
    assert "md.help" in str(exc_info.value)


def test_help_rejects_cross_surface_type() -> None:
    with pytest.raises(SemanticHelpTargetError) as exc_info:
        ms.help_text(mv.Session)
    assert "mv.help" in str(exc_info.value)


def test_help_rejects_unknown_string() -> None:
    with pytest.raises(SemanticHelpTargetError) as exc_info:
        ms.help_text("nonexistent_target")
    assert exc_info.value.repair is not None


def test_analysis_handoff_help_is_not_a_callable_entrypoint() -> None:
    """``analysis_handoff`` is a boundary concept, not a callable capability.

    The help must not advertise a non-existent ``ms.analysis_handoff(...)``
    call. It must state the handoff is not callable and point at the real
    location: ``ReadinessReport.analysis_handoff`` produced by ``readiness``.
    See issue #19.
    """
    text = ms.help_text("analysis_handoff")

    assert "analysis_handoff" in text
    # The handoff data is not reachable as a call on the module or catalog.
    assert "not a callable entrypoint" in text.lower()
    # Point agents at the producing capability and the result field.
    assert "readiness" in text
    assert "ReadinessReport" in text
    assert "analysis_handoff" in text
    # The misleading "Output family: None" block must not be rendered for a
    # boundary concept — it implies a callable producing an output.
    assert "Output family" not in text


def test_help_rejects_private_object() -> None:
    with pytest.raises(SemanticHelpTargetError):
        ms.help_text(object())  # type: ignore[arg-type]


def test_live_help_performs_no_runtime_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("help must not perform runtime effects")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)

    assert ms.help_text()
    for target in ("load", ms.load, ms.SemanticCatalog):
        assert ms.help_text(target)  # type: ignore[arg-type]
