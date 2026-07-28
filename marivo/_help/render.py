"""Public print boundary for unified Marivo help."""

from __future__ import annotations

from collections.abc import Callable

from marivo._help.model import (
    MarivoHelpTargetError,
    NativeHelpRoute,
    SurfaceRootHelpRoute,
    TopicHelpRoute,
)
from marivo._help.object_briefing import (
    is_semantic_object,
    render_semantic_object,
    semantic_object_path,
)
from marivo._help.route import render_native_route, render_surface_root, route_help_target
from marivo._help.topics import render_authoring, render_load, render_root
from marivo.refs import Ref, SemanticKindTag
from marivo.render import RenderableResult
from marivo.telemetry import track_operation

type PublicHelpTarget = (
    str
    | Callable[..., object]
    | type[object]
    | Ref[SemanticKindTag]
    | RenderableResult
    | BaseException
    | None
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
    name = type(target).__name__
    if name == "Ref":
        return "ref"
    if name.endswith("Entry"):
        return "entry"
    if name.endswith("Error"):
        return "error"
    return "result"


def _topic_text(route: TopicHelpRoute) -> str:
    if route.topic == "root":
        return render_root()
    if route.topic == "authoring":
        return render_authoring()
    return render_load()


def render_help_text(target: PublicHelpTarget = None) -> tuple[str, str, str | None]:
    """Return private rendered text plus bounded routing telemetry facts."""
    if target is not None and is_semantic_object(target):
        text = render_semantic_object(target)
        owner = "semantic"
        canonical_id = semantic_object_path(target)
        return text, owner, canonical_id

    route = route_help_target(target)
    if isinstance(route, TopicHelpRoute):
        return _topic_text(route), "global", route.topic
    if isinstance(route, SurfaceRootHelpRoute):
        return render_surface_root(route), route.owner, route.owner
    if isinstance(route, NativeHelpRoute):
        resolved_id = (
            route.resolved.canonical_id or route.resolved.type_name or route.resolved.error_name
        )
        return render_native_route(route), route.owner, resolved_id
    raise RuntimeError(f"unsupported help route: {route!r}")


def help(target: PublicHelpTarget = None) -> None:
    """Print bounded help for one registered Marivo target.

    Parameters
    ----------
    target:
        ``None`` for the global index, a registered string, callable, type,
        result, error, exact semantic ``Ref``, or loaded ``CatalogEntry``.

    Returns
    -------
    None

    Example
    -------
    >>> import marivo
    >>> marivo.help("analysis.observe")
    """
    attributes = {"marivo.help.target_kind": _target_kind(target)}
    with track_operation(
        "marivo.help",
        family="read",
        intent="help",
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
        print(text)
