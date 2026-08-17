"""Phase 5 contracts for the single public ``marivo.help`` surface."""

from __future__ import annotations

import importlib.util
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import get_args, get_type_hints

import pytest

import marivo
import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._help.model import (
    MarivoHelpTargetError,
    NativeHelpRoute,
    SurfaceRootHelpRoute,
    TopicHelpRoute,
)
from marivo._help.render import PublicHelpTarget, render_help_text
from marivo._help.route import _resolve_one, route_help_target
from marivo.analysis._capabilities.registry import REGISTRY as ANALYSIS_REGISTRY
from marivo.analysis._capabilities.surface import ANALYSIS_LIVE_SURFACE
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.datasource._capabilities.registry import REGISTRY as DATASOURCE_REGISTRY
from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
from marivo.introspection.live.model import SURFACE_LIMITS, HelpSurface, LiveHelpTarget
from marivo.semantic._capabilities.registry import REGISTRY as SEMANTIC_REGISTRY
from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

_REGISTRIES = {
    "datasource": DATASOURCE_REGISTRY,
    "semantic": SEMANTIC_REGISTRY,
    "analysis": ANALYSIS_REGISTRY,
}
_SURFACES = {
    "datasource": DATASOURCE_LIVE_SURFACE,
    "semantic": SEMANTIC_LIVE_SURFACE,
    "analysis": ANALYSIS_LIVE_SURFACE,
}
_SURFACE_NAMES: tuple[HelpSurface, ...] = ("datasource", "semantic", "analysis")


def _text(target: PublicHelpTarget = None) -> str:
    return render_help_text(target)[0]


def test_marivo_help_is_the_only_public_help_callable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert marivo.__all__ == ["__version__", "help"]
    assert callable(marivo.help)
    assert marivo.help() is None
    assert capsys.readouterr().out.strip()
    assert not hasattr(marivo, "help_text")

    for surface in (md, ms, mv):
        assert "help" not in surface.__all__
        assert "help_text" not in surface.__all__
        assert not hasattr(surface, "help")
        assert not hasattr(surface, "help_text")


def test_domain_help_attribute_raises_guiding_error() -> None:
    """mv.help raises a friendly AttributeError that points to marivo.help(...)."""
    with pytest.raises(AttributeError, match=r"marivo\.help"):
        mv.help("analysis.observe")  # type: ignore[attr-defined]


def test_root_help_identifies_coordinator_and_native_content_owners() -> None:
    text = _text()

    assert "one public coordinator" in text
    assert "datasource.* -> marivo.datasource capability registry" in text
    assert "semantic.*   -> marivo.semantic capability registry" in text
    assert "analysis.*   -> marivo.analysis capability registry" in text
    assert "ontology.*   -> marivo.ontology capability registry" in text
    assert "Domain modules expose no public .help alias" in text


def test_root_help_only_advertises_resolvable_datasource_targets() -> None:
    text = _text()

    assert 'marivo.help("datasource.DiscoverySnapshot")' in text
    assert 'marivo.help("datasource.snapshot")' not in text
    assert isinstance(route_help_target("datasource.DiscoverySnapshot"), NativeHelpRoute)


@pytest.mark.parametrize(
    "module_name",
    ("marivo.datasource.help", "marivo.semantic.help", "marivo.analysis.help"),
)
def test_removed_domain_help_modules_cannot_rebind_public_attributes(
    module_name: str,
) -> None:
    assert importlib.util.find_spec(module_name) is None


def test_public_help_annotation_is_one_closed_alias() -> None:
    signature = inspect.signature(marivo.help)
    assert tuple(signature.parameters) == ("target",)
    assert "format" not in signature.parameters
    assert "print" not in signature.parameters
    target_hint = get_type_hints(marivo.help)["target"]
    assert target_hint is PublicHelpTarget or PublicHelpTarget in get_args(target_hint)
    assert Callable[..., object] in get_args(PublicHelpTarget.__value__)


def test_every_qualified_registry_target_preserves_native_descriptor_identity() -> None:
    for owner, registry in _REGISTRIES.items():
        surface = _SURFACES[owner]
        for canonical_id in registry.canonical_ids():
            route = route_help_target(f"{owner}.{canonical_id}")
            assert isinstance(route, NativeHelpRoute)
            assert route.owner == owner
            assert route.resolved.descriptor is surface.registry.by_canonical_id(canonical_id)


@pytest.mark.parametrize("owner", _SURFACE_NAMES)
def test_surface_name_routes_to_the_exact_native_root(owner: HelpSurface) -> None:
    route = route_help_target(owner)
    assert route == SurfaceRootHelpRoute(owner)
    assert _text(owner) == rendered_native_root(owner)


def test_every_unique_unqualified_registry_target_routes_to_its_only_owner() -> None:
    canonical_ids = {
        canonical_id
        for registry in _REGISTRIES.values()
        for canonical_id in registry.canonical_ids()
    }
    for canonical_id in canonical_ids:
        owners = [
            owner for owner in _SURFACE_NAMES if _resolve_one(canonical_id, owner) is not None
        ]
        if len(owners) != 1:
            continue
        route = route_help_target(canonical_id)
        assert isinstance(route, NativeHelpRoute)
        assert route.owner == owners[0]
        assert route.resolved.descriptor is _REGISTRIES[owners[0]].by_canonical_id(canonical_id)


def test_unregistered_multi_owner_target_never_uses_surface_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo._help.route as route_module

    monkeypatch.setattr(route_module, "_GLOBAL_TOPICS", frozenset())
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        route_help_target("load")

    error = exc_info.value
    assert error.outcome == "ambiguous"
    assert error.candidates == (
        "datasource.load",
        "semantic.load",
        "ontology.authoring",
    )
    assert len(error.candidates) <= SURFACE_LIMITS.help_suggestion_limit


@pytest.mark.parametrize("target", ("catalog.require", "catalog.readiness"))
def test_multi_owner_alias_never_prefers_a_canonical_owner(target: str) -> None:
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        route_help_target(target)

    error = exc_info.value
    assert error.outcome == "ambiguous"
    semantic_id = {
        "catalog.require": "SemanticCatalog.require",
        "catalog.readiness": "readiness",
    }[target]
    assert error.candidates == (f"semantic.{semantic_id}", f"analysis.{target}")


@pytest.mark.parametrize("target", ("authoring", "load"))
def test_registered_global_composition_topics_win_over_native_duplicates(target: str) -> None:
    route = route_help_target(target)
    assert route == TopicHelpRoute(target)
    text = _text(target)
    assert f"datasource.{target}" in text
    assert f"semantic.{target}" in text


def test_global_authoring_routes_preflight_and_exact_project_catalog_reads() -> None:
    text = _text("authoring")

    assert "Before data access or capability enumeration" in text
    assert "Ask only the earliest missing accountable input and stop" in text
    assert "Do not bundle owner with an independent business decision" in text
    assert "A user-named build target satisfies target-concept preflight" in text
    assert "datasource_catalog = md.load()" in text
    assert "semantic_catalog = ms.load()" in text


@pytest.mark.parametrize(
    "target",
    (
        "observe",
        "analysis.observe",
        "Session.observe",
        "session.observe",
        mv.Session.observe,
    ),
)
def test_equivalent_observe_targets_render_one_native_descriptor(
    target: str | Callable[..., object],
) -> None:
    assert _text(target) == _text("analysis.observe")


def test_period_calendar_period_navigation_resolves_through_public_help() -> None:
    from marivo.semantic.catalog import PeriodCalendarEntry

    route = route_help_target("analysis.calendar.period")

    assert isinstance(route, NativeHelpRoute)
    assert route.owner == "analysis"
    assert route.resolved.descriptor is ANALYSIS_REGISTRY.by_canonical_id("calendar.period")
    assert 'scope = calendar.period("fiscal_week", "FY2026-W01")' in _text(
        "analysis.calendar.period"
    )
    assert _text(PeriodCalendarEntry.period) == _text("analysis.calendar.period")


def test_bound_method_renders_the_same_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="unified_help", use_datasources=False)
    assert _text(session.observe) == _text("analysis.observe")


def test_unknown_target_raises_one_bounded_global_error() -> None:
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        _text("observv")

    error = exc_info.value
    assert error.outcome == "unknown"
    assert len(error.candidates) <= SURFACE_LIMITS.help_suggestion_limit
    assert all(candidate != "help" and candidate != "help_text" for candidate in error.candidates)


def test_ref_briefing_is_identity_only_and_performs_no_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("Ref help must not load a project, inspect readiness, or query data")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.config.load_project_config", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend_with_secrets", fail)
    monkeypatch.setattr("marivo.semantic.catalog.SemanticCatalog.readiness", fail)

    text = _text(ms.ref.metric("sales.revenue"))
    assert "metric: sales.revenue" in text
    assert "typed identity only" in text
    assert "catalog.require(ref)" in text
    assert "readiness are unknown" in text
    assert "currently legal" not in text


def test_catalog_entry_briefing_uses_loaded_facts_without_datasource_io(
    authoring_evidence_project: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = ms.load()
    entry = catalog.require(ms.ref.metric("sales.revenue"))

    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("CatalogEntry help must not load, query, or infer readiness")

    monkeypatch.setattr("marivo.semantic.reader.SemanticProject.load", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend_with_secrets", fail)
    monkeypatch.setattr("marivo.semantic.catalog.SemanticCatalog.readiness", fail)

    text = _text(entry)
    assert "metric: sales.revenue" in text
    assert "Details:" in text
    assert "Semantic continuation:" in text
    assert "Analysis handoff (kind-level" in text
    assert "session.observe(...) -> MetricFrame" in text
    assert 'marivo.help("analysis.observe")' in text
    assert "result.contract().show()" in text
    assert "Readiness is not inferred here" in text
    assert "No datasource connectivity or inspection evidence was queried" in text


def test_error_instance_uses_qualified_repair_and_repair_free_instance_is_generic() -> None:
    repaired = AnalysisError(
        message="inspect the datasource",
        repair=AnalysisRepair(
            kind="inspect",
            action="Inspect the registered source.",
            help_target=LiveHelpTarget(surface="datasource", canonical_id="inspect"),
        ),
    )
    assert 'marivo.help("datasource.inspect")' in _text(repaired)
    assert _text(AnalysisError(message="no repair")) == _text(AnalysisError)


def test_root_and_all_focused_registry_help_stay_inside_shared_budgets() -> None:
    root = _text()
    assert len(root.splitlines()) <= SURFACE_LIMITS.root_help_max_lines
    assert len(root) <= SURFACE_LIMITS.root_help_max_codepoints

    for owner, registry in _REGISTRIES.items():
        for canonical_id in registry.canonical_ids():
            text = _text(f"{owner}.{canonical_id}")
            assert len(text.splitlines()) <= SURFACE_LIMITS.focused_help_max_lines
            assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints


def test_current_rendered_help_never_points_to_removed_domain_help_paths() -> None:
    forbidden = ("md.help", "ms.help", "mv.help", "help_text", "skill attachment")
    targets = [
        None,
        "authoring",
        "load",
        *(
            f"{owner}.{canonical_id}"
            for owner, registry in _REGISTRIES.items()
            for canonical_id in registry.canonical_ids()
        ),
    ]
    for target in targets:
        text = _text(target)
        for stale in forbidden:
            assert stale not in text, f"{target!r} still renders {stale!r}"


def rendered_native_root(owner: HelpSurface) -> str:
    if owner == "datasource":
        from marivo.datasource._capabilities.render import render_root_help

        return render_root_help()
    if owner == "semantic":
        from marivo.semantic._capabilities.render import render_root_help

        return render_root_help()
    from marivo.analysis._capabilities.render import render_root_help

    return render_root_help()
