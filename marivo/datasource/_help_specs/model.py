"""Private datasource live-surface registry models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from typing_extensions import override

from marivo._authoring.model import AuthoringCapability
from marivo.introspection.capabilities.registry import SurfaceRegistry
from marivo.introspection.live.model import LiveHelpTarget

DatasourceRootGroup = Literal["connections", "sources", "inspection", "root"]


@dataclass(frozen=True)
class DatasourceNavigationRoute:
    """One need-directed route owned by the datasource registry."""

    label: str
    target: LiveHelpTarget
    summary: str


@dataclass(frozen=True)
class DatasourceManifest(SurfaceRegistry[AuthoringCapability]):
    """The shared registry with datasource discovery membership."""

    _groups: Mapping[DatasourceRootGroup, tuple[str, ...]]
    _navigation_routes: Mapping[str, tuple[DatasourceNavigationRoute, ...]]

    @override
    def discovery_ids(self) -> tuple[str, ...]:
        return tuple(row.canonical_id for row in self.specs if row.kind != "method")

    def group(self, group: DatasourceRootGroup) -> tuple[AuthoringCapability, ...]:
        return tuple(self._by_id[canonical_id] for canonical_id in self._groups[group])

    def navigation_routes(self, canonical_id: str = "") -> tuple[DatasourceNavigationRoute, ...]:
        """Return the root or topic's explicit capability-selection routes."""
        return self._navigation_routes.get(canonical_id, ())
