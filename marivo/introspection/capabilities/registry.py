"""Immutable ordered indexes for owner-supplied Help specifications."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType
from typing import Generic, TypeVar

from marivo._kernel.errors import HelpSurface, LiveHelpTarget
from marivo.introspection.capabilities.spec import CapabilitySpec
from marivo.introspection.live.reflect import (
    callable_function,
    callable_identity,
    import_registered_callable,
)

SpecT = TypeVar("SpecT", bound=CapabilitySpec)


@dataclass(frozen=True)
class TypeContractSpec:
    """Public fields, methods, and data-flow links for a runtime type."""

    name: str
    producers: tuple[LiveHelpTarget, ...]
    public_properties: tuple[str, ...] = ()
    public_methods: tuple[str, ...] = ()
    type_page_methods: tuple[str, ...] = ()
    consumers: tuple[LiveHelpTarget, ...] = ()
    guidance: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    example: str | None = None
    target: str | None = None
    method_links: tuple[tuple[str, LiveHelpTarget], ...] = ()


@dataclass(frozen=True)
class SurfaceRegistry(Generic[SpecT]):
    """Index the owning specifications without copying or adapting their facts."""

    surface: HelpSurface
    specs: tuple[SpecT, ...]

    def __post_init__(self) -> None:
        if len(self._by_id) != len(self.specs):
            raise ValueError(f"duplicate {self.surface} Help canonical id")
        if any(not spec.summary.strip() for spec in self.specs):
            raise ValueError(f"{self.surface} Help summaries must not be empty")
        if len(self._by_callable_path) != sum(bool(spec.callable_path) for spec in self.specs):
            raise ValueError(f"duplicate {self.surface} Help callable path")

    @cached_property
    def _by_id(self) -> Mapping[str, SpecT]:
        return MappingProxyType({spec.canonical_id: spec for spec in self.specs})

    @cached_property
    def _by_callable_path(self) -> Mapping[str, SpecT]:
        return MappingProxyType(
            {spec.callable_path: spec for spec in self.specs if spec.callable_path}
        )

    def canonical_ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    def discovery_ids(self) -> tuple[str, ...]:
        return self.canonical_ids()

    def callable_ids(self) -> tuple[str, ...]:
        return tuple(spec.canonical_id for spec in self._by_callable_path.values())

    def by_canonical_id(self, canonical_id: str) -> SpecT:
        return self._by_id[canonical_id]

    def by_callable(self, value: object) -> SpecT:
        path = callable_identity(value)
        spec = self._by_callable_path[path]
        if callable_function(value) is not callable_function(import_registered_callable(path)):
            raise KeyError(path)
        return spec
