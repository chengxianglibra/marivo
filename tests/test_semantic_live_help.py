"""Semantic live-help target and render contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo._authoring.model import AuthoringRepair
from marivo._help.model import MarivoHelpTargetError
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget
from marivo.semantic.errors import SemanticLoadError
from tests.shared_fixtures import rendered_help


def _text(target: object | None = None) -> str:
    return rendered_help(target, owner="semantic")


def test_root_help_reveals_current_environment() -> None:
    text = _text()
    assert f"Marivo: {marivo.__version__}" in text
    assert f"Python: {sys.executable}" in text
    assert f"Package: {Path(marivo.__file__).resolve()}" in text


def test_root_help_within_line_budget() -> None:
    text = _text()
    assert text.count("\n") + 1 <= SURFACE_LIMITS.root_help_max_lines
    assert len(text) <= SURFACE_LIMITS.root_help_max_codepoints


def test_help_text_none_returns_root() -> None:
    text = _text()
    assert "marivo.semantic" in text
    assert "Capabilities:" in text


def test_empty_string_is_not_a_hidden_root_alias() -> None:
    with pytest.raises(MarivoHelpTargetError):
        _text("")


def test_help_resolves_authoring_topic() -> None:
    text = _text("authoring")
    assert "authoring" in text
    for target in (
        "datasource.authoring",
        "semantic.domain",
        "semantic.entity",
        "semantic.dimension_column",
        "semantic.time_dimension_column",
        "semantic.measure_column",
        "semantic.where",
        "semantic.count",
        "semantic.aggregate",
        "semantic.readiness",
    ):
        assert f'marivo.help("{target}")' in text


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
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    with pytest.raises(Exception):
        resolve_live_target(mv.Session, SEMANTIC_LIVE_SURFACE)


# ---------------------------------------------------------------------------
# Help target matrix — string, callable, type, error type, cross-surface
# rejections, unknown string, private object, no-runtime-effects.
# ---------------------------------------------------------------------------


def test_help_resolves_string_target() -> None:
    text = _text("load")
    assert "load" in text
    assert "catalog = ms.load()" in text
    assert "catalog.show()" in text


def test_help_resolves_callable_target() -> None:
    text = _text(ms.load)
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
    assert _text(target).startswith("preview\n")


def test_where_is_registered_help_target_and_count_teaches_filter() -> None:
    """ms.where is a public primitive and must be a registered help target; count
    and aggregate must teach filter=ms.where(...). See MR !29 review (help).
    """
    where_text = _text("where")
    assert "where" in where_text
    assert "ms.where" in where_text
    assert "tuple/list values mean membership" in where_text
    assert "ms.where(type=(2, 4), query_kind='Select')" in where_text
    assert "filter_condition_valid" in where_text

    count_text = _text("count")
    assert "filter" in count_text.lower()
    assert "ms.where" in count_text

    aggregate_text = _text("aggregate")
    assert "filter" in aggregate_text.lower()
    assert "ms.where" in aggregate_text


def test_help_resolves_type_target() -> None:
    text = _text(ms.SemanticCatalog)
    assert "SemanticCatalog" in text


def test_catalog_collection_help_teaches_displayed_typed_key_lookup() -> None:
    text = _text(ms.CatalogCollection)

    assert "displayed same-kind typed key" in text
    assert "catalog.metrics.get('metric:sales.revenue')" in text
    assert "marivo.help(entry)" in text
    assert "entry or entry.ref" in text
    assert "call .show() for bounded readable state" in text


def test_root_and_ref_help_teach_entry_runtime_and_ref_identity_handoffs() -> None:
    root = _text()
    focused = _text(ms.Ref)

    assert "CatalogEntry" in root
    assert "entry.ref" in root
    assert "pass a current CatalogEntry directly" in root
    assert "ms.ref.<kind>(path)" in root
    assert "entry = catalog.metrics.get('sales.revenue')" in focused
    assert "metric_ref = entry.ref" in focused
    assert "catalog.require(ref) resolves the exact ref" in focused
    assert "marivo.help(ref) reports identity only" in focused
    assert "ms.bind(field_ref, entity_alias)" in focused
    assert "bind" in root

    entry_help = _text(ms.CatalogEntry)
    assert "marivo.help(entry) combines current details" in entry_help

    factory = _text(ms.ref)
    assert factory.startswith("ref\n")
    assert "ms.ref.<kind>(path)" in factory
    assert _text("ref") == factory

    bind = _text(ms.bind)
    assert "ms.bind(amount, orders)" in bind


@pytest.mark.parametrize("target", ["preview", "preview_many", "readiness"])
def test_runtime_help_uses_public_semantic_input_name(target: str) -> None:
    text = _text(target)
    assert "_SemanticInput" not in text
    assert "SemanticInput" in text


def test_help_resolves_error_type_target() -> None:
    text = _text(SemanticLoadError)
    assert "SemanticLoadError" in text


def test_help_rejects_unknown_string() -> None:
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        _text("nonexistent_target")
    assert exc_info.value.outcome == "unknown"


def test_help_rejects_private_object() -> None:
    with pytest.raises(MarivoHelpTargetError):
        _text(object())


def test_help_rejects_private_callable_owner_string() -> None:
    with pytest.raises(MarivoHelpTargetError):
        _text("_authoring_declarations.metric")


def test_ref_help_resolves_to_object_near_reference_briefing() -> None:
    from marivo.introspection.live.resolve import resolve_live_target
    from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

    ref = ms.ref.metric("sales.revenue")
    resolved = resolve_live_target(ref, SEMANTIC_LIVE_SURFACE)

    assert resolved.kind == "reference_briefing"
    assert resolved.reference_id == "sales.revenue"
    text = _text(ref)
    assert "metric: sales.revenue" in text
    assert "entry = catalog.require(ref)" in text
    assert "entry.details().show()" in text
    assert "catalog.readiness(refs=[entry]).show()" in text
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
    text = _text(entry)
    assert "Object: MetricEntry" in text
    assert "metric: sales.revenue" in text
    assert "Details:" in text
    assert "Analysis handoff (kind-level" in text
    assert "session.observe(...) -> MetricFrame" in text
    assert 'marivo.help("analysis.observe")' in text
    assert "result.contract().show()" in text
    assert "Readiness is not inferred here" in text


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
            snippet='marivo.help("analysis.observe")',
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
    assert contract.kind == "error_briefing"
    assert error_class.kind == "error_contract"
    assert contract != error_class
    assert 'marivo.help("semantic.authoring")' in _text(without_repair)
    text = _text(with_repair)
    assert "Kind: retry" in text
    assert "Expected: one loaded domain" in text
    assert "Received: no domains" in text
    assert "Location: semantic project" in text
    assert 'Next help: marivo.help("analysis.observe")' in text
    assert 'marivo.help("analysis.observe")' in text
    assert "Candidates: observe" in text


def test_live_help_performs_no_runtime_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("help must not perform runtime effects")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)

    assert _text()
    for target in ("load", ms.load, ms.SemanticCatalog):
        assert _text(target)
