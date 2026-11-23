"""Semantic-owned live help target resolution inputs."""

from __future__ import annotations

from dataclasses import replace
from typing import NoReturn

from marivo._authoring.model import AuthoringCapability
from marivo._kernel.refs import ref as ref_factory
from marivo.introspection.capabilities.provider import SurfaceRenderer, adapt_surface, bind_provider
from marivo.introspection.capabilities.resolve import ResolvedLiveTarget
from marivo.introspection.live.errors import build_help_target_error_payload
from marivo.semantic._help_specs.manifest import ERROR_TYPES, REGISTRY
from marivo.semantic._help_specs.model import SemanticHelpDescriptor, SemanticObjectContract
from marivo.semantic._help_specs.render import (
    render_descriptor,
    render_error_contract,
    render_root_help,
    render_type,
)
from marivo.semantic._help_specs.type_specs import TYPE_CONTRACTS
from marivo.semantic.errors import SemanticHelpTargetError
from marivo.semantic.runtime_check.source_checks import source_check


def _help_target_error(target: object, suggestions: tuple[str, ...]) -> NoReturn:
    owner = _cross_surface_owner(target)
    payload = build_help_target_error_payload(
        target,
        surface="semantic",
        candidates=suggestions,
    )
    if owner is not None:
        payload = replace(
            payload,
            message=(
                f"{payload.message} This target belongs to {owner}; "
                f'use marivo.help("{owner}.<target>").'
            ),
        )
    raise SemanticHelpTargetError(payload)


def _cross_surface_owner(target: object) -> str | None:
    callable_target = getattr(target, "__func__", target)
    module = getattr(callable_target, "__module__", None)
    if not isinstance(module, str):
        module = type(target).__module__
    if module.startswith("marivo.analysis"):
        return "analysis"
    if module.startswith("marivo.datasource"):
        return "datasource"
    return None


def _enrich(target: object) -> ResolvedLiveTarget[SemanticHelpDescriptor] | None:
    """Resolve concrete semantic runtime values before callable dispatch."""

    namespace_target = None
    if target is ref_factory:
        namespace_target = "ref"
    elif target is source_check:
        namespace_target = "source_check"
    if namespace_target is not None:
        descriptor = REGISTRY.by_canonical_id(namespace_target)
        return ResolvedLiveTarget(
            kind="descriptor",
            surface="semantic",
            canonical_id=namespace_target,
            descriptor=descriptor,
        )

    receiver = getattr(target, "__self__", None)
    method_name = getattr(target, "__name__", None)
    receiver_contract = TYPE_CONTRACTS.get(type(receiver))
    if (
        receiver_contract is not None
        and method_name in ("show", "render")
        and method_name in receiver_contract.public_methods
    ):
        try:
            REGISTRY.by_callable(target)
        except KeyError:
            return ResolvedLiveTarget(
                kind="type_contract",
                surface="semantic",
                type_name=receiver_contract.name,
            )
    return None


def _descriptor_kind(descriptor: SemanticHelpDescriptor) -> str:
    if isinstance(descriptor, SemanticObjectContract):
        return "object"
    if isinstance(descriptor, AuthoringCapability):
        if descriptor.callable_path is not None:
            return "callable"
        if descriptor.public_entrypoint is not None:
            return "expression"
    return "topic"


SEMANTIC_LIVE_SURFACE = adapt_surface(
    REGISTRY,
    TYPE_CONTRACTS,
    ERROR_TYPES,
    help_target_error=_help_target_error,
    default_suggestions=("load", "verify", "preview", "readiness", "metric"),
    enrich=_enrich,
)
PROVIDER = bind_provider(
    SEMANTIC_LIVE_SURFACE,
    SurfaceRenderer(render_root_help, render_descriptor, render_type, render_error_contract),
    type_contracts=TYPE_CONTRACTS,
    target_error=SemanticHelpTargetError,
    descriptor_kind=_descriptor_kind,
)
