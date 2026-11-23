"""Bind owned Help specifications and renderers to the public coordinator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, NoReturn, TypeVar

from marivo.introspection.capabilities.registry import TypeContractSpec
from marivo.introspection.capabilities.resolve import (
    LiveSurfaceAdapter,
    ResolvedLiveTarget,
    build_suggestion_index,
    try_resolve_live_target,
)
from marivo.introspection.capabilities.spec import CapabilitySpec, RegistryView

SpecT = TypeVar("SpecT", bound=CapabilitySpec)


@dataclass(frozen=True)
class SurfaceRenderer(Generic[SpecT]):
    """Owner-supplied page grammar with shared dispatch by resolved kind."""

    root: Callable[[], str]
    descriptor: Callable[[SpecT], str]
    type_page: Callable[[str], str]
    error_page: Callable[[str], str]

    def render(self, resolved: ResolvedLiveTarget[SpecT]) -> str:
        if resolved.descriptor is not None:
            return self.descriptor(resolved.descriptor)
        if resolved.kind == "type_contract" and resolved.type_name is not None:
            return self.type_page(resolved.type_name)
        if resolved.error_name is not None:
            return self.error_page(resolved.error_name)
        raise RuntimeError(f"unsupported Help page: {resolved.kind}")


@dataclass(frozen=True)
class BoundHelp:
    """A resolved page with a renderer that retains the owning specification type."""

    resolved: ResolvedLiveTarget[CapabilitySpec]
    kind: str
    render: Callable[[], str]


@dataclass(frozen=True)
class SurfaceProvider:
    """Type-neutral Help entry points exposed by each domain's SPI."""

    adapter: LiveSurfaceAdapter[CapabilitySpec]
    root: Callable[[], str]
    locate: Callable[[object], BoundHelp | None]
    target_error: type[Exception]
    type_contracts: Mapping[type, TypeContractSpec]


def bind_provider(
    adapter: LiveSurfaceAdapter[SpecT],
    renderer: SurfaceRenderer[SpecT],
    *,
    target_error: type[Exception],
    type_contracts: Mapping[type, TypeContractSpec] = MappingProxyType({}),
    descriptor_kind: Callable[[SpecT], str] | None = None,
) -> SurfaceProvider:
    def locate(target: object) -> BoundHelp | None:
        resolved = try_resolve_live_target(target, adapter)
        if resolved is None:
            return None
        descriptor = resolved.descriptor
        kind = "object"
        if resolved.kind == "attribute":
            kind = "attribute"
        elif resolved.kind == "error_contract":
            kind = "error"
        elif descriptor is not None:
            kind = (
                descriptor_kind(descriptor)
                if descriptor_kind is not None
                else "callable"
                if descriptor.callable_path
                else "expression"
                if descriptor.public_entrypoint
                else "topic"
            )
        return BoundHelp(resolved, kind, lambda: renderer.render(resolved))

    return SurfaceProvider(adapter, renderer.root, locate, target_error, type_contracts)


def adapt_surface(
    registry: RegistryView[SpecT],
    type_contracts: Mapping[type, TypeContractSpec],
    error_types: Mapping[str, type],
    *,
    help_target_error: Callable[[object, tuple[str, ...]], NoReturn],
    default_suggestions: tuple[str, ...],
    enrich: Callable[[object], ResolvedLiveTarget[SpecT] | None] | None = None,
) -> LiveSurfaceAdapter[SpecT]:
    return LiveSurfaceAdapter(
        registry=registry,
        type_index=MappingProxyType(
            {cls: contract.name for cls, contract in type_contracts.items()}
        ),
        type_targets=MappingProxyType(
            {cls: spec.target for cls, spec in type_contracts.items() if spec.target is not None}
        ),
        error_types=error_types,
        help_target_error=help_target_error,
        default_suggestions=default_suggestions,
        enrich=enrich,
        suggestion_index=build_suggestion_index(registry),
    )
