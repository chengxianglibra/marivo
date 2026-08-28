"""Phase 5 contracts for the single public ``marivo.help`` surface."""

from __future__ import annotations

import importlib.util
import inspect
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import get_args, get_type_hints

import pytest

import marivo
import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._help import render as help_render
from marivo._help.model import (
    GLOBAL_HELP_RENDER_BUDGETS,
    GlobalHelpRenderBudget,
    MarivoHelpSurfaceError,
    MarivoHelpTargetError,
    NativeHelpRoute,
    SurfaceRootHelpRoute,
    TopicHelpRoute,
)
from marivo._help.render import PublicHelpTarget, render_help_text
from marivo._help.route import _resolve_one, route_help_target
from marivo.analysis._capabilities.model import (
    ARTIFACT_FAMILIES,
    AnalysisArtifactFamilyContract,
)
from marivo.analysis._capabilities.registry import REGISTRY as ANALYSIS_REGISTRY
from marivo.analysis._capabilities.surface import ANALYSIS_LIVE_SURFACE
from marivo.analysis.constraints import iter_constraints
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.datasource._capabilities.registry import REGISTRY as DATASOURCE_REGISTRY
from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
from marivo.introspection.live.model import SURFACE_LIMITS, HelpSurface, LiveHelpTarget
from marivo.ontology._capabilities.registry import REGISTRY as ONTOLOGY_REGISTRY
from marivo.ontology._capabilities.surface import ONTOLOGY_LIVE_SURFACE
from marivo.semantic._capabilities.registry import REGISTRY as SEMANTIC_REGISTRY
from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

_REGISTRIES = {
    "datasource": DATASOURCE_REGISTRY,
    "semantic": SEMANTIC_REGISTRY,
    "analysis": ANALYSIS_REGISTRY,
    "ontology": ONTOLOGY_REGISTRY,
}
_SURFACES = {
    "datasource": DATASOURCE_LIVE_SURFACE,
    "semantic": SEMANTIC_LIVE_SURFACE,
    "analysis": ANALYSIS_LIVE_SURFACE,
    "ontology": ONTOLOGY_LIVE_SURFACE,
}
_SURFACE_NAMES: tuple[HelpSurface, ...] = (
    "datasource",
    "semantic",
    "analysis",
    "ontology",
)
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SLICE3_ACTIVE_GUIDANCE = (
    _REPO_ROOT / "marivo" / "skills" / "marivo-analysis" / "SKILL.md",
    _REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "docs"
    / "latest"
    / "concepts"
    / "analysis-workflow.mdx",
    _REPO_ROOT
    / "site"
    / "src"
    / "content"
    / "docs"
    / "zh-cn"
    / "docs"
    / "latest"
    / "concepts"
    / "analysis-workflow.mdx",
)


def _text(target: PublicHelpTarget = None) -> str:
    return render_help_text(target)[0]


def test_public_help_wraps_unexpected_surface_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_target: PublicHelpTarget = None) -> tuple[str, str, str | None]:
        raise RuntimeError("synthetic renderer failure")

    monkeypatch.setattr(help_render, "render_help_text", fail)

    with pytest.raises(MarivoHelpSurfaceError) as captured:
        marivo.help("datasource.duckdb")

    error = captured.value
    assert error.received == "datasource.duckdb"
    assert error.stage == "route_or_render"
    assert error.cause_type == "RuntimeError"
    assert "doctor" in str(error)
    assert "installed package source" in str(error)


def test_public_help_preserves_unknown_target_error() -> None:
    with pytest.raises(MarivoHelpTargetError) as captured:
        marivo.help("not-a-registered-target")

    assert "Use marivo.help() to choose authoring or analysis" in str(captured.value)


def test_public_help_routes_core_targets_in_a_cold_start_process() -> None:
    code = (
        "import marivo; "
        "marivo.help(); "
        "marivo.help('authoring'); "
        "marivo.help('datasource.authoring')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "marivo.help" in completed.stdout
    assert "Choose inspection, optional scoped sampling, or governed raw SQL" in completed.stdout


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


@pytest.mark.parametrize("target", ("load", "targets"))
def test_removed_flat_global_topics_do_not_resolve(target: str) -> None:
    with pytest.raises(MarivoHelpTargetError):
        marivo.help(target)


def test_domain_help_attribute_raises_guiding_error() -> None:
    """mv.help raises a friendly AttributeError that points to marivo.help(...)."""
    with pytest.raises(AttributeError, match=r"marivo\.help"):
        mv.help("analysis.observe")  # type: ignore[attr-defined]


def test_root_help_introduces_marivo_and_routes_to_two_secondary_roots() -> None:
    text = _text()

    assert text.startswith("Marivo\n")
    assert "pure Python library for governed, auditable analysis" in text
    assert "Datasource -> physical connections and source evidence" in text
    assert "Semantic   -> governed business objects and stable refs" in text
    assert "Analysis   -> typed artifacts, findings, evidence, and lineage" in text
    assert 'marivo.help("authoring")' in text
    assert 'marivo.help("analysis")' in text
    assert 'marivo.help("load")' not in text
    assert 'marivo.help("targets")' not in text
    assert "Domain modules expose no public .help alias" in text


def test_global_help_topics_use_coordinator_owned_render_budgets() -> None:
    expected = {
        "root": (32, 3_000, 10, 0),
        "decision_hub": (40, 4_000, 8, 0),
    }
    assert {
        render_class: (
            budget.max_lines,
            budget.max_codepoints,
            budget.max_outgoing_routes,
            budget.max_examples_or_snippets,
        )
        for render_class, budget in GLOBAL_HELP_RENDER_BUDGETS.items()
    } == expected
    with pytest.raises(TypeError):
        GLOBAL_HELP_RENDER_BUDGETS["root"] = GlobalHelpRenderBudget(1, 1, 1, 0)  # type: ignore[index]

    for target, render_class in ((None, "root"), ("authoring", "decision_hub")):
        text = _text(target)
        budget = GLOBAL_HELP_RENDER_BUDGETS[render_class]  # type: ignore[index]
        assert len(text.splitlines()) <= budget.max_lines
        assert len(text) <= budget.max_codepoints


def test_root_help_advertises_only_the_two_resolvable_secondary_roots() -> None:
    text = _text()
    advertised = tuple(
        line.strip() for line in text.splitlines() if line.strip().startswith("marivo.help(")
    )

    assert advertised == ('marivo.help("authoring")', 'marivo.help("analysis")')
    assert route_help_target("authoring") == TopicHelpRoute("authoring")
    assert route_help_target("analysis") == SurfaceRootHelpRoute("analysis")


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


def test_unregistered_multi_owner_target_never_uses_surface_order() -> None:
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


def test_global_authoring_composition_topic_wins_over_native_duplicates() -> None:
    route = route_help_target("authoring")
    assert route == TopicHelpRoute("authoring")
    text = _text("authoring")
    assert "datasource.authoring" in text
    assert "semantic.authoring" in text


@pytest.mark.parametrize(
    "target",
    ("semantic.objects", "semantic.builders", "semantic.checks"),
)
def test_semantic_slice3_navigation_targets_are_publicly_active(target: str) -> None:
    route = route_help_target(target)
    assert isinstance(route, NativeHelpRoute)
    assert route.owner == "semantic"


def test_global_authoring_routes_to_surface_owned_decision_hubs() -> None:
    text = _text("authoring")

    assert "Physical source definitions and evidence" in text
    assert "Executable reusable business semantics" in text
    assert "Optional non-executable contextual relations" in text
    assert "marivo.help(result_or_error)" in text
    assert "does not establish" in text
    for owner in ("datasource", "semantic", "ontology"):
        assert f'marivo.help("{owner}.authoring")' in text
        assert f'marivo.help("{owner}")' in text


def test_every_native_discovery_target_resolves_from_its_secondary_tree() -> None:
    for owner in _SURFACE_NAMES:
        for target in _SURFACES[owner].registry.discovery_ids():
            route = route_help_target(f"{owner}.{target}")
            assert isinstance(route, NativeHelpRoute)
            assert route.owner == owner


@pytest.mark.parametrize(
    "target",
    (
        "analysis.evidence",
        "analysis.evidence.browse",
        "analysis.evidence.exact",
        "analysis.runtime",
        "analysis.runtime.sessions",
        "analysis.runtime.artifacts",
        "analysis.runtime.jobs",
    ),
)
def test_slice3_qualified_navigation_targets_resolve(target: str) -> None:
    route = route_help_target(target)
    assert isinstance(route, NativeHelpRoute)
    assert route.owner == "analysis"


def test_slice3_active_guidance_uses_live_canonical_recovery_targets() -> None:
    targets = (
        "analysis.runtime",
        "analysis.runtime.artifacts",
        "analysis.evidence",
    )

    for path in _SLICE3_ACTIVE_GUIDANCE:
        text = path.read_text()
        assert 'marivo.help("analysis.recovery")' not in text
        for target in targets:
            assert f'marivo.help("{target}")' in text

    for target in targets:
        route = route_help_target(target)
        assert isinstance(route, NativeHelpRoute)
        assert route.owner == "analysis"
        canonical_id = target.removeprefix("analysis.")
        assert route.resolved.descriptor is ANALYSIS_REGISTRY.by_canonical_id(canonical_id)


def test_slice5_active_guidance_uses_the_same_progressive_topology_in_both_locales() -> None:
    targets = (
        "analysis.entry",
        "analysis.methods",
        "analysis.inputs",
        "analysis.artifacts",
        "analysis.evidence",
        "analysis.runtime",
        "analysis.boundary.to_pandas",
    )

    for path in _SLICE3_ACTIVE_GUIDANCE:
        text = path.read_text()
        for target in targets:
            assert target in text


def test_slice3_bounded_target_projections_resolve_independently() -> None:
    for owner in (
        "evidence",
        "evidence.browse",
        "evidence.exact",
        "runtime",
        "runtime.sessions",
        "runtime.artifacts",
        "runtime.jobs",
    ):
        topic = ANALYSIS_REGISTRY.navigation_topic(owner)
        projection = tuple(dict.fromkeys((*topic.members, *ANALYSIS_REGISTRY.cross_links(owner))))
        budget = ANALYSIS_REGISTRY.render_budget(topic.render_class)
        assert len(projection) <= budget.max_outgoing_routes
        for target in projection:
            assert target.canonical_id is not None
            route = route_help_target(f"{target.surface}.{target.canonical_id}")
            assert isinstance(route, NativeHelpRoute)


@pytest.mark.parametrize(
    ("target", "replacement"),
    (
        ("analysis.recovery", "analysis.runtime.artifacts"),
        ("analysis.session", "analysis.runtime.sessions"),
        ("analysis.boundary", "analysis.boundary.to_pandas"),
        ("analysis.sampling", "analysis.SamplingPolicy"),
    ),
)
def test_slice3_removed_qualified_navigation_targets_do_not_resolve(
    target: str,
    replacement: str,
) -> None:
    with pytest.raises(MarivoHelpTargetError) as captured:
        route_help_target(target)
    assert replacement in captured.value.candidates


def test_every_analysis_constraint_help_target_resolves() -> None:
    forbidden = {"help", "datasources", "recovery", "session", "boundary", "sampling"}

    for constraint in iter_constraints():
        target = constraint.help_target
        if target is None:
            continue
        assert target not in forbidden
        route = route_help_target(f"analysis.{target}")
        assert isinstance(route, NativeHelpRoute)
        assert route.owner == "analysis"


def test_default_analysis_error_repairs_resolve_on_their_declared_surface() -> None:
    for error_type in dict.fromkeys(ANALYSIS_LIVE_SURFACE.error_types.values()):
        if "message" not in inspect.signature(error_type).parameters:
            continue
        error = error_type(message="repair resolution audit")
        if error.repair is None:
            continue
        target = error.repair.help_target
        qualified = target.surface
        if target.canonical_id is not None:
            qualified = f"{qualified}.{target.canonical_id}"
        route_help_target(qualified)


def test_type_and_error_names_remain_exactly_resolvable() -> None:
    for owner in _SURFACE_NAMES:
        surface = _SURFACES[owner]
        for type_name in dict.fromkeys(surface.type_index.values()):
            route = route_help_target(f"{owner}.{type_name}")
            assert isinstance(route, NativeHelpRoute)
            if owner == "analysis" and type_name in ARTIFACT_FAMILIES:
                assert route.resolved.kind == "descriptor"
                assert isinstance(
                    route.resolved.descriptor,
                    AnalysisArtifactFamilyContract,
                )
                assert route.resolved.descriptor.type_name == type_name
            elif owner == "semantic" and type_name == "ref":
                assert route.resolved.kind == "descriptor"
                assert route.resolved.descriptor is _REGISTRIES[owner].by_canonical_id("ref")
            else:
                assert route.resolved.kind == "type_contract"
                assert route.resolved.type_name == type_name
        for error_type in dict.fromkeys(surface.error_types.values()):
            route = route_help_target(f"{owner}.{error_type.__name__}")
            assert isinstance(route, NativeHelpRoute)
            assert route.resolved.kind == "error_contract"
            assert route.resolved.error_name == error_type.__name__


def test_receiver_members_and_grouped_leaves_remain_exactly_resolvable() -> None:
    for target in (
        "datasource.SourceInspection.sample",
        "semantic.readiness",
        "analysis.transform.filter",
        "analysis.session.evidence.trace",
        "analysis.session.get_frame",
        "analysis.session.revalidate",
        "analysis.boundary.to_pandas",
        "analysis.MetricFrame.as_time_series",
    ):
        assert isinstance(route_help_target(target), NativeHelpRoute)


def test_model_state_handle_has_one_semantic_help_owner() -> None:
    handle = ms.ModelStateHandle(
        model=ms.ref.state_model("sales.order_lifecycle"),
        name="paid",
    )

    semantic_text = _text("semantic.ModelStateHandle")
    assert _text(ms.ModelStateHandle) == semantic_text
    assert _text(handle) == semantic_text

    with pytest.raises(MarivoHelpTargetError) as captured:
        route_help_target("analysis.ModelStateHandle")

    assert captured.value.outcome == "unknown"
    assert captured.value.candidates[0] == "semantic.ModelStateHandle"


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


@pytest.mark.parametrize(
    ("entry_type_name", "method_name", "target"),
    (
        ("PeriodCalendarEntry", "grain", "calendar.grain"),
        ("PeriodCalendarEntry", "period_on", "calendar.period_on"),
        ("PeriodCalendarEntry", "periods", "calendar.periods"),
        ("TemporalSetEntry", "occurrence", "temporal_set.occurrence"),
        ("TemporalSetEntry", "occurrences", "temporal_set.occurrences"),
    ),
)
def test_temporal_catalog_member_navigation_resolves_through_public_help(
    entry_type_name: str,
    method_name: str,
    target: str,
) -> None:
    import marivo.semantic.catalog as catalog_module

    method = getattr(getattr(catalog_module, entry_type_name), method_name)
    route = route_help_target(f"analysis.{target}")

    assert isinstance(route, NativeHelpRoute)
    assert route.owner == "analysis"
    assert route.resolved.descriptor is ANALYSIS_REGISTRY.by_canonical_id(target)
    assert _text(method) == _text(f"analysis.{target}")


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
    assert "Object-near inspection:" in text
    assert "entry.show()" in text
    assert "entry.details().show()" in text
    assert 'marivo.help("semantic.preview")' in text
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
    if owner == "ontology":
        from marivo.ontology._capabilities.render import render_root_help

        return render_root_help()
    from marivo.analysis._capabilities.render import render_root_help

    return render_root_help()
