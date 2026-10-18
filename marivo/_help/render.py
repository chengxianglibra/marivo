"""Public print boundary for unified Marivo help."""

import re
from collections.abc import Callable

from typing_extensions import TypeAliasType

from marivo._help.model import (
    MarivoHelpSurfaceError,
    MarivoHelpTargetError,
    NativeHelpRoute,
    SurfaceRootHelpRoute,
    TopicHelpRoute,
)
from marivo._help.route import (
    _owned_methods,
    native_route_target,
    render_native_route,
    render_surface_root,
    route_help_target,
)
from marivo._help.topics import render_authoring, render_root
from marivo.introspection.live.attributes import public_attributes, render_attribute
from marivo.introspection.live.model import HelpSurface
from marivo.introspection.live.targets import canonical_target
from marivo.telemetry import track_operation

PublicHelpTarget = TypeAliasType(
    "PublicHelpTarget",
    str | Callable[..., object] | type[object] | None,
)


def _target_kind(target: object | None) -> str:
    if target is None:
        return "root"
    if isinstance(target, str):
        return "string"
    if isinstance(target, type):
        return "type"
    if callable(target):
        return "callable"
    return "unsupported"


def _topic_text(route: TopicHelpRoute) -> str:
    if route.topic == "root":
        return render_root()
    if route.topic == "authoring":
        return render_authoring()
    raise RuntimeError(f"unsupported global help topic: {route.topic!r}")


def _header(text: str, target: str, kind: str) -> str:
    from marivo import __version__
    from marivo.introspection.live.render import bounded_help

    return bounded_help(
        f"Target: {target}\nKind: {kind}\nMarivo: {__version__}\n\n{text}",
        root=target in ("root", "authoring", "analysis", "semantic", "datasource", "ontology"),
    )


def _method_routes(owner: HelpSurface, type_obj: type, type_name: str) -> str:
    """Link native method facts to their actual canonical documentation owner."""
    lines: list[str] = []
    methods, type_page_methods = _owned_methods(owner, type_obj)
    for name in methods:
        if name in type_page_methods:
            target = f"{owner}.{canonical_target(type_name)}"
            suffix = " (documented on this type page)"
        else:
            route = route_help_target(getattr(type_obj, name))
            if not isinstance(route, NativeHelpRoute):
                raise RuntimeError(f"public method has no native Help owner: {type_name}.{name}")
            identity = route.resolved.canonical_id or route.resolved.type_name
            if identity is None:
                raise RuntimeError(f"public method has no canonical target: {type_name}.{name}")
            target = f"{route.owner}.{canonical_target(identity)}"
            suffix = ""
        lines.append(f'  {name}: marivo.help("{target}"){suffix}')
    return "\nMethods (exact Help targets):\n" + "\n".join(lines) if lines else ""


def render_help_text(target: PublicHelpTarget = None) -> tuple[str, str, str | None]:
    """Return private rendered text plus bounded routing telemetry facts."""
    route = route_help_target(target)
    if isinstance(route, TopicHelpRoute):
        return _header(_topic_text(route), route.topic, "topic"), "global", route.topic
    if isinstance(route, SurfaceRootHelpRoute):
        return _header(render_surface_root(route), route.owner, "topic"), route.owner, route.owner
    if isinstance(route, NativeHelpRoute):
        resolved_id = (
            route.resolved.canonical_id or route.resolved.type_name or route.resolved.error_name
        )
        resolved = route.resolved
        kind = "object"
        if resolved.kind == "attribute":
            from marivo._help.route import _native_surface

            type_obj = resolved.attribute_owner
            assert type_obj is not None
            assert resolved.attribute_name is not None
            resolved_id = f"{canonical_target(resolved.type_name or '')}.{resolved.attribute_name}"
            surface = _native_surface(route.owner)
            receiver = canonical_target(resolved.type_name or "")
            conditions = (
                tuple(
                    dict.fromkeys(
                        surface.attribute_applicability[value]
                        for value, name in surface.type_index.items()
                        if canonical_target(name) == receiver
                        and value in surface.attribute_applicability
                        and resolved.attribute_name in public_attributes(value)
                    )
                )
                if type_obj in surface.attribute_applicability
                else ()
            )
            text = render_attribute(
                type_obj,
                resolved.attribute_name,
                receiver=receiver,
                applicability=conditions,
            )
            kind = "attribute"
        else:
            text = render_native_route(route)
            if resolved.descriptor is not None:
                descriptor = resolved.descriptor
                from marivo._help.route import _native_surface

                surface = _native_surface(route.owner)
                kind = (
                    surface.descriptor_kind(descriptor)
                    if surface.descriptor_kind
                    else "callable"
                    if descriptor.callable_path
                    else "expression"
                    if descriptor.public_entrypoint
                    else "topic"
                )
            elif resolved.kind == "error_contract":
                kind = "error"
            from marivo._help.route import _native_surface

            type_bindings = tuple(
                (value, name)
                for value, name in _native_surface(route.owner).type_index.items()
                if name == resolved.type_name or canonical_target(name) == resolved_id
            )
            type_binding = next(
                (
                    binding
                    for binding in type_bindings
                    if binding[1] == (resolved.type_name or resolved_id)
                ),
                type_bindings[0] if type_bindings else None,
            )
            if type_binding is not None:
                type_obj, type_name = type_binding
                kind = "object"
                text += _method_routes(route.owner, type_obj, type_name)
                attributes = {
                    member: facts
                    for variant, variant_name in _native_surface(route.owner).type_index.items()
                    if canonical_target(variant_name) == canonical_target(type_name)
                    for member, facts in public_attributes(variant).items()
                }
                if attributes:
                    text += "\nAttributes (query by string target):\n" + "\n".join(
                        f"  {route.owner}.{canonical_target(type_name)}.{name}"
                        for name in sorted(attributes)
                    )
        full_target = native_route_target(route)
        return _header(text, full_target, kind), route.owner, resolved_id
    raise RuntimeError(f"unsupported help route: {route!r}")


def help(target: PublicHelpTarget = None) -> None:
    """Print bounded help for one registered Marivo target.

    Parameters
    ----------
    target:
        ``None`` for the global concept root, a complete canonical string, callable,
        or public type. Read instance state through its own public APIs.

    Returns
    -------
    None

    Example
    -------
    >>> import marivo
    >>> marivo.help()
    >>> marivo.help("analysis")
    >>> marivo.help("analysis.session.members")
    """
    _print_help(target)


def _cli_help_text(text: str) -> str:
    """Render canonical string links as executable CLI commands."""
    return re.sub(
        r"""marivo\.help\((["'])([a-z_][a-z0-9_.]*)\1\)""",
        lambda match: "marivo help " + match.group(2),
        text,
    ).replace("marivo.help()", "marivo help")


def _print_help(target: PublicHelpTarget = None, *, cli: bool = False) -> None:
    """Share resolution, native contracts and telemetry between public entrypoints."""
    attributes = {"marivo.help.target_kind": _target_kind(target)}
    with track_operation(
        surface="help",
        capability_kind="read",
        capability_id="help",
        attributes=attributes,
    ) as operation:
        try:
            text, owner, canonical_id = render_help_text(target)
        except MarivoHelpTargetError as error:
            if operation is not None:
                operation.attributes.update(
                    {
                        "marivo.help.outcome": error.outcome,
                        "marivo.help.resolved_owner": "global",
                    }
                )
            raise
        except Exception as error:
            if operation is not None:
                operation.attributes.update(
                    {
                        "marivo.help.outcome": "surface_error",
                        "marivo.help.resolved_owner": "global",
                    }
                )
            raise MarivoHelpSurfaceError(
                target=target,
                stage="route_or_render",
                cause_type=type(error).__name__,
            ) from error
        if operation is not None:
            operation.attributes.update(
                {
                    "marivo.help.outcome": "success",
                    "marivo.help.resolved_owner": owner,
                    **(
                        {"marivo.help.canonical_id": canonical_id}
                        if canonical_id is not None
                        else {}
                    ),
                }
            )
        print(_cli_help_text(text) if cli else text)
