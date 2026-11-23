"""Build canonical Semantic capability and input specifications."""

from __future__ import annotations

from typing import Literal

from marivo._authoring.model import (
    AuthoringCapability,
    AuthoringCapabilityKind,
    AuthoringEffects,
    AuthoringInputRequirement,
    AuthoringInputRole,
    RepairKind,
    authoring_effects,
)
from marivo._kernel.refs import SemanticKind
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.targets import canonical_target, semantic_target
from marivo.semantic._help_specs.inputs import authoring_guidance, authoring_inputs


def _target(canonical_id: str) -> LiveHelpTarget:
    return LiveHelpTarget(surface="semantic", canonical_id=semantic_target(canonical_id))


def _parameter_input(
    role: AuthoringInputRole,
    family: str,
    *parameter_names: str,
    optional: bool = False,
) -> AuthoringInputRequirement:
    return AuthoringInputRequirement(
        role=role,
        family=family,
        parameter_names=parameter_names,
        min_count=0 if optional else 1,
    )


_NONE = authoring_effects()
_LOCAL = authoring_effects("local_metadata_read")
_AUTHOR = authoring_effects(mutations=("semantic_source",))
_PREVIEW = authoring_effects(
    "scoped_data_read",
    "opens_connection",
    flags=(
        "requires_explicit_scope",
        "requires_positive_row_guard",
    ),
)
_VALIDATE = authoring_effects(
    "live_metadata_or_scoped_data_read",
    "opens_connection",
    flags=("scope_required_for_declared_data_checks",),
)
_ADDITIVITY_HELP = (
    "\n  additivity: ms.additive_all(), ms.additive_except(...), ms.additive_over(...), or ms.non_additive()."
    "\n  unit=None leaves the declared unit unspecified."
)
_FOLD_HELP = "\n  Temporal sample reduction is explicit in per(time_field).reducer(); leaf default folds are retired."


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
    invocation_shape: Literal["direct", "decorator"] = "direct",
    telemetry: bool = True,
) -> AuthoringCapability:
    if canonical_id in {
        "entity",
        "dimension",
        "dimension_column",
        "measure_column",
        "time_dimension",
        "time_dimension_column",
        "relationship",
    }:
        summary += (
            " Capture returns a native typed Deferred symbol. Compose it directly; the loader binds "
            "the complete dependency scope before Catalog publication."
        )
    return AuthoringCapability(
        canonical_id=semantic_target(canonical_id),
        kind=kind,
        surface="semantic",
        public_entrypoint=public_entrypoint
        or (f"ms.{canonical_id}" if callable_path is not None else None),
        callable_path=callable_path,
        summary=summary,
        input_requirements=authoring_inputs(canonical_id, inputs),
        output_family=output,
        preconditions=preconditions,
        guidance=authoring_guidance(canonical_id),
        effects=effects,
        constraints=constraints,
        minimal_example=example,
        see_also=see_also,
        repair_kinds=repair_kinds,
        invocation_shape=invocation_shape,
        telemetry=telemetry,
    )


def _symbol_family(kind: SemanticKind) -> str:
    return "".join(part.title() for part in kind.value.split("_")) + "Symbol"


def _canonical_link(target: LiveHelpTarget) -> LiveHelpTarget:
    if target.canonical_id is None:
        return target
    if target.surface == "semantic":
        return _target(target.canonical_id)
    return LiveHelpTarget(
        surface=target.surface, canonical_id=canonical_target(target.canonical_id)
    )
