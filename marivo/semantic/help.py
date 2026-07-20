"""Bounded live help for the semantic surface."""

from __future__ import annotations

from collections.abc import Callable

from marivo.introspection.live.resolve import resolve_live_target
from marivo.refs import Ref, SemanticKindTag
from marivo.semantic._capabilities.render import render_help_target, render_root_help
from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE
from marivo.semantic.catalog import CatalogCollection, CatalogEntry, SemanticCatalog
from marivo.semantic.dtos import VerifyResult
from marivo.semantic.errors import SemanticError
from marivo.semantic.readiness import ReadinessReport
from marivo.semantic.richness import RichnessReport

RegisteredSemanticCallable = Callable[..., object]
PublicSemanticHelpTarget = (
    str
    | RegisteredSemanticCallable
    | type[object]
    | Ref[SemanticKindTag]
    | CatalogEntry[SemanticKindTag]
    | CatalogCollection[SemanticKindTag]
    | SemanticCatalog
    | VerifyResult
    | ReadinessReport
    | RichnessReport
    | SemanticError
    | None
)


def help_text(target: PublicSemanticHelpTarget = None) -> str:
    """Return bounded semantic help for a registered live target.

    Parameters
    ----------
    target:
        A canonical semantic capability id, registered callable or bound
        method, public semantic type or runtime result, or registered
        semantic error. ``None`` returns the semantic root index.

    Returns
    -------
    str
        Bounded semantic help text.

    Raises
    ------
    SemanticHelpTargetError
        If ``target`` is not owned by the semantic live surface.

    Example
    -------
    >>> print(help_text("load"))
    """
    if target is None or target == "":
        return render_root_help()
    return render_help_target(
        resolve_live_target(target, SEMANTIC_LIVE_SURFACE),
        original_target=target,
    )


def help(target: PublicSemanticHelpTarget = None) -> None:
    """Print bounded semantic help for a registered live target.

    Parameters
    ----------
    target:
        A canonical semantic capability id, registered callable or bound
        method, public semantic type or runtime result, or registered
        semantic error. ``None`` prints the semantic root index.

    Returns
    -------
    None

    Example
    -------
    >>> help("load")
    """
    print(help_text(target))
