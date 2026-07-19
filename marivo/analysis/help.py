"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from marivo.analysis._capabilities.render import render_help_target, render_root_help
from marivo.analysis._capabilities.surface import ANALYSIS_LIVE_SURFACE
from marivo.analysis.errors import AnalysisError
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.session.core import Session
from marivo.introspection.live.resolve import resolve_live_target
from marivo.refs import SemanticRef
from marivo.semantic.catalog import CatalogObject

if TYPE_CHECKING:
    from marivo.semantic.reader import SemanticProject

RegisteredPublicCallable = Callable[..., object]
PublicHelpTarget = (
    str
    | RegisteredPublicCallable
    | type[object]
    | Session
    | BaseFrame
    | CatalogObject[SemanticRef]
    | SemanticRef
    | AnalysisError
    | None
)


def help_text(
    target: PublicHelpTarget = None,
    *,
    project: SemanticProject | None = None,
) -> str:
    """Return bounded analysis help text as a string.

    Parameters
    ----------
    target:
        The help target. ``None`` returns root help. Accepts canonical
        strings, registered public callables/types, public analysis
        objects, semantic objects/refs, and AnalysisError subclasses or
        instances.
    project:
        Explicit SemanticProject for semantic ref resolution. Required
        when ``target`` is a ``SemanticRef`` and no project can be
        inferred from the current working directory.

    Returns
    -------
    str
        Bounded help text for the requested target.

    Raises
    ------
    HelpTargetError
        If ``target`` is not a registered canonical target.

    Example:
        >>> text = mv.help_text("observe")
        >>> print(text)
    """
    if target is None:
        return render_root_help()

    resolved = resolve_live_target(target, ANALYSIS_LIVE_SURFACE)
    return render_help_target(resolved, project=project, original_target=target)


def help(
    target: PublicHelpTarget = None,
    *,
    project: SemanticProject | None = None,
) -> None:
    """Print bounded help text for a Marivo analysis symbol or semantic ref.

    Parameters
    ----------
    target:
        The help target. ``None`` prints root help. Accepts canonical
        strings, registered public callables/types, public analysis
        objects, semantic objects/refs, and AnalysisError subclasses or
        instances.
    project:
        Explicit SemanticProject for semantic ref resolution. Required
        when ``target`` is a ``SemanticRef`` and no project can be
        inferred from the current working directory.

    Returns
    -------
    None

    Raises
    ------
    HelpTargetError
        If ``target`` is not a registered canonical target.

    Example:
        >>> mv.help()                       # root analysis help
        >>> mv.help("observe")              # capability help
        >>> mv.help("MetricFrame")          # type help
        >>> mv.help(ref, project=project)   # semantic-object help
    """
    print(help_text(target, project=project))
