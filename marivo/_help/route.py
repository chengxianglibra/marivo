"""Lazy cross-surface routing for unified help."""

from __future__ import annotations

from collections.abc import Iterable
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
)

_SURFACES: tuple[HelpSurface, ...] = ("datasource", "semantic", "analysis", "ontology")
_GLOBAL_TOPICS = ("authoring",)

if TYPE_CHECKING:
    from marivo._authoring.model import AuthoringCapability
    from marivo.analysis._capabilities.model import CapabilityDescriptor
    from marivo.ontology._capabilities.registry import OntologyDescriptor


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
    values: set[str] = set()
    for error in errors:
        repair = getattr(error, "repair", None)
        for candidate in getattr(repair, "candidates", ()):
            if isinstance(candidate, str):
                values.add(candidate)
    return tuple(sorted(values))[: SURFACE_LIMITS.help_suggestion_limit]


def _resolve_one(target: object, owner: HelpSurface) -> NativeHelpRoute | None:
    try:
        resolved = resolve_live_target(target, _native_surface(owner))
    except _native_target_error(owner):
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


def _exact_cross_surface_candidates(
    target: str,
    *,
    excluded_owner: HelpSurface,
) -> tuple[str, ...]:
    candidates: list[str] = []
    for owner in _SURFACES:
        if owner == excluded_owner:
            continue
        route = _resolve_one(target, owner)
        if route is None:
            continue
        resolved = route.resolved
        native_target = resolved.canonical_id or resolved.type_name or resolved.error_name
        if native_target is not None:
            candidates.append(f"{owner}.{native_target}")
    return tuple(candidates)


def route_help_target(target: object | None) -> HelpRoute:
    """Resolve one public target without rendering or invoking domain behavior."""
    if target is None:
        return TopicHelpRoute("root")

    if isinstance(target, str):
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
    routes: list[NativeHelpRoute] = []
    errors: list[Exception] = []
    for owner in _SURFACES:
        try:
            resolved = resolve_live_target(target, _native_surface(owner))
        except _native_target_error(owner) as error:
            errors.append(error)
            continue
        routes.append(
            NativeHelpRoute(
                owner=owner,
                resolved=resolved,
                original_target=target,
            )
        )

    if len(routes) == 1:
        return routes[0]
    if len(routes) > 1:
        candidates = tuple(
            f"{route.owner}.{route.resolved.canonical_id or route.resolved.type_name or route.resolved.error_name}"
            for route in routes
        )
        raise MarivoHelpTargetError(
            target=target,
            outcome="ambiguous",
            candidates=candidates,
        )
    raise MarivoHelpTargetError(
        target=target,
        outcome="unknown",
        candidates=_suggestions(errors),
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
            cast("ResolvedLiveTarget[AuthoringCapability]", route.resolved),
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
        cast("ResolvedLiveTarget[CapabilityDescriptor]", route.resolved),
        original_target=route.original_target,
    )
