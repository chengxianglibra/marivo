"""Private semantic live-surface registry models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from marivo._authoring.model import AuthoringCapability, RepairKind
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.reflect import callable_identity

SemanticRootGroup = Literal[
    "browse_load",
    "author_families",
    "verify_preview",
    "readiness",
    "diagnostics_boundaries",
]
AuthoringPlacementKind = Literal["domain_entrypoint", "domain_module"]


@dataclass(frozen=True)
class AuthoringSourceContract:
    """Placement and post-load handoff for one source-authored semantic object."""

    placement_kind: AuthoringPlacementKind
    path_template: str
    prerequisite_targets: tuple[LiveHelpTarget, ...]
    catalog_collection: str
    canonical_identity_template: str


@dataclass(frozen=True)
class SemanticRepairContract:
    """Deterministic structured repair for one semantic authoring error kind."""

    error_kind: str
    kind: RepairKind
    help_target: LiveHelpTarget
    action: str
    snippet: str | None = None
    preserves_evidence: bool | None = None


@dataclass(frozen=True)
class SemanticTypeContract:
    """Stable public fields and flow edges for one semantic runtime type."""

    name: str
    producers: tuple[LiveHelpTarget, ...]
    public_properties: tuple[str, ...] = ()
    public_methods: tuple[str, ...] = ()
    consumers: tuple[LiveHelpTarget, ...] = ()


@dataclass(frozen=True)
class SemanticCapabilityRegistry:
    """Immutable lookup table for semantic live capability descriptors."""

    surface: Literal["semantic"]
    _descriptors: tuple[AuthoringCapability, ...]
    _groups: Mapping[SemanticRootGroup, tuple[str, ...]]
    _by_id: Mapping[str, AuthoringCapability]
    _by_callable_path: Mapping[str, AuthoringCapability]
    _source_contracts: Mapping[str, AuthoringSourceContract]
    _repair_contracts: Mapping[str, SemanticRepairContract]

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

    def group(self, group: SemanticRootGroup) -> tuple[AuthoringCapability, ...]:
        return tuple(self._by_id[canonical_id] for canonical_id in self._groups[group])

    def source_contract(self, canonical_id: str) -> AuthoringSourceContract | None:
        """Return source placement and handoff facts for one object constructor."""

        return self._source_contracts.get(canonical_id)

    def error_repair_contract(self, error_kind: str) -> SemanticRepairContract | None:
        """Return a deterministic repair template for one authoring error."""

        return self._repair_contracts.get(error_kind)
