"""Bounded live help for the datasource surface."""

from __future__ import annotations

from collections.abc import Callable

from marivo.datasource._capabilities.render import render_help_target, render_root_help
from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE
from marivo.datasource.authoring import DatasourceRef, DatasourceSpec
from marivo.datasource.catalog import DatasourceCatalog
from marivo.datasource.errors import DatasourceError
from marivo.datasource.evidence import (
    DimensionEvidenceResult,
    DimensionValuesResult,
    EntityEvidenceResult,
    MeasureEvidenceResult,
    RelationshipEvidenceResult,
    TimeEvidenceResult,
)
from marivo.datasource.inspection import SourceInspection
from marivo.datasource.manage import (
    DatasourceConnection,
    DatasourceDescription,
    DatasourceSummary,
    DatasourceTestResult,
)
from marivo.datasource.snapshot import DiscoverySnapshot
from marivo.datasource.source import PartitionScope, TableSource, UnprunedScope
from marivo.introspection.live.resolve import resolve_live_target

RegisteredDatasourceCallable = Callable[..., object]
DatasourceEvidenceResult = (
    EntityEvidenceResult
    | DimensionEvidenceResult
    | DimensionValuesResult
    | TimeEvidenceResult
    | MeasureEvidenceResult
    | RelationshipEvidenceResult
)
PublicDatasourceHelpTarget = (
    str
    | RegisteredDatasourceCallable
    | type[object]
    | DatasourceRef
    | DatasourceSpec
    | DatasourceCatalog
    | DatasourceSummary
    | DatasourceDescription
    | DatasourceTestResult
    | DatasourceConnection
    | TableSource
    | PartitionScope
    | UnprunedScope
    | SourceInspection
    | DiscoverySnapshot
    | DatasourceEvidenceResult
    | DatasourceError
    | None
)


def help_text(target: PublicDatasourceHelpTarget = None) -> str:
    """Return bounded datasource help for a registered live target.

    Parameters
    ----------
    target:
        A canonical datasource capability id, registered callable or bound
        method, public datasource type or runtime result, or registered
        datasource error. ``None`` returns the datasource root index.

    Returns
    -------
    str
        Bounded datasource help text.

    Raises
    ------
    DatasourceHelpTargetError
        If ``target`` is not owned by the datasource live surface.

    Example
    -------
    >>> print(help_text("inspect"))
    """
    if target is None:
        return render_root_help()
    return render_help_target(resolve_live_target(target, DATASOURCE_LIVE_SURFACE))


def help(target: PublicDatasourceHelpTarget = None) -> None:
    """Print bounded datasource help for a registered live target.

    Parameters
    ----------
    target:
        A canonical datasource capability id, registered callable or bound
        method, public datasource type or runtime result, or registered
        datasource error. ``None`` prints the datasource root index.

    Returns
    -------
    None

    Example
    -------
    >>> help("inspect")
    """
    print(help_text(target))
