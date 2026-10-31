"""Lazy cross-surface routing for unified help."""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, cast

from marivo._help.model import (
    GlobalTopic,
    HelpRoute,
    MarivoHelpTargetError,
    NativeHelpRoute,
    SurfaceRootHelpRoute,
    TopicHelpRoute,
)
from marivo.introspection.live.model import SURFACE_LIMITS, HelpSurface, ResolvableHelpDescriptor
from marivo.introspection.live.resolve import (
    LiveSurface,
    ResolvedLiveTarget,
    resolve_live_target,
    try_resolve_live_target,
)
from marivo.introspection.live.targets import canonical_target

_SURFACES: tuple[HelpSurface, ...] = ("datasource", "semantic", "analysis", "ontology")
_GLOBAL_TOPICS = ("root", "authoring")

if TYPE_CHECKING:
    from marivo._authoring.model import AuthoringCapability
    from marivo.analysis._capabilities.dataset_model import Descriptor
    from marivo.ontology._capabilities.registry import OntologyDescriptor
    from marivo.semantic._capabilities.model import SemanticHelpDescriptor


def _native_surface(
    owner: HelpSurface,
) -> LiveSurface[ResolvableHelpDescriptor]:
    if owner == "datasource":
        from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE

        return cast("LiveSurface[ResolvableHelpDescriptor]", DATASOURCE_LIVE_SURFACE)
    if owner == "semantic":
        from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

        return cast("LiveSurface[ResolvableHelpDescriptor]", SEMANTIC_LIVE_SURFACE)
    if owner == "ontology":
        from marivo.ontology._capabilities.surface import ONTOLOGY_LIVE_SURFACE

        return cast("LiveSurface[ResolvableHelpDescriptor]", ONTOLOGY_LIVE_SURFACE)
    from marivo.analysis._capabilities.surface import ANALYSIS_LIVE_SURFACE

    return cast("LiveSurface[ResolvableHelpDescriptor]", ANALYSIS_LIVE_SURFACE)


def _native_target_error(owner: HelpSurface) -> type[Exception]:
    if owner == "datasource":
        from marivo.datasource.errors import DatasourceHelpTargetError

        return DatasourceHelpTargetError
    if owner == "semantic":
        from marivo.semantic.errors import SemanticHelpTargetError

        return SemanticHelpTargetError
    if owner == "ontology":
        from marivo.ontology.errors import OntologyHelpTargetError

        return OntologyHelpTargetError
    from marivo.analysis.errors import HelpTargetError

    return HelpTargetError


def _suggestions(errors: Iterable[Exception]) -> tuple[str, ...]:
    values: dict[str, None] = {}
    for error in errors:
        repair = getattr(error, "repair", None)
        for candidate in getattr(repair, "candidates", ()):
            if isinstance(candidate, str):
                values.setdefault(candidate, None)
    return tuple(values)[: SURFACE_LIMITS.help_suggestion_limit]


def _resolve_one(target: object, owner: HelpSurface) -> NativeHelpRoute | None:
    resolved = try_resolve_live_target(target, _native_surface(owner))
    if resolved is None:
        return None
    return NativeHelpRoute(
        owner=owner,
        resolved=resolved,
        original_target=target,
    )


def _qualified_string(target: str) -> tuple[HelpSurface, str] | None:
    prefix, separator, remainder = target.partition(".")
    if not separator or prefix not in _SURFACES:
        return None
    return prefix, remainder


def _owned_methods(
    owner: HelpSurface,
    type_obj: type,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read method membership from the native type contract."""
    if owner == "semantic":
        from marivo.semantic._capabilities.registry import TYPE_CONTRACTS

        semantic = TYPE_CONTRACTS[type_obj]
        return semantic.public_methods, semantic.type_page_methods
    if owner == "datasource":
        from marivo.datasource._capabilities.registry import TYPE_CONTRACTS as DATASOURCE_CONTRACTS

        datasource = DATASOURCE_CONTRACTS[type_obj]
        return datasource.public_methods, datasource.type_page_methods
    if owner == "ontology":
        from marivo.ontology._capabilities.registry import TYPE_CONTRACTS as ONTOLOGY_CONTRACTS

        ontology = ONTOLOGY_CONTRACTS[type_obj]
        return ontology.public_methods, ontology.type_page_methods
    # Analysis renders methods from its native callable bindings.
    return (), ()


def _receiver_candidates(target: str, owner: HelpSurface) -> tuple[str, ...]:
    """Repair a member guess using its registered receiver and callable owner."""
    parent, separator, member = target.rpartition(".")
    if not separator:
        return ()
    route = _resolve_one(parent, owner)
    if route is None:
        return ()
    parent_target = native_route_target(route)
    for type_obj, type_name in _native_surface(owner).type_index.items():
        if _native_surface(owner).type_targets.get(type_obj, canonical_target(type_name)) != parent:
            continue
        methods, type_page_methods = _owned_methods(owner, type_obj)
        if member in methods and member not in type_page_methods:
            method_route = route_help_target(getattr(type_obj, member))
            if isinstance(method_route, NativeHelpRoute):
                return (native_route_target(method_route), parent_target)
    if any(
        candidate.rpartition(".")[0] == parent
        and SequenceMatcher(None, member, candidate.rpartition(".")[2]).ratio() >= 0.7
        for candidate in _native_surface(owner).registry.canonical_ids()
    ):
        return ()
    return (parent_target,)


def _exact_cross_surface_candidates(
    target: str,
    *,
    excluded_owner: HelpSurface | None = None,
) -> tuple[str, ...]:
    candidates: list[str] = []
    for owner in _SURFACES:
        if owner == excluded_owner:
            continue
        route = _resolve_one(target, owner)
        if route is None:
            continue
        candidates.append(native_route_target(route))
    return tuple(candidates)


def native_route_target(route: NativeHelpRoute) -> str:
    """Return the public address of an already resolved native page."""
    resolved = route.resolved
    identity = resolved.canonical_id or resolved.type_name or resolved.error_name
    if identity is None:
        raise RuntimeError("native Help route has no registered identity")
    target = canonical_target(identity)
    if resolved.type_name is not None:
        surface = _native_surface(route.owner)
        target = next(
            (
                surface.type_targets.get(type_obj, canonical_target(name))
                for type_obj, name in surface.type_index.items()
                if name == resolved.type_name
            ),
            target,
        )
    if resolved.kind == "attribute":
        target += f".{resolved.attribute_name}"
    return f"{route.owner}.{target}"


def route_help_target(target: object | None) -> HelpRoute:
    """Resolve one public target without rendering or invoking domain behavior."""
    if target is None:
        return TopicHelpRoute("root")

    if isinstance(target, str):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*", target):
            raise MarivoHelpTargetError(target=target, outcome="unknown")
        if target in _SURFACES:
            return SurfaceRootHelpRoute(target)
        qualified = _qualified_string(target)
        if qualified is not None:
            owner, native_target = qualified
            if not native_target:
                raise MarivoHelpTargetError(target=target, outcome="unknown")
            route = _resolve_one(native_target, owner)
            if route is None:
                qualified_errors: list[Exception] = []
                try:
                    resolve_live_target(native_target, _native_surface(owner))
                except _native_target_error(owner) as error:
                    qualified_errors.append(error)
                candidates = tuple(
                    dict.fromkeys(
                        (
                            *_receiver_candidates(native_target, owner),
                            *_exact_cross_surface_candidates(
                                native_target,
                                excluded_owner=owner,
                            ),
                            *(
                                f"{owner}.{candidate}"
                                for candidate in _suggestions(qualified_errors)
                            ),
                        )
                    )
                )[: SURFACE_LIMITS.help_suggestion_limit]
                raise MarivoHelpTargetError(
                    target=target,
                    outcome="unknown",
                    candidates=candidates,
                )
            return route
        if target in _GLOBAL_TOPICS:
            return TopicHelpRoute(cast("GlobalTopic", target))
        raise MarivoHelpTargetError(
            target=target,
            outcome="unknown",
            candidates=_exact_cross_surface_candidates(target),
        )
    if isinstance(target, str) or (not isinstance(target, type) and not callable(target)):
        raise MarivoHelpTargetError(target=target, outcome="unknown")
    routes: list[NativeHelpRoute] = []
    for owner in _SURFACES:
        route = _resolve_one(target, owner)
        if route is not None:
            routes.append(route)

    if len(routes) == 1:
        return routes[0]
    if len(routes) > 1:
        if callable(target):
            # Shared catalog inputs can be retained by another surface. The
            # callable's defining module still identifies its native owner.
            module = getattr(target, "__module__", None)
            native_routes = tuple(
                route
                for route in routes
                if isinstance(module, str)
                and (
                    module == f"marivo.{route.owner}" or module.startswith(f"marivo.{route.owner}.")
                )
            )
            if len(native_routes) == 1:
                return native_routes[0]
        candidates = tuple(native_route_target(route) for route in routes)
        raise MarivoHelpTargetError(
            target=target,
            outcome="ambiguous",
            candidates=candidates,
        )
    qualified_candidates: dict[str, None] = {}
    for owner in _SURFACES:
        try:
            resolve_live_target(target, _native_surface(owner))
        except _native_target_error(owner) as error:
            for candidate in _suggestions((error,)):
                route = _resolve_one(candidate, owner)
                if route is not None:
                    qualified_candidates.setdefault(native_route_target(route), None)
    raise MarivoHelpTargetError(
        target=target,
        outcome="unknown",
        candidates=tuple(qualified_candidates)[: SURFACE_LIMITS.help_suggestion_limit],
    )


def render_surface_root(route: SurfaceRootHelpRoute) -> str:
    """Render one native root page through its owning surface."""
    if route.owner == "datasource":
        from marivo.datasource._capabilities.render import render_root_help

        return render_root_help()
    if route.owner == "semantic":
        from marivo.semantic._capabilities.render import render_root_help

        return render_root_help()
    if route.owner == "ontology":
        from marivo.ontology._capabilities.render import render_root_help

        return render_root_help()
    from marivo.analysis._capabilities.render import render_root_help

    return render_root_help()


def render_native_route(route: NativeHelpRoute) -> str:
    """Render through the owning native descriptor model."""
    if route.owner == "datasource":
        from marivo.datasource._capabilities.render import (
            render_help_target as render_datasource_target,
        )

        return render_datasource_target(
            cast("ResolvedLiveTarget[AuthoringCapability]", route.resolved),
            original_target=route.original_target,
        )
    if route.owner == "semantic":
        from marivo.semantic._capabilities.render import (
            render_help_target as render_semantic_target,
        )

        return render_semantic_target(
            cast("ResolvedLiveTarget[SemanticHelpDescriptor]", route.resolved),
            original_target=route.original_target,
        )
    if route.owner == "ontology":
        from marivo.ontology._capabilities.render import (
            render_help_target as render_ontology_target,
        )

        return render_ontology_target(
            cast("ResolvedLiveTarget[OntologyDescriptor]", route.resolved),
            original_target=route.original_target,
        )
    from marivo.analysis._capabilities.render import (
        render_help_target as render_analysis_target,
    )

    return render_analysis_target(
        cast("ResolvedLiveTarget[Descriptor]", route.resolved),
        original_target=route.original_target,
    )
