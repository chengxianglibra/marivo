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

from pydantic import BaseModel, ConfigDict

from marivo.refs import SemanticRef
from marivo.semantic.catalog import SemanticKind

# ---------------------------------------------------------------------------
# Closed vocabulary: capability kinds, visibility, groups, families
# ---------------------------------------------------------------------------

CapabilityKind = Literal["operator", "constructor", "read", "recovery", "boundary"]

RootVisibility = Literal["direct", "grouped"]

RootGroup = Literal[
    "session_state",
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
        "DimensionSemantic",
        "TimeDimensionSemantic",
        "SemanticProject",
        "AlignmentPolicy",
        "SamplingPolicy",
        "TimeScopeInput",
        "IbisQuerySpec",
        "MetricColumns",
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


# ---------------------------------------------------------------------------
# Capability descriptors
# ---------------------------------------------------------------------------


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
        Canonical target accepted by ``mv.help(...)``.
    summary:
        One bounded factual sentence describing the capability.
    root_group:
        Exactly one teaching-order group from :data:`ROOT_GROUP_ORDER`.
    root_visibility:
        ``"direct"`` for standalone root entries, ``"grouped"`` for entries
        rendered under a grouping topic.
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
    constraint_ids: tuple[str, ...] = ()
    callable_path: str | None = None


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
    output_family:
        One canonical artifact family (or :class:`SameAsInputFamily`).
    """

    kind: Literal["operator"] = "operator"
    receiver: str = ""
    accepted_inputs: Mapping[str, frozenset[InputFamily]] = field(default_factory=dict)
    output_family: OutputFamily = "MetricFrame"


@dataclass(frozen=True)
class ConstructorCapability(CapabilityBase):
    """Public value, policy, or governed-query builder.

    Parameters
    ----------
    output_type:
        One precise public value type produced by the constructor.
    """

    kind: Literal["constructor"] = "constructor"
    output_type: str = ""


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
    "session_state",
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
# Surface limits — single private immutable value
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceLimits:
    """Numeric interface and evaluation limits for the help surface.

    Renderers, validators, repository tests, and the cold-agent scorer
    import :data:`SURFACE_LIMITS` instead of repeating literals.
    """

    root_help_max_lines: int = 80
    root_help_max_codepoints: int = 8_000
    focused_help_max_lines: int = 120
    focused_help_max_codepoints: int = 12_000
    object_contract_max_subjects: int = 8
    object_contract_render_max_lines: int = 120
    object_contract_render_max_codepoints: int = 12_000
    help_suggestion_limit: int = 5
    cold_agent_trials_per_case: int = 3
    cold_agent_min_qualifying_trials: int = 2
    cold_agent_max_help_calls_before_observe: int = 2
    cold_agent_max_invalid_api_errors_before_observe: int = 1


SURFACE_LIMITS = SurfaceLimits()


# ---------------------------------------------------------------------------
# Handoff and environment models
# ---------------------------------------------------------------------------

HelpSurface = Literal["analysis", "datasource", "semantic"]


class EnvironmentFingerprint(BaseModel):
    """Snapshot of the runtime environment for cross-layer handoffs.

    Parameters
    ----------
    marivo_version:
        Installed Marivo package version string.
    python_executable:
        Path to the Python interpreter running the session.
    package_path:
        Filesystem path to the installed ``marivo`` package root.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    marivo_version: str
    python_executable: str
    package_path: str


class LiveHelpTarget(BaseModel):
    """Typed target for a live help lookup across surfaces.

    Parameters
    ----------
    surface:
        Which help surface to consult (``analysis``, ``datasource``,
        ``semantic``).
    canonical_id:
        Canonical symbol or capability id to look up, when applicable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    surface: HelpSurface
    canonical_id: str | None = None

    @property
    def display(self) -> str:
        """Return the display string for rendering in help and repair text."""
        if self.canonical_id is not None:
            return self.canonical_id
        return self.surface


class AnalysisToSemanticHandoff(BaseModel):
    """Typed request from analysis to the semantic layer.

    Parameters
    ----------
    required_kind:
        Semantic kind the analysis layer needs, or ``None`` when the
        requirement is open-ended.
    requirement:
        Human-readable description of what the semantic layer must provide.
    affected_capability_id:
        Capability descriptor id whose preconditions triggered the handoff.
    environment_fingerprint:
        Snapshot of the calling environment for continuity.
    semantic_context_refs:
        Semantic refs relevant to the handoff.
    artifact_refs:
        Analysis artifact refs relevant to the handoff.
    evidence_refs:
        Evidence ids relevant to the handoff.
    project_fingerprint:
        Fingerprint of the project root, when available.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_kind: SemanticKind | None
    requirement: str
    affected_capability_id: str
    environment_fingerprint: EnvironmentFingerprint
    semantic_context_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    project_fingerprint: str | None = None


class SemanticToAnalysisHandoff(BaseModel):
    """Typed response from the semantic layer back to analysis.

    Parameters
    ----------
    help_target:
        Live help target the analysis agent should consult for details.
    ready_refs:
        Semantic refs that are now ready for analysis consumption.
    project_fingerprint:
        Fingerprint of the project root.
    catalog_fingerprint:
        Fingerprint of the semantic catalog state.
    environment_fingerprint:
        Snapshot of the semantic-side environment for continuity.
    readiness_status:
        ``"ready"`` or ``"ready_with_warnings"``.
    warning_ids:
        Identifiers for any warnings raised during semantic preparation.
    preview_evidence_ids:
        Evidence ids from preview runs during semantic preparation.
    caveats:
        Human-readable caveats about the handed-off semantic state.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    help_target: LiveHelpTarget
    ready_refs: tuple[SemanticRef, ...]
    project_fingerprint: str
    catalog_fingerprint: str
    environment_fingerprint: EnvironmentFingerprint
    readiness_status: Literal["ready", "ready_with_warnings"]
    warning_ids: tuple[str, ...] = ()
    preview_evidence_ids: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()


class SemanticHandoffReceipt(BaseModel):
    """Receipt confirming a semantic handoff was validated and accepted.

    Parameters
    ----------
    ready_refs:
        Semantic refs that were validated and are ready for consumption.
    project_fingerprint:
        Fingerprint of the project root.
    catalog_fingerprint:
        Fingerprint of the semantic catalog state.
    environment_fingerprint:
        Snapshot of the environment at validation time.
    readiness_status:
        ``"ready"`` or ``"ready_with_warnings"``.
    warning_ids:
        Identifiers for any warnings raised during validation.
    preview_evidence_ids:
        Evidence ids from preview runs associated with the handoff.
    caveats:
        Human-readable caveats about the validated semantic state.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ready_refs: tuple[SemanticRef, ...]
    project_fingerprint: str
    catalog_fingerprint: str
    environment_fingerprint: EnvironmentFingerprint
    readiness_status: Literal["ready", "ready_with_warnings"]
    warning_ids: tuple[str, ...] = ()
    preview_evidence_ids: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
