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
    "start",
    "discover_authoring",
    "current_catalog",
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


@dataclass(frozen=True)
class SemanticRootSection:
    """One registry-owned section of the compact semantic root."""

    section_id: SemanticRootGroup
    label: str
    members: tuple[LiveHelpTarget, ...]


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
class SemanticNavigationRoute:
    """One labelled registry-owned route on a semantic navigation page."""

    label: str
    target: LiveHelpTarget
    summary: str | None = None
    owns_discovery: bool = True


@dataclass(frozen=True)
class SemanticObjectIndexEntry:
    """One object contract embedded directly in the object-kind index."""

    contract: SemanticObjectContract

    @property
    def label(self) -> str:
        return self.contract.semantic_kind.value

    @property
    def summary(self) -> str:
        return self.contract.summary

    @property
    def target(self) -> LiveHelpTarget:
        return LiveHelpTarget(surface="semantic", canonical_id=self.contract.canonical_id)

    @property
    def owns_discovery(self) -> bool:
        return True


@dataclass(frozen=True)
class SemanticNavigationTopic:
    """One non-invokable semantic decision or navigation page."""

    canonical_id: str
    summary: str
    members: tuple[SemanticNavigationRoute | SemanticObjectIndexEntry, ...]
    member_heading: str = "Choose by need"
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
    _root_sections: tuple[SemanticRootSection, ...]
    _by_id: Mapping[str, SemanticHelpDescriptor]
    _by_callable_path: Mapping[str, AuthoringCapability]
    _source_contracts: Mapping[str, AuthoringSourceContract]
    _repair_contracts: Mapping[str, SemanticRepairContract]
    _object_contracts: tuple[SemanticObjectContract, ...]
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

    @property
    def object_contracts(self) -> tuple[SemanticObjectContract, ...]:
        """Return registered Slice 2 object-kind contracts in teaching order."""

        return self._object_contracts

    def object_contract(self, kind: SemanticKind) -> SemanticObjectContract:
        """Return the exact registered contract for one semantic kind."""

        for contract in self._object_contracts:
            if contract.semantic_kind is kind:
                return contract
        raise KeyError(kind)

    def canonical_ids(self) -> tuple[str, ...]:
        return tuple(descriptor.canonical_id for descriptor in self._help_descriptors)

    def discovery_ids(self) -> tuple[str, ...]:
        """Return public capabilities that are not receiver-member drill-downs."""
        return tuple(
            descriptor.canonical_id
            for descriptor in self._help_descriptors
            if not isinstance(descriptor, AuthoringCapability) or descriptor.kind != "method"
        )

    def callable_ids(self) -> tuple[str, ...]:
        return tuple(
            descriptor.canonical_id
            for descriptor in self._descriptors
            if descriptor.callable_path is not None
        )

    @property
    def root_sections(self) -> tuple[SemanticRootSection, ...]:
        """Return compact semantic-root sections in teaching order."""

        return self._root_sections

    def by_canonical_id(self, canonical_id: str) -> SemanticHelpDescriptor:
        return self._by_id[canonical_id]

    def by_callable(self, obj: object) -> AuthoringCapability:
        return self._by_callable_path[callable_identity(obj)]

    def routes(self, canonical_id: str) -> tuple[LiveHelpTarget, ...]:
        """Return all registry-owned outgoing routes for one static descriptor."""

        descriptor = self._by_id[canonical_id]
        routes: tuple[LiveHelpTarget, ...]
        if isinstance(descriptor, SemanticNavigationTopic):
            routes = tuple(member.target for member in descriptor.members)
        elif isinstance(descriptor, SemanticBuilderTopic):
            routes = descriptor.members
        elif isinstance(descriptor, SemanticCheckTopic):
            routes = tuple(target for route in descriptor.routes for target in route.targets)
        elif isinstance(descriptor, SemanticObjectContract):
            routes = (
                descriptor.ref_target,
                *(mode.target for mode in descriptor.construction_modes),
                *(relationship.target for relationship in descriptor.relationships),
                *descriptor.supporting_targets,
                *descriptor.check_targets,
                *(target for decision in descriptor.decisions for target in decision.next_targets),
            )
        else:
            routes = descriptor.see_also
        return tuple(dict.fromkeys(routes))

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
