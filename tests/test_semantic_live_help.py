"""Semantic live-help target and render contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._authoring.model import AuthoringRepair
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget
from marivo.semantic.errors import SemanticHelpTargetError, SemanticLoadError


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


@pytest.mark.parametrize(
    "target",
    ("preview", "catalog.preview", "SemanticCatalog.preview", "ms.SemanticCatalog.preview"),
)
def test_registered_preview_string_paths_resolve_to_one_descriptor(target: str) -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    resolved = resolve_live_target(target, SEMANTIC_LIVE_SURFACE)
    assert resolved.kind == "descriptor"
    assert resolved.canonical_id == "preview"
    assert ms.help_text(target).startswith("preview\n")


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


def test_root_and_ref_help_teach_one_entry_to_ref_handoff() -> None:
    root = ms.help_text()
    focused = ms.help_text(ms.Ref)

    assert "CatalogEntry" in root
    assert "entry.ref" in root
    assert "ms.ref.<kind>(path)" in root
    assert "entry = catalog.require(ms.ref.metric('sales.revenue'))" in focused
    assert "metric_ref = entry.ref" in focused
    assert "ms.bind(field_ref, entity_alias)" in focused
    assert "bind" in root

    factory = ms.help_text(ms.ref)
    assert factory.startswith("ref\n")
    assert "ms.ref.<kind>(path)" in factory
    assert ms.help_text("ref") == factory

    bind = ms.help_text(ms.bind)
    assert "ms.bind(amount, orders)" in bind


def test_help_resolves_error_type_target() -> None:
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


def test_help_rejects_private_callable_owner_string() -> None:
    with pytest.raises(SemanticHelpTargetError):
        ms.help_text("_authoring_declarations.metric")


def test_ref_help_resolves_to_object_near_reference_briefing() -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    ref = ms.ref.metric("sales.revenue")
    resolved = resolve_live_target(ref, SEMANTIC_LIVE_SURFACE)

    assert resolved.kind == "reference_briefing"
    assert resolved.reference_id == "sales.revenue"
    text = ms.help_text(ref)
    assert "Kind: metric" in text
    assert "Path: sales.revenue" in text
    assert "entry = catalog.require(ref)" in text
    assert "entry.contract().show()" in text
    assert "observe" not in text
    assert "preview" not in text


def test_loaded_entry_help_is_reference_briefing_without_runtime_effects(
    authoring_evidence_project: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    catalog = ms.load()
    entry = catalog.require(ms.ref.metric("sales.revenue"))

    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("reference help must not load or query")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)

    resolved = resolve_live_target(entry, SEMANTIC_LIVE_SURFACE)
    assert resolved.kind == "reference_briefing"
    assert resolved.reference_id == "sales.revenue"
    text = ms.help_text(entry)
    assert "Object: MetricEntry" in text
    assert "Kind: metric" in text
    assert "Path: sales.revenue" in text
    assert "entry.details().show()" in text
    assert "entry.contract().show()" in text
    assert "observe" not in text
    assert "preview" not in text


def test_error_help_kind_depends_on_concrete_repair_target() -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    with_repair = SemanticLoadError(
        kind="invalid_project",
        message="semantic project is invalid",
        expected="one loaded domain",
        received="no domains",
        location_label="semantic project",
        repair=AuthoringRepair(
            kind="retry",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
            action="Inspect the analysis input contract.",
            snippet='mv.help("observe")',
            candidates=("observe",),
        ),
    )
    without_repair = SemanticLoadError(
        kind="invalid_project",
        message="semantic project is invalid",
    )

    briefing = resolve_live_target(with_repair, SEMANTIC_LIVE_SURFACE)
    contract = resolve_live_target(without_repair, SEMANTIC_LIVE_SURFACE)
    error_class = resolve_live_target(SemanticLoadError, SEMANTIC_LIVE_SURFACE)

    assert briefing.kind == "error_briefing"
    assert contract.kind == "error_contract"
    assert error_class.kind == "error_contract"
    assert contract == error_class
    assert ms.help_text(without_repair) == ms.help_text(SemanticLoadError)
    text = ms.help_text(with_repair)
    assert "Kind: retry" in text
    assert "Expected: one loaded domain" in text
    assert "Received: no domains" in text
    assert "Location: semantic project" in text
    assert 'Next help: mv.help("observe")' in text
    assert 'mv.help("observe")' in text
    assert "Candidates: observe" in text


def test_live_help_performs_no_runtime_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("help must not perform runtime effects")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)

    assert ms.help_text()
    for target in ("load", ms.load, ms.SemanticCatalog):
        assert ms.help_text(target)  # type: ignore[arg-type]
