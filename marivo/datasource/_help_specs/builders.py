"""Closed registry and consumed-type catalog for ``marivo.datasource``."""

from __future__ import annotations

from marivo._authoring.model import (
    AuthoringCapability,
    AuthoringCapabilityKind,
    AuthoringEffects,
    AuthoringInputRequirement,
    AuthoringInputRole,
    RepairKind,
    authoring_effects,
)
from marivo.datasource._help_specs.inputs import datasource_inputs
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.targets import canonical_target


def _target(canonical_id: str) -> LiveHelpTarget:
    return LiveHelpTarget(surface="datasource", canonical_id=canonical_target(canonical_id))


def _inputs(
    *families: tuple[AuthoringInputRole, str],
) -> tuple[AuthoringInputRequirement, ...]:
    return tuple(AuthoringInputRequirement(role=role, family=family) for role, family in families)


def _optional_input(role: AuthoringInputRole, family: str) -> AuthoringInputRequirement:
    return AuthoringInputRequirement(role=role, family=family, min_count=0)


_NONE = authoring_effects()
_LOCAL = authoring_effects("local_metadata_read")
_TEST = authoring_effects(
    "local_metadata_read",
    "opens_connection",
    mutations=("user_global_state",),
    flags=("may_cache_resolved_secret",),
)


def _capability(
    canonical_id: str,
    callable_path: str | None,
    summary: str,
    *,
    kind: AuthoringCapabilityKind = "callable",
    output: str | None = None,
    inputs: tuple[AuthoringInputRequirement, ...] = (),
    effects: AuthoringEffects = _NONE,
    constraints: tuple[str, ...] = (),
    example: str | None = None,
    preconditions: tuple[str, ...] = (),
    repair_kinds: tuple[RepairKind, ...] = (),
    see_also: tuple[LiveHelpTarget, ...] = (),
    public_entrypoint: str | None = None,
) -> AuthoringCapability:
    return AuthoringCapability(
        canonical_id=canonical_target(canonical_id),
        kind=kind,
        surface="datasource",
        public_entrypoint=(public_entrypoint if callable_path is not None else None)
        or (f"md.{canonical_id}" if callable_path is not None else None),
        callable_path=callable_path,
        summary=summary,
        input_requirements=datasource_inputs(canonical_id, inputs),
        output_family=output,
        preconditions=preconditions,
        effects=effects,
        constraints=constraints,
        minimal_example=example,
        see_also=see_also,
        repair_kinds=repair_kinds,
    )
