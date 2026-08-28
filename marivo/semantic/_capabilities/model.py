"""Private semantic live-surface registry models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from marivo._authoring.model import AuthoringCapability, RepairKind
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.reflect import callable_identity
from marivo.refs import SemanticKind

SemanticRootGroup = Literal[
    "browse_load",
    "author_families",
    "runtime_probes",
    "readiness",
    "diagnostics_boundaries",
]
AuthoringPlacementKind = Literal["domain_entrypoint", "domain_module"]
SemanticHelpRenderClass = Literal[
    "root",
    "decision_hub",
    "navigation",
    "exact_contract",
    "current_briefing",
]


@dataclass(frozen=True)
class SemanticHelpRenderBudget:
    """Closed structural budget for one semantic Help page class."""

    max_lines: int
    max_codepoints: int
    max_outgoing_routes: int
    max_examples_or_snippets: int


SEMANTIC_HELP_RENDER_BUDGETS: Mapping[
    SemanticHelpRenderClass,
    SemanticHelpRenderBudget,
] = MappingProxyType(
    {
        "root": SemanticHelpRenderBudget(32, 3_000, 10, 0),
        "decision_hub": SemanticHelpRenderBudget(40, 4_000, 8, 0),
        "navigation": SemanticHelpRenderBudget(64, 6_000, 24, 0),
        "exact_contract": SemanticHelpRenderBudget(72, 7_000, 8, 1),
        "current_briefing": SemanticHelpRenderBudget(64, 6_000, 6, 1),
    }
)


@dataclass(frozen=True)
class SemanticNavigationTopic:
    """One non-invokable semantic decision or navigation page."""

    canonical_id: str
    summary: str
    members: tuple[LiveHelpTarget, ...]
    public_entrypoint: None = field(default=None, init=False)
    callable_path: None = field(default=None, init=False)


@dataclass(frozen=True)
class SemanticBuilderTopic:
    """One independently resolvable family of supporting value builders."""

    canonical_id: str
    label: str
    summary: str
    members: tuple[LiveHelpTarget, ...]
    public_entrypoint: None = field(default=None, init=False)
    callable_path: None = field(default=None, init=False)


@dataclass(frozen=True)
class SemanticCheckRoute:
    """One evidence question and its exact check routes and proof boundary."""

    question: str
    targets: tuple[LiveHelpTarget, ...]
    proves: str
    does_not_prove: str


@dataclass(frozen=True)
class SemanticCheckTopic:
    """One independently resolvable semantic check-routing page."""

    canonical_id: str
    summary: str
    routes: tuple[SemanticCheckRoute, ...]
    public_entrypoint: None = field(default=None, init=False)
    callable_path: None = field(default=None, init=False)


@dataclass(frozen=True)
class ConstructionMode:
    """One legal construction route for a semantic object kind."""

    intent: str
    role: Literal["default", "alternative", "escape_hatch"]
    target: LiveHelpTarget


@dataclass(frozen=True)
class SemanticObjectRelationship:
    """One typed relationship between a semantic object and another help target."""

    relation: Literal[
        "owned_by",
        "requires",
        "may_reference",
        "inferred_from",
        "consumed_by",
    ]
    target: LiveHelpTarget
    explanation: str


@dataclass(frozen=True)
class SemanticObjectDecision:
    """One material decision owned by a semantic object-kind contract."""

    decision_id: str
    question: str
    determine_from: str
    basis: Literal[
        "source_evidence",
        "business_authority",
        "source_and_business",
    ]
    encoding_status: Literal["supported", "unsupported"]
    next_targets: tuple[LiveHelpTarget, ...]
    does_not_establish: str | None = None
    unsupported_reason: str | None = None


@dataclass(frozen=True)
class SemanticObjectContract:
    """One non-invokable semantic object-kind disclosure contract."""

    canonical_id: str
    summary: str
    semantic_kind: SemanticKind
    ref_target: LiveHelpTarget
    catalog_collection: str
    placement_kind: AuthoringPlacementKind
    decisions: tuple[SemanticObjectDecision, ...]
    construction_modes: tuple[ConstructionMode, ...]
    relationships: tuple[SemanticObjectRelationship, ...]
    supporting_targets: tuple[LiveHelpTarget, ...]
    check_targets: tuple[LiveHelpTarget, ...]
    public_entrypoint: None = field(default=None, init=False)
    callable_path: None = field(default=None, init=False)


SemanticHelpDescriptor = (
    AuthoringCapability
    | SemanticNavigationTopic
    | SemanticBuilderTopic
    | SemanticCheckTopic
    | SemanticObjectContract
)


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
    _help_descriptors: tuple[SemanticHelpDescriptor, ...]
    _descriptors: tuple[AuthoringCapability, ...]
    _groups: Mapping[SemanticRootGroup, tuple[str, ...]]
    _by_id: Mapping[str, SemanticHelpDescriptor]
    _by_callable_path: Mapping[str, AuthoringCapability]
    _source_contracts: Mapping[str, AuthoringSourceContract]
    _repair_contracts: Mapping[str, SemanticRepairContract]
    _render_classes: Mapping[str, SemanticHelpRenderClass]
    _render_budgets: Mapping[SemanticHelpRenderClass, SemanticHelpRenderBudget]

    @property
    def help_descriptors(self) -> tuple[SemanticHelpDescriptor, ...]:
        """Return every currently resolvable static semantic Help descriptor."""

        return self._help_descriptors

    @property
    def descriptors(self) -> tuple[AuthoringCapability, ...]:
        """Return exact semantic capability descriptors only."""

        return self._descriptors

    def canonical_ids(self) -> tuple[str, ...]:
        return tuple(descriptor.canonical_id for descriptor in self._help_descriptors)

    def discovery_ids(self) -> tuple[str, ...]:
        """Return public capabilities that are not receiver-member drill-downs."""
        return tuple(
            descriptor.canonical_id
            for descriptor in self._help_descriptors
            if isinstance(descriptor, AuthoringCapability) and descriptor.kind != "method"
        )

    def callable_ids(self) -> tuple[str, ...]:
        return tuple(
            descriptor.canonical_id
            for descriptor in self._descriptors
            if descriptor.callable_path is not None
        )

    def by_canonical_id(self, canonical_id: str) -> SemanticHelpDescriptor:
        return self._by_id[canonical_id]

    def by_callable(self, obj: object) -> AuthoringCapability:
        return self._by_callable_path[callable_identity(obj)]

    def group(self, group: SemanticRootGroup) -> tuple[AuthoringCapability, ...]:
        members: list[AuthoringCapability] = []
        for canonical_id in self._groups[group]:
            descriptor = self._by_id[canonical_id]
            if not isinstance(descriptor, AuthoringCapability):
                raise TypeError(f"semantic root group member is not a capability: {canonical_id}")
            members.append(descriptor)
        return tuple(members)

    def source_contract(self, canonical_id: str) -> AuthoringSourceContract | None:
        """Return source placement and handoff facts for one object constructor."""

        return self._source_contracts.get(canonical_id)

    def error_repair_contract(self, error_kind: str) -> SemanticRepairContract | None:
        """Return a deterministic repair template for one authoring error."""

        return self._repair_contracts.get(error_kind)

    @property
    def render_budgets(
        self,
    ) -> Mapping[SemanticHelpRenderClass, SemanticHelpRenderBudget]:
        """Return semantic-owned immutable Help render budgets."""

        return self._render_budgets

    def render_budget(self, render_class: SemanticHelpRenderClass) -> SemanticHelpRenderBudget:
        """Return the registered budget for one semantic Help render class."""

        return self._render_budgets[render_class]

    def render_class(self, canonical_id: str) -> SemanticHelpRenderClass:
        """Return the registry-owned render class for one static descriptor."""

        return self._render_classes[canonical_id]
