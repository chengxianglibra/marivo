"""Private datasource live-surface registry models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from marivo.introspection.live.model import LiveCapability, LiveHelpTarget, LiveSurfaceRegistry

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
class DatasourceCapabilityRegistry(LiveSurfaceRegistry):
    """Immutable lookup table for datasource live capability descriptors."""

    surface: Literal["datasource"]
    _descriptors: tuple[LiveCapability, ...]
    _groups: Mapping[DatasourceRootGroup, tuple[str, ...]]
    _by_id: Mapping[str, LiveCapability]
    _by_callable_path: Mapping[str, LiveCapability]

    def canonical_ids(self) -> tuple[str, ...]:
        return tuple(descriptor.canonical_id for descriptor in self._descriptors)

    def callable_ids(self) -> tuple[str, ...]:
        return tuple(
            descriptor.canonical_id
            for descriptor in self._descriptors
            if descriptor.callable_path is not None
        )

    def by_canonical_id(self, canonical_id: str) -> LiveCapability:
        return self._by_id[canonical_id]

    def by_callable(self, obj: object) -> LiveCapability:
        function = getattr(obj, "__func__", obj)
        module = getattr(function, "__module__", None)
        qualname = getattr(function, "__qualname__", None)
        if not isinstance(module, str) or not isinstance(qualname, str):
            raise KeyError(obj)
        path = f"{module}.{qualname}"
        return self._by_callable_path[path]

    def group(self, group: DatasourceRootGroup) -> tuple[LiveCapability, ...]:
        return tuple(self._by_id[canonical_id] for canonical_id in self._groups[group])
