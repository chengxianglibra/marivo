"""Semantic-owned live help target resolution inputs."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import NoReturn

from marivo._authoring.model import AuthoringCapability
from marivo.introspection.live.errors import build_help_target_error_payload
from marivo.introspection.live.resolve import (
    LiveSurface,
    ResolvedLiveTarget,
    build_string_target_index,
    build_suggestion_index,
)
from marivo.semantic._capabilities.model import SemanticHelpDescriptor, SemanticObjectContract
from marivo.semantic._capabilities.registry import ERROR_TYPES, REGISTRY, TYPE_CONTRACTS
from marivo.semantic.errors import SemanticHelpTargetError


class _NeverSemanticError(Exception):
    """Prevent generic error-base resolution outside the registered catalog."""


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
    from marivo.refs import ref as ref_factory
    from marivo.semantic.validation import source_check

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

    if isinstance(target, type) and ERROR_TYPES.get(target.__name__) is target:
        return ResolvedLiveTarget(
            kind="error_contract",
            surface="semantic",
            error_name=target.__name__,
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


def _build_surface() -> LiveSurface[SemanticHelpDescriptor]:
    """Build the immutable semantic help surface from the private registry."""
    type_index = MappingProxyType(
        {type_obj: contract.name for type_obj, contract in TYPE_CONTRACTS.items()}
    )
    string_targets = dict(
        build_string_target_index(REGISTRY, public_type_names=frozenset(type_index.values()))
    )
    return LiveSurface(
        registry=REGISTRY,
        descriptor_kind=_descriptor_kind,
        type_index=type_index,
        error_types=ERROR_TYPES,
        error_base=_NeverSemanticError,
        default_suggestions=("load", "verify", "preview", "readiness", "metric"),
        help_target_error=_help_target_error,
        enrich=_enrich,
        string_target_index=MappingProxyType(string_targets),
        suggestion_index=build_suggestion_index(REGISTRY),
    )


SEMANTIC_LIVE_SURFACE = _build_surface()
