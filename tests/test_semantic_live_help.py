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
    assert f"Python: {Path(sys.executable).resolve()}" in text
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
