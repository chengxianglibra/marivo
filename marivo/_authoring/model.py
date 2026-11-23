"""Closed value model for agent-facing capability facts and repairs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, get_args

from pydantic import BaseModel, ConfigDict

from marivo._kernel.errors import StructuredRepair
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
    "scope_required_for_declared_data_checks",
    "may_persist_plaintext_values",
    "may_cache_resolved_secret",
    "may_call_model",
    "may_write_search_cache",
]


class AuthoringEffects(BaseModel):
    """Closed orthogonal effect declaration for an authoring capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_access: DataAccessEffect
    connection: ConnectionEffect
    mutations: tuple[MutationEffect, ...] = ()
    flags: tuple[EffectFlag, ...] = ()


def authoring_effects(
    data_access: DataAccessEffect = "none",
    connection: ConnectionEffect = "none",
    mutations: tuple[MutationEffect, ...] = (),
    flags: tuple[EffectFlag, ...] = (),
) -> AuthoringEffects:
    """Declare authoring effects with the project-state effect of a connection."""
    if connection == "opens_connection" and "project_state" not in mutations:
        mutations = (*mutations, "project_state")
    return AuthoringEffects(
        data_access=data_access, connection=connection, mutations=mutations, flags=flags
    )


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
    parameter_names: tuple[str, ...] = ()
    description: str | None = None
    acquisition: tuple[LiveHelpTarget, ...] = ()
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


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthoringRepair(StructuredRepair[RepairKind]):
    """Closed typed repair shared by datasource and semantic values."""

    kinds: ClassVar[tuple[str, ...]] = get_args(RepairKind)

    preserves_evidence: bool | None = None

    def __post_init__(self) -> None:
        StructuredRepair.__post_init__(self)
        if self.preserves_evidence is not None and not isinstance(self.preserves_evidence, bool):
            raise ValueError("preserves_evidence must be a bool or None")

    def model_dump(self, *, mode: Literal["python", "json"] = "python") -> dict[str, object]:
        return {
            **StructuredRepair.model_dump(self, mode=mode),
            "preserves_evidence": self.preserves_evidence,
        }


AuthoringCapabilityKind = Literal[
    "callable",
    "method",
    "boundary",
    "recovery",
]
AuthoringInvocationShape = Literal["direct", "decorator"]


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
    guidance: tuple[str, ...] = ()
    effects: AuthoringEffects | None = None
    constraints: tuple[str, ...] = ()
    minimal_example: str | None = None
    see_also: tuple[LiveHelpTarget, ...] = ()
    repair_kinds: tuple[RepairKind, ...] = ()
    invocation_shape: AuthoringInvocationShape = "direct"
    telemetry: bool = True

    @property
    def live_target(self) -> LiveHelpTarget:
        """Return the namespaced live help target for this capability."""
        return LiveHelpTarget(surface=self.surface, canonical_id=self.canonical_id)
