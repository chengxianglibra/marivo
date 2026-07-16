"""Private datasource live-surface registry models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from marivo._authoring.model import AuthoringCapability
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.reflect import callable_identity

DatasourceRootGroup = Literal[
    "declare_manage",
    "physical_sources",
    "inspect_scope",
    "acquire_project",
    "diagnostics_boundaries",
]


@dataclass(frozen=True)
class DatasourceTypeContract:
    """Stable public fields and flow edges for one datasource runtime type."""

    name: str
    producers: tuple[LiveHelpTarget, ...]
    public_properties: tuple[str, ...] = ()
    public_methods: tuple[str, ...] = ()
    consumers: tuple[LiveHelpTarget, ...] = ()
    state_bearing: bool = False


@dataclass(frozen=True)
class DatasourceCapabilityRegistry:
    """Immutable lookup table for datasource live capability descriptors."""

    surface: Literal["datasource"]
    _descriptors: tuple[AuthoringCapability, ...]
    _groups: Mapping[DatasourceRootGroup, tuple[str, ...]]
    _by_id: Mapping[str, AuthoringCapability]
    _by_callable_path: Mapping[str, AuthoringCapability]

    def canonical_ids(self) -> tuple[str, ...]:
        return tuple(descriptor.canonical_id for descriptor in self._descriptors)

    def callable_ids(self) -> tuple[str, ...]:
        return tuple(
            descriptor.canonical_id
            for descriptor in self._descriptors
            if descriptor.callable_path is not None
        )

    def by_canonical_id(self, canonical_id: str) -> AuthoringCapability:
        return self._by_id[canonical_id]

    def by_callable(self, obj: object) -> AuthoringCapability:
        return self._by_callable_path[callable_identity(obj)]

    def group(self, group: DatasourceRootGroup) -> tuple[AuthoringCapability, ...]:
        return tuple(self._by_id[canonical_id] for canonical_id in self._groups[group])
