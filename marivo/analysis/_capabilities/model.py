"""Closed capability kernel models for ``marivo.analysis``.

This module defines the descriptor union, closed family/group vocabulary,
and the single ``SURFACE_LIMITS`` value consumed by all later tasks
(registry, resolver, renderer, family gate, and evaluation).

All names are private to ``marivo.analysis``.  Nothing is added to
``marivo/analysis/__init__.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Closed vocabulary: capability kinds, visibility, groups, families
# ---------------------------------------------------------------------------

CapabilityKind = Literal["operator", "constructor", "read", "recovery", "boundary"]

RootVisibility = Literal["direct", "grouped"]

RootGroup = Literal[
    "semantic_inputs",
    "policies_builders",
    "artifact_production",
    "typed_analysis",
    "family_operations",
    "artifact_inspection",
    "recovery",
    "boundaries",
]

ArtifactFamily = Literal[
    "MetricFrame",
    "EventFrame",
    "LifecycleFrame",
    "SubjectSet",
    "DeltaFrame",
    "AttributionFrame",
    "ForecastFrame",
    "QualityReport",
    "CandidateSet",
    "AssociationResult",
    "ComponentFrame",
    "CoverageFrame",
    "HypothesisTestResult",
]

InputFamily = (
    ArtifactFamily
    | Literal[
        "MetricSemantic",
        "RuntimeMetricExpression",
        "DimensionSemantic",
        "TimeDimensionSemantic",
        "SemanticProject",
        "AlignmentPolicy",
        "SamplingPolicy",
        "TimeScopeInput",
        "EventPattern",
        "EventMatchingPolicy",
        "CompletenessDeclaration",
        "StateModelSemantic",
        "LifecycleSeed",
        "SubjectSelection",
        "FunnelLossRate",
        "OntologyMetricCandidate",
    ]
)


# ---------------------------------------------------------------------------
# SameAsInputFamily — output family that mirrors a named input parameter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SameAsInputFamily:
    """Output family marker that mirrors a named input parameter's family.

    Parameters
    ----------
    parameter:
        Name of the accepted-input parameter whose family the output family
        mirrors at runtime.
    """

    parameter: str


OutputFamily = ArtifactFamily | SameAsInputFamily


@dataclass(frozen=True)
class ArtifactOutputContract:
    """Closed static facts about an artifact-producing capability."""

    family: OutputFamily
    semantic_shapes: frozenset[str] = field(default_factory=frozenset)
    matching_kinds: frozenset[str] = field(default_factory=frozenset)
    nullable: bool = False

    def render(self) -> str:
        """Render the bounded output type used by help and type algebra."""

        family = (
            f"same as {self.family.parameter}"
            if isinstance(self.family, SameAsInputFamily)
            else self.family
        )
        qualifiers: list[str] = []
        if self.semantic_shapes:
            qualifiers.append("|".join(sorted(self.semantic_shapes)))
        if self.matching_kinds:
            qualifiers.append(f"matching={'|'.join(sorted(self.matching_kinds))}")
        rendered = family if not qualifiers else f"{family}[{'; '.join(qualifiers)}]"
        return f"{rendered} | None" if self.nullable else rendered


@dataclass(frozen=True)
class ArtifactAdmissionRule:
    """Closed runtime predicates for one artifact-bearing parameter.

    The accepted family remains owned by ``accepted_inputs``. These optional
    predicates narrow that family by persisted artifact facts without
    duplicating operator-specific planning.
    """

    semantic_shapes: Mapping[ArtifactFamily, frozenset[str]] = field(default_factory=dict)
    matching_kinds: Mapping[ArtifactFamily, frozenset[str]] = field(default_factory=dict)
    coverage_statuses: Mapping[ArtifactFamily, frozenset[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Capability descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HelpExample:
    """One bounded, labeled additional focused-help example."""

    label: str
    code: str
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityBase:
    """Shared identity fields carried by every capability variant.

    Parameters
    ----------
    id:
        Canonical stable capability identifier.
    public_entrypoint:
        Exact public invocation shape (e.g. ``session.observe(...)``).
    help_target:
        Canonical target accepted by ``marivo.help("analysis.<target>")``.
    summary:
        One bounded factual sentence describing the capability.
    root_group:
        Exactly one teaching-order group from :data:`ROOT_GROUP_ORDER`.
    root_visibility:
        ``"direct"`` for standalone root entries, ``"grouped"`` for entries
        rendered under a grouping topic.
    root_summary:
        Optional shorter orientation text used only by root help. Focused help
        always renders ``summary``.
    constraint_ids:
        Links into the live constraint catalog.
    callable_path:
        Dotted import path to the registered callable, when applicable.
    """

    id: str
    public_entrypoint: str
    help_target: str
    summary: str
    root_group: RootGroup
    root_visibility: RootVisibility
    root_summary: str | None = None
    constraint_ids: tuple[str, ...] = ()
    callable_path: str | None = None
    additional_examples: tuple[HelpExample, ...] = ()

    @property
    def canonical_id(self) -> str:
        """Return the stable native capability identifier."""
        return self.id


@dataclass(frozen=True)
class OperatorCapability(CapabilityBase):
    """Artifact-producing operation.

    Parameters
    ----------
    receiver:
        Session, artifact namespace, or frame namespace that hosts the
        callable.
    accepted_inputs:
        Mapping from public parameter name to the closed set of accepted
        input families.
    output_contract:
        Closed family and any statically known artifact shape facts.
    """

    kind: Literal["operator"] = "operator"
    receiver: str = ""
    accepted_inputs: Mapping[str, frozenset[InputFamily]] = field(default_factory=dict)
    artifact_admission: Mapping[str, ArtifactAdmissionRule] = field(default_factory=dict)
    output_contract: ArtifactOutputContract = field(
        default_factory=lambda: ArtifactOutputContract(family="MetricFrame")
    )

    @property
    def output_family(self) -> OutputFamily:
        """Return the output family for family-only internal consumers."""

        return self.output_contract.family


@dataclass(frozen=True)
class ConstructorCapability(CapabilityBase):
    """Public value, policy, or governed-query builder.

    Parameters
    ----------
    output_type:
        One precise public value type produced by the constructor.
    produced_input_family:
        Closed analysis input family satisfied by the constructed value, when
        the value is accepted directly by an operator.
    """

    kind: Literal["constructor"] = "constructor"
    output_type: str = ""
    produced_input_family: InputFamily | None = None


@dataclass(frozen=True)
class ReadCapability(CapabilityBase):
    """Bounded, non-mutating read.

    Parameters
    ----------
    receiver_family:
        Artifact family or session type that owns the read method.
    result_kind:
        ``"terminal_text"`` for stdout/text exits,
        ``"immutable_metadata"`` for bounded metadata,
        ``"defensive_copy"`` for data copies.
    read_bound:
        ``"bounded"`` for normal reads, ``"terminal"`` for exits that cross
        the typed-flow boundary.
    """

    kind: Literal["read"] = "read"
    receiver_family: str = ""
    result_kind: Literal["terminal_text", "immutable_metadata", "defensive_copy"] = (
        "immutable_metadata"
    )
    read_bound: Literal["bounded", "terminal"] = "bounded"
    produced_input_family: InputFamily | None = None


@dataclass(frozen=True)
class RecoveryCapability(CapabilityBase):
    """Restoration of persisted state.

    Parameters
    ----------
    identity_input:
        Session name, job id, frame ref, or artifact id used to locate the
        persisted state.
    restored_family:
        Artifact family restored by the recovery operation.
    query_behavior:
        ``"none"`` when no datasource query is needed,
        ``"explicit"`` when the caller must provide a scoped query.
    """

    kind: Literal["recovery"] = "recovery"
    identity_input: str = ""
    restored_family: str = ""
    query_behavior: Literal["none", "explicit"] = "none"


@dataclass(frozen=True)
class BoundaryCapability(CapabilityBase):
    """Typed-flow boundary crossing.

    Parameters
    ----------
    direction:
        ``"terminal_exit"`` for exits out of typed Marivo continuity,
        ``"governed_entry"`` for explicit re-entry into typed artifacts.
    accepted_inputs:
        Mapping from public parameter name to the closed set of accepted
        source families.
    output_family:
        Target family, including external terminal types such as
        ``pandas.DataFrame``.
    preserves:
        Guarantees retained across the boundary.
    does_not_preserve:
        Guarantees the caller must not assume after crossing.
    """

    kind: Literal["boundary"] = "boundary"
    direction: Literal["terminal_exit", "governed_entry"] = "terminal_exit"
    accepted_inputs: Mapping[str, frozenset[InputFamily]] = field(default_factory=dict)
    output_family: str = ""
    preserves: tuple[str, ...] = ()
    does_not_preserve: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Descriptor union
# ---------------------------------------------------------------------------

CapabilityDescriptor = (
    OperatorCapability
    | ConstructorCapability
    | ReadCapability
    | RecoveryCapability
    | BoundaryCapability
)


# ---------------------------------------------------------------------------
# Teaching-order and family vocabulary constants
# ---------------------------------------------------------------------------

ROOT_GROUP_ORDER: tuple[RootGroup, ...] = (
    "semantic_inputs",
    "policies_builders",
    "artifact_production",
    "typed_analysis",
    "family_operations",
    "artifact_inspection",
    "recovery",
    "boundaries",
)

ARTIFACT_FAMILIES: tuple[ArtifactFamily, ...] = (
    "MetricFrame",
    "EventFrame",
    "LifecycleFrame",
    "SubjectSet",
    "DeltaFrame",
    "AttributionFrame",
    "ForecastFrame",
    "QualityReport",
    "CandidateSet",
    "AssociationResult",
    "ComponentFrame",
    "CoverageFrame",
    "HypothesisTestResult",
)


# ---------------------------------------------------------------------------
# Neutral help identities and boundary schemas are imported from their private
# owners for analysis-internal annotations only.
# ---------------------------------------------------------------------------
