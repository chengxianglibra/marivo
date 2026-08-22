"""Closed value model for agent-facing capability facts and repairs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from marivo.introspection.live.model import HelpSurface, LiveHelpTarget

DataAccessEffect = Literal[
    "none",
    "local_metadata_read",
    "live_metadata_read",
    "live_metadata_or_scoped_data_read",
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
    "scope_required_for_declared_data_checks",
    "may_publish_certified_artifact",
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


AuthoringInputRole = Literal[
    "receiver",
    "subject",
    "dependency",
    "scope",
    "evidence",
    "mapping_key",
]


class AuthoringInputRequirement(BaseModel):
    """Role-bound input fact for an agent-facing capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AuthoringInputRole
    family: str
    subject_refs: tuple[str, ...] = ()
    exact_keys: tuple[str, ...] = ()
    min_count: int = 1
    max_count: int | None = 1


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
    "environment",
    "user_choice",
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
    effects: AuthoringEffects | None = None
    constraints: tuple[str, ...] = ()
    minimal_example: str | None = None
    see_also: tuple[LiveHelpTarget, ...] = ()
    repair_kinds: tuple[RepairKind, ...] = ()

    @property
    def live_target(self) -> LiveHelpTarget:
        """Return the namespaced live help target for this capability."""
        return LiveHelpTarget(surface=self.surface, canonical_id=self.canonical_id)
