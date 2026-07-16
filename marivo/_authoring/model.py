"""Closed value model for the datasource-to-semantic authoring lifecycle."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from marivo.introspection.live.model import HelpSurface, LiveHelpTarget

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
    """Runtime-observable authoring state bound to subjects and evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: AuthoringStateId
    subject_refs: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


DataAccessEffect = Literal[
    "none",
    "local_metadata_read",
    "live_metadata_read",
    "scoped_data_read",
    "potentially_unbounded_read",
]
ConnectionEffect = Literal["none", "opens_connection"]
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
    """Closed orthogonal effect declaration for an authoring capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_access: DataAccessEffect
    connection: ConnectionEffect
    mutations: tuple[MutationEffect, ...] = ()
    flags: tuple[EffectFlag, ...] = ()


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
    """Role-bound input requirement for an authoring transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: TransitionInputRole
    family: str
    subject_refs: tuple[str, ...] = ()
    exact_keys: tuple[str, ...] = ()
    min_count: int = 1
    max_count: int | None = 1


class AuthoringTransition(BaseModel):
    """Mechanically available or blocked transition from current state."""

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
    """Mechanical continuation contract for one state-bearing value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_refs: tuple[str, ...]
    states: tuple[AuthoringStateRef, ...]
    transitions: tuple[AuthoringTransition, ...]


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
    """Closed typed repair shared by datasource and semantic values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: RepairKind
    help_target: LiveHelpTarget
    action: str
    snippet: str | None = None
    candidates: tuple[str, ...] = ()
    preserves_evidence: bool | None = None


AuthoringCapabilityKind = Literal[
    "callable",
    "method",
    "transition",
    "boundary",
    "recovery",
]


class AuthoringCapability(BaseModel):
    """One datasource or semantic authoring capability's closed fact set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_id: str
    kind: AuthoringCapabilityKind
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
        """Return the namespaced live help target for this capability."""
        return LiveHelpTarget(surface=self.surface, canonical_id=self.canonical_id)
