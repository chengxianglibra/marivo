"""Lazy cross-surface routing for unified help."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from difflib import SequenceMatcher

from marivo._help.model import (
    HelpRoute,
    MarivoHelpTargetError,
    NativeHelpRoute,
    SurfaceRootHelpRoute,
    TopicHelpRoute,
)
from marivo.introspection.capabilities.provider import SurfaceProvider
from marivo.introspection.capabilities.resolve import (
    LiveSurfaceAdapter,
    resolve_live_target,
)
from marivo.introspection.capabilities.spec import CapabilitySpec
from marivo.introspection.live.model import SURFACE_LIMITS, HelpSurface
from marivo.introspection.live.targets import canonical_target


def _load_datasource() -> SurfaceProvider:
    from marivo.datasource.help_spi import PROVIDER  # noqa: PLC0415  # Load only the Help surface selected by the caller.

    return PROVIDER


def _load_semantic() -> SurfaceProvider:
    from marivo.semantic.help_spi import PROVIDER  # noqa: PLC0415  # Load only the Help surface selected by the caller.

    return PROVIDER


def _load_analysis() -> SurfaceProvider:
    from marivo.analysis.help_spi import PROVIDER  # noqa: PLC0415  # Load only the Help surface selected by the caller.

    return PROVIDER


def _load_ontology() -> SurfaceProvider:
    from marivo.ontology.help_spi import PROVIDER  # noqa: PLC0415  # Load only the Help surface selected by the caller.

    return PROVIDER


_SURFACE_PROVIDERS: dict[HelpSurface, Callable[[], SurfaceProvider]] = {
    "datasource": _load_datasource,
    "semantic": _load_semantic,
    "analysis": _load_analysis,
    "ontology": _load_ontology,
}
_SURFACES = tuple(_SURFACE_PROVIDERS)


def _native_provider(owner: HelpSurface) -> SurfaceProvider:
    return _SURFACE_PROVIDERS[owner]()


def _native_surface(owner: HelpSurface) -> LiveSurfaceAdapter[CapabilitySpec]:
    return _native_provider(owner).adapter


def _suggestions(errors: Iterable[Exception]) -> tuple[str, ...]:
    values: dict[str, None] = {}
    for error in errors:
        repair = getattr(error, "repair", None)
        for candidate in getattr(repair, "candidates", ()):
            if isinstance(candidate, str):
                values.setdefault(candidate, None)
    return tuple(values)[: SURFACE_LIMITS.help_suggestion_limit]


def _resolve_one(target: object, owner: HelpSurface) -> NativeHelpRoute | None:
    page = _native_provider(owner).locate(target)
    return None if page is None else NativeHelpRoute(owner, page)


def _defining_surface(target: object) -> HelpSurface | None:
    module = getattr(target, "__module__", None)
    if isinstance(module, str) and module.startswith("marivo."):
        owner = module.removeprefix("marivo.").partition(".")[0]
        if owner in _SURFACES:
            return owner
    return None


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
    contract = _native_provider(owner).type_contracts.get(type_obj)
    return ((), ()) if contract is None else (contract.public_methods, contract.type_page_methods)


def owned_method_target(owner: HelpSurface, type_obj: type, name: str) -> str | None:
    """Read the method's authored link or resolve its installed callable."""
    contract = _native_provider(owner).type_contracts[type_obj]
    for member, target in contract.method_links:
        if member == name:
            return target.display
    method = getattr(type_obj, name)
    if _defining_surface(method) is None:
        native = _resolve_one(method, owner)
        if native is not None:
            return native_route_target(native)
    route = route_help_target(method)
    return native_route_target(route) if isinstance(route, NativeHelpRoute) else None


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
            method_target = owned_method_target(owner, type_obj, member)
            if method_target is not None:
                return (method_target, parent_target)
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


def _route_string(target: str) -> HelpRoute:
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
            except _native_provider(owner).target_error as error:
                qualified_errors.append(error)
            candidates = tuple(
                dict.fromkeys(
                    (
                        *_receiver_candidates(native_target, owner),
                        *_exact_cross_surface_candidates(
                            native_target,
                            excluded_owner=owner,
                        ),
                        *(f"{owner}.{candidate}" for candidate in _suggestions(qualified_errors)),
                    )
                )
            )[: SURFACE_LIMITS.help_suggestion_limit]
            raise MarivoHelpTargetError(
                target=target,
                outcome="unknown",
                candidates=candidates,
            )
        return route
    if target == "root":
        return TopicHelpRoute("root")
    if target == "authoring":
        return TopicHelpRoute("authoring")
    raise MarivoHelpTargetError(
        target=target,
        outcome="unknown",
        candidates=_exact_cross_surface_candidates(target),
    )


def route_help_target(target: object | None) -> HelpRoute:
    """Resolve one public target without rendering or invoking domain behavior."""
    if target is None:
        return TopicHelpRoute("root")

    if isinstance(target, str):
        return _route_string(target)
    if not isinstance(target, type) and not callable(target):
        raise MarivoHelpTargetError(target=target, outcome="unknown")

    # A defining owner already takes precedence over surfaces retaining the
    # same object. Try it before importing foreign manifests for resolution.
    defining_owner = _defining_surface(target)
    if defining_owner is not None:
        native = _resolve_one(target, defining_owner)
        if native is not None:
            return native
    routes: list[NativeHelpRoute] = []
    for owner in _SURFACES:
        if owner == defining_owner:
            continue
        route = _resolve_one(target, owner)
        if route is not None:
            routes.append(route)

    if len(routes) == 1:
        return routes[0]
    if len(routes) > 1:
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
        except _native_provider(owner).target_error as error:
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
    return _native_provider(route.owner).root()


def render_native_route(route: NativeHelpRoute) -> str:
    return route.page.render()
