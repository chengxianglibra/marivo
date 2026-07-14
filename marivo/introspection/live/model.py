"""Shared live-authoring models for the neutral introspection surface.

Relocated from ``marivo/analysis/_capabilities/model.py`` so the datasource and
semantic surfaces can consume them without importing ``marivo.analysis``.
The analysis kernel re-imports these names for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from marivo.refs import SemanticRef, SymbolKind

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
# Environment and live help target models
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

    @classmethod
    def current(cls) -> EnvironmentFingerprint:
        """Construct from the current runtime environment."""
        import sys
        from pathlib import Path

        import marivo

        return cls(
            marivo_version=marivo.__version__,
            python_executable=str(Path(sys.executable).resolve()),
            package_path=str(Path(marivo.__file__).resolve()),
        )


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


# ---------------------------------------------------------------------------
# Directional handoff schemas and receipt
# ---------------------------------------------------------------------------


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

    required_kind: SymbolKind | None
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


# ---------------------------------------------------------------------------
# Authoring state families
# ---------------------------------------------------------------------------

AuthoringStateId = Literal[
    "datasource.declared",
    "datasource.registered",
    "datasource.connection_validated",
    "source.inspected",
    "scope.explicit",
    "evidence.acquired",
    "evidence.projected",
    "semantic.loaded",
    "semantic.verified",
    "semantic.previewed",
    "semantic.ready",
]


class AuthoringStateRef(BaseModel):
    """A runtime-observable authoring state bound to subject and evidence ids.

    Parameters
    ----------
    id:
        Closed authoring state identifier.
    subject_refs:
        Typed subject identities the state is bound to.
    evidence_ids:
        Evidence identities the state is bound to.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: AuthoringStateId
    subject_refs: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Orthogonal effect contract
# ---------------------------------------------------------------------------

DataAccessEffect = Literal[
    "none",
    "local_metadata_read",
    "live_metadata_read",
    "scoped_data_read",
    "potentially_unbounded_read",
]

ConnectionEffect = Literal[
    "none",
    "opens_connection",
]

MutationEffect = Literal[
    "project_state",
    "semantic_source",
    "user_global_state",
]

EffectFlag = Literal[
    "requires_explicit_scope",
    "requires_positive_row_guard",
    "requires_positive_timeout_guard",
    "requires_existing_snapshot_binding",
    "may_persist_plaintext_values",
    "may_cache_resolved_secret",
]


class AuthoringEffects(BaseModel):
    """Closed orthogonal effect declaration for one capability or boundary.

    Data access, connection creation, and mutation are independent axes. A
    capability declares one closed value on each axis rather than choosing one
    lossy primary effect.

    Parameters
    ----------
    data_access:
        What data, if any, the capability reads.
    connection:
        Whether the capability opens a live datasource connection.
    mutations:
        State mutations the capability may write.
    flags:
        Additional guard/privacy flags that apply to the capability.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_access: DataAccessEffect
    connection: ConnectionEffect
    mutations: tuple[MutationEffect, ...] = ()
    flags: tuple[EffectFlag, ...] = ()


# ---------------------------------------------------------------------------
# Typed transition contract
# ---------------------------------------------------------------------------

TransitionKind = Literal[
    "declare",
    "register",
    "validate_connection",
    "inspect",
    "scope",
    "acquire",
    "project_evidence",
    "load",
    "reload",
    "verify",
    "preview",
    "readiness",
    "analysis_handoff",
]

TransitionInputRole = Literal[
    "receiver",
    "subject",
    "dependency",
    "scope",
    "evidence",
    "mapping_key",
]


class AuthoringInputRequirement(BaseModel):
    """A role-bound input requirement for a transition.

    Parameters
    ----------
    role:
        Closed role the input plays (receiver, subject, dependency, scope,
        evidence, mapping_key).
    family:
        Registered public family the input must belong to.
    subject_refs:
        Subject identities the input must be bound to, when applicable.
    exact_keys:
        Exact mapping keys required, when applicable.
    min_count:
        Minimum number of inputs in this role.
    max_count:
        Maximum number of inputs in this role, or ``None`` for unbounded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: TransitionInputRole
    family: str
    subject_refs: tuple[str, ...] = ()
    exact_keys: tuple[str, ...] = ()
    min_count: int = 1
    max_count: int | None = 1


class AuthoringTransition(BaseModel):
    """One mechanically available (or blocked) transition from current state.

    Parameters
    ----------
    kind:
        Closed transition kind.
    help_target:
        Namespaced live help target for the transition's owning capability.
    subject_refs:
        Subject identities the transition is bound to.
    required_states:
        Authoring states that must hold for the transition to be available.
    produced_state:
        Authoring state the transition produces, if any.
    effects:
        Complete orthogonal effect metadata for the transition.
    available:
        Whether the transition is mechanically available from current state.
    input_requirements:
        Role-bound input requirements for invoking the transition.
    blocked_by:
        Canonical blocker ids when ``available`` is False and the blocker is
        repairable from current state.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: TransitionKind
    help_target: LiveHelpTarget
    subject_refs: tuple[str, ...]
    required_states: tuple[AuthoringStateRef, ...] = ()
    produced_state: AuthoringStateRef | None = None
    effects: AuthoringEffects
    available: bool
    input_requirements: tuple[AuthoringInputRequirement, ...] = ()
    blocked_by: tuple[str, ...] = ()


class AuthoringContract(BaseModel):
    """Mechanical continuation contract for one state-bearing object/result.

    Parameters
    ----------
    subject_refs:
        Subject identities the contract is scoped to.
    states:
        Current authoring states bound to the subject.
    transitions:
        Mechanically relevant transitions in deterministic order.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_refs: tuple[str, ...]
    states: tuple[AuthoringStateRef, ...]
    transitions: tuple[AuthoringTransition, ...]


# ---------------------------------------------------------------------------
# Typed repair contract
# ---------------------------------------------------------------------------

RepairKind = Literal[
    "retry",
    "configure",
    "register",
    "reconnect",
    "inspect",
    "rescope",
    "reacquire",
    "reauthor",
    "reload",
    "reverify",
    "repreview",
    "environment",
]


class AuthoringRepair(BaseModel):
    """Closed typed repair shared by datasource and semantic errors/results.

    Parameters
    ----------
    kind:
        Closed repair kind.
    help_target:
        Namespaced live help target for the repair's owning capability.
    action:
        Concrete next step the agent takes.
    snippet:
        Optional runnable snippet, without ellipsis placeholders.
    candidates:
        Bounded live candidate identities, when the repair is a selection.
    preserves_evidence:
        ``True`` if existing evidence remains valid after repair, ``False`` if
        dependent preview/readiness checks must rerun, ``None`` if the repair
        does not touch datasource evidence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: RepairKind
    help_target: LiveHelpTarget
    action: str
    snippet: str | None = None
    candidates: tuple[str, ...] = ()
    preserves_evidence: bool | None = None


# ---------------------------------------------------------------------------
# Registry descriptor contract
# ---------------------------------------------------------------------------

LiveCapabilityKind = Literal[
    "callable",
    "method",
    "transition",
    "boundary",
    "recovery",
]


class LiveCapability(BaseModel):
    """One registered capability's closed fact set.

    Every registered datasource/semantic capability provides these facts. The
    registry is descriptive, not executable orchestration: it states what is
    mechanically available and required, never which call to choose.

    Parameters
    ----------
    canonical_id:
        Canonical id mirroring the public invocation shape, without ``md.``/
        ``ms.`` prefixes inside its owning surface.
    kind:
        Closed capability kind.
    surface:
        Owning help surface.
    public_entrypoint:
        Real public entrypoint string (e.g. ``catalog.preview``), when the
        capability is callable or a method.
    callable_path:
        Dotted import path to the live callable, for signature derivation.
    summary:
        One-line factual summary.
    input_requirements:
        Role-bound input requirements for invoking the capability.
    output_family:
        Registered output family name.
    preconditions:
        Canonical precondition ids.
    produced_state:
        Authoring state the capability produces, if any.
    required_states:
        Authoring states required for the capability to be available.
    effects:
        Complete orthogonal effect metadata.
    constraints:
        Canonical constraint ids that apply.
    minimal_example:
        One runnable minimal example without ellipsis placeholders.
    see_also:
        Related namespaced live help targets.
    repair_kinds:
        Closed repair kinds this capability's failures may produce.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_id: str
    kind: LiveCapabilityKind
    surface: HelpSurface
    public_entrypoint: str | None = None
    callable_path: str | None = None
    summary: str
    input_requirements: tuple[AuthoringInputRequirement, ...] = ()
    output_family: str | None = None
    preconditions: tuple[str, ...] = ()
    produced_state: AuthoringStateRef | None = None
    required_states: tuple[AuthoringStateRef, ...] = ()
    effects: AuthoringEffects | None = None
    constraints: tuple[str, ...] = ()
    minimal_example: str | None = None
    see_also: tuple[LiveHelpTarget, ...] = ()
    repair_kinds: tuple[RepairKind, ...] = ()

    @property
    def live_target(self) -> LiveHelpTarget:
        """Namespaced live help target for this capability."""
        return LiveHelpTarget(surface=self.surface, canonical_id=self.canonical_id)


class LiveSurfaceRegistry(Protocol):
    """Read-only contract for a surface's capability registry.

    Implementations (datasource in Phase 2, semantic in Phase 3) build a closed
    set of :class:`LiveCapability` descriptors and expose these lookups. All
    lookups raise ``KeyError`` on miss.
    """

    surface: HelpSurface

    def canonical_ids(self) -> tuple[str, ...]:
        """All canonical ids in deterministic registry order."""
        ...

    def by_canonical_id(self, canonical_id: str) -> LiveCapability:
        """Look up a capability by canonical id."""
        ...

    def by_callable(self, obj: object) -> LiveCapability:
        """Look up a capability by its registered callable or bound method."""
        ...
