"""Contracts for the single public ``marivo.help`` surface."""

from __future__ import annotations

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
from marivo._authoring.model import AuthoringCapability
from marivo._help import render as help_render
from marivo._help.model import (
    MarivoHelpSurfaceError,
    MarivoHelpTargetError,
    NativeHelpRoute,
    SurfaceRootHelpRoute,
    TopicHelpRoute,
)
from marivo._help.render import PublicHelpTarget
from marivo._help.render import help as public_help
from marivo._help.route import route_help_target
from marivo.analysis._capabilities.dataset_model import Descriptor
from marivo.analysis._capabilities.registry import REGISTRY as ANALYSIS_REGISTRY
from marivo.analysis._capabilities.surface import ANALYSIS_LIVE_SURFACE
from marivo.datasource._capabilities.registry import REGISTRY as DATASOURCE_REGISTRY
from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
from marivo.introspection.live.model import (
    SURFACE_LIMITS,
    HelpSurface,
    LiveSurfaceRegistry,
    ResolvableHelpDescriptor,
)
from marivo.introspection.live.resolve import LiveSurface
from marivo.ontology._capabilities.registry import REGISTRY as ONTOLOGY_REGISTRY
from marivo.ontology._capabilities.registry import OntologyDescriptor
from marivo.ontology._capabilities.surface import ONTOLOGY_LIVE_SURFACE
from marivo.semantic._capabilities.model import SemanticHelpDescriptor
from marivo.semantic._capabilities.registry import REGISTRY as SEMANTIC_REGISTRY
from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE
from tests.support.paths import PROJECT_ROOT


def test_default_root_advertises_a_resolvable_canonical_target() -> None:
    text, _, _ = help_render.render_help_text()
    target = text.splitlines()[0].removeprefix("Target: ")
    assert target == "root"
    assert help_render.render_help_text(target)[0] == text


_REGISTRIES: dict[HelpSurface, LiveSurfaceRegistry[ResolvableHelpDescriptor]] = {
    "datasource": DATASOURCE_REGISTRY,
    "semantic": SEMANTIC_REGISTRY,
    "analysis": ANALYSIS_REGISTRY,
    "ontology": ONTOLOGY_REGISTRY,
}
_SURFACES: dict[
    HelpSurface,
    LiveSurface[AuthoringCapability]
    | LiveSurface[SemanticHelpDescriptor]
    | LiveSurface[Descriptor]
    | LiveSurface[OntologyDescriptor],
] = {
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


assert marivo.help is public_help


def test_public_help_wraps_unexpected_surface_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_target: PublicHelpTarget = None) -> tuple[str, str, str | None]:
        raise RuntimeError("synthetic renderer failure")

    monkeypatch.setattr(help_render, "render_help_text", fail)

    with pytest.raises(MarivoHelpSurfaceError) as captured:
        public_help("datasource.duckdb")

    error = captured.value
    assert error.received == "datasource.duckdb"
    assert error.stage == "route_or_render"
    assert error.cause_type == "RuntimeError"


def test_public_help_routes_core_targets_in_a_cold_start_process() -> None:
    code = (
        "import marivo; "
        "marivo.help(); "
        "marivo.help('authoring'); "
        "marivo.help('datasource.authoring')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_marivo_help_is_the_only_public_help_callable() -> None:
    assert marivo.__all__ == ["__version__", "help"]
    assert callable(marivo.help)
    assert public_help() is None  # type: ignore[func-returns-value]  # Runtime oracle pins the public no-value return.

    for surface in (md, ms, mv):
        assert "help" not in surface.__all__
        assert not hasattr(surface, "help")


def test_public_help_annotation_is_one_closed_alias() -> None:
    signature = inspect.signature(public_help)
    assert tuple(signature.parameters) == ("target",)
    assert "format" not in signature.parameters
    assert "print" not in signature.parameters
    target_hint = get_type_hints(public_help)["target"]
    assert target_hint is PublicHelpTarget or PublicHelpTarget in get_args(target_hint)
    assert Callable[..., object] in get_args(PublicHelpTarget.__value__)


def test_every_qualified_registry_target_preserves_native_descriptor_identity() -> None:
    for owner, registry in _REGISTRIES.items():
        surface = _SURFACES[owner]
        for canonical_id in registry.canonical_ids():
            if not canonical_id:
                continue
            route = route_help_target(f"{owner}.{canonical_id}")
            assert isinstance(route, NativeHelpRoute)
            assert route.owner == owner
            assert route.resolved.descriptor is surface.registry.by_canonical_id(canonical_id)


@pytest.mark.parametrize("owner", _SURFACE_NAMES)
def test_surface_name_routes_to_the_exact_native_root(owner: HelpSurface) -> None:
    route = route_help_target(owner)
    assert route == SurfaceRootHelpRoute(owner)


@pytest.mark.parametrize(
    "method_name, canonical_target",
    [("describe", "semantic_catalog.describe")],
)
def test_shared_catalog_callable_uses_its_native_semantic_contract(
    authoring_evidence_project: Path,
    method_name: str,
    canonical_target: str,
) -> None:
    from marivo.semantic.catalog import SemanticCatalog

    catalog = ms.load()
    expected = help_render.render_help_text(f"semantic.{canonical_target}")
    for method in (getattr(SemanticCatalog, method_name), getattr(catalog, method_name)):
        route = route_help_target(method)
        assert isinstance(route, NativeHelpRoute)
        assert route.owner == "semantic"
        assert route.resolved.descriptor is SEMANTIC_REGISTRY.by_canonical_id(canonical_target)
        assert help_render.render_help_text(method) == expected


def test_global_authoring_composition_topic_wins_over_native_duplicates() -> None:
    route = route_help_target("authoring")
    assert route == TopicHelpRoute("authoring")


@pytest.mark.parametrize(
    "target",
    ("semantic.objects", "semantic.builders", "semantic.checks"),
)
def test_semantic_slice3_navigation_targets_are_publicly_active(target: str) -> None:
    route = route_help_target(target)
    assert isinstance(route, NativeHelpRoute)
    assert route.owner == "semantic"


def test_every_native_discovery_target_resolves_from_its_secondary_tree() -> None:
    for owner in _SURFACE_NAMES:
        for target in _SURFACES[owner].registry.discovery_ids():
            if not target:
                continue
            route = route_help_target(f"{owner}.{target}")
            assert isinstance(route, NativeHelpRoute)
            assert route.owner == owner


@pytest.mark.parametrize(
    "target",
    (
        "analysis.evidence",
        "analysis.runtime",
        "analysis.runtime.sessions",
        "analysis.runtime.runs",
    ),
)
def test_slice3_qualified_navigation_targets_resolve(target: str) -> None:
    route = route_help_target(target)
    assert isinstance(route, NativeHelpRoute)
    assert route.owner == "analysis"


def test_slice3_bounded_target_projections_resolve_independently() -> None:
    for owner in (
        "evidence",
        "runtime",
        "runtime.sessions",
        "runtime.runs",
    ):
        from marivo.analysis._capabilities.dataset_model import NavigationInput

        topic = ANALYSIS_REGISTRY.by_canonical_id(owner)
        assert isinstance(topic, NavigationInput)
        assert len(topic.members) <= 16
        for target in topic.members:
            route = route_help_target(f"analysis.{target}")
            assert isinstance(route, NativeHelpRoute)


def test_default_analysis_error_repairs_resolve_on_their_declared_surface() -> None:
    for error_type in dict.fromkeys(ANALYSIS_LIVE_SURFACE.error_types.values()):
        if "message" not in inspect.signature(error_type).parameters:
            continue
        if any(
            p.default is inspect.Parameter.empty
            and p.name != "message"
            and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            for p in inspect.signature(error_type).parameters.values()
        ):
            continue
        error = error_type(message="repair resolution audit")
        if error.repair is None:
            continue
        target = error.repair.help_target
        qualified = target.surface
        if target.canonical_id is not None:
            qualified = f"{qualified}.{target.canonical_id}"
        route_help_target(qualified)


def test_bound_method_renders_the_same_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.create(name="unified_help")
    from marivo.introspection.live.resolve import resolve_live_target

    resolved = resolve_live_target(session.members, ANALYSIS_LIVE_SURFACE)
    assert resolved.descriptor is ANALYSIS_REGISTRY.by_canonical_id("session.members")


def test_unknown_target_raises_one_bounded_global_error() -> None:
    with pytest.raises(MarivoHelpTargetError) as exc_info:
        public_help("not-a-registered-target")

    error = exc_info.value
    assert error.outcome == "unknown"
    assert error.received == "not-a-registered-target"
    assert len(error.candidates) <= SURFACE_LIMITS.help_suggestion_limit


@pytest.mark.parametrize(
    "target",
    [
        "Query",
        "analysis.Query",
        "mv.Query",
        " query",
        "session.get_or_create",
        "semantic.semantic_catalog.require",
        "analysis.catalog.require",
    ],
)
def test_legacy_string_aliases_are_rejected(target: str) -> None:
    with pytest.raises(MarivoHelpTargetError):
        public_help(target)


@pytest.mark.parametrize("value", [object(), ValueError("failure"), ms.ref.metric("sales.revenue")])
def test_instances_do_not_enter_static_help(value: object) -> None:
    with pytest.raises(MarivoHelpTargetError):
        route_help_target(value)


def test_attribute_discovery_does_not_execute_getters(monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo._help import route as routing
    from marivo.introspection.live.attributes import public_attributes

    class Example:
        field: int

        @property
        def value(self) -> str:
            """A value requiring explicit reading."""
            raise AssertionError("Help evaluated a getter")

    surface = LiveSurface(
        registry=ANALYSIS_REGISTRY,
        type_index={Example: "Example"},
        error_types={},
        error_base=Exception,
    )
    monkeypatch.setattr(routing, "_native_surface", lambda _owner: surface)
    assert set(public_attributes(Example)) == {"field", "value"}
    for member in ("field", "value"):
        text, _, _ = help_render.render_help_text(f"analysis.example.{member}")
        assert f"Target: analysis.example.{member}" in text
        assert "Kind: attribute" in text
        assert f"example.{member}" in text


def test_attribute_target_is_independent_and_typed() -> None:
    text, _, _ = help_render.render_help_text("analysis.analysis_frame.output_names")
    assert "Kind: attribute" in text
    assert "tuple[str, ...]" in text
    assert "read-only" in text


def test_every_public_attribute_has_an_independent_target() -> None:
    from marivo.introspection.live.attributes import public_attributes
    from marivo.introspection.live.targets import canonical_target

    for owner, surface in _SURFACES.items():
        for type_obj, type_name in surface.type_index.items():
            for member in public_attributes(type_obj):
                target = f"{owner}.{canonical_target(type_name)}.{member}"
                text, _, _ = help_render.render_help_text(target)
                assert f"Target: {target}\nKind: attribute" in text


def test_semantic_concept_classes_share_their_canonical_object_pages() -> None:
    from marivo.introspection.live.targets import canonical_target
    from marivo.refs import Ref
    from marivo.semantic._authoring_temporal import PeriodCorrespondence
    from marivo.semantic.expressions import Entity, Relationship

    for type_obj in (Entity, Relationship, Ref, PeriodCorrespondence):
        assert help_render.render_help_text(type_obj) == help_render.render_help_text(
            f"semantic.{canonical_target(type_obj.__name__)}"
        )


def test_attribute_access_uses_owning_frozen_model_contracts() -> None:
    from pydantic import BaseModel, Field

    from marivo._temporal import TimeScopeContractV1
    from marivo.introspection.live.attributes import public_attributes

    # Required time identity fields need no construction to discover immutability.
    assert TimeScopeContractV1.model_config["frozen"] is True
    assert public_attributes(TimeScopeContractV1)["kind"].access == "read-only"
    text, _, _ = help_render.render_help_text("analysis.time_scope.kind")
    assert "Access: read-only" in text

    class PerFieldModel(BaseModel):
        immutable: int = Field(frozen=True)
        mutable: int

    class Unspecified:
        value: int

    assert public_attributes(PerFieldModel)["immutable"].access == "read-only"
    assert public_attributes(PerFieldModel)["mutable"].access == "read/write"
    assert (
        public_attributes(Unspecified)["value"].access == "readable; write policy is not declared"
    )
