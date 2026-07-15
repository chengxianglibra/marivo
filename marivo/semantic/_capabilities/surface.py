"""Semantic-owned live help target resolution inputs."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import NoReturn

from marivo.introspection.live.errors import build_help_target_error_payload
from marivo.introspection.live.resolve import (
    LiveSurface,
    ResolvedLiveTarget,
    build_suggestion_index,
)
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
        adapter = {"analysis": "mv.help", "datasource": "md.help"}[owner]
        payload = replace(
            payload,
            message=f"{payload.message} This target belongs to {owner}; use {adapter}(...).",
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


def _enrich(target: object) -> ResolvedLiveTarget | None:
    """Resolve concrete semantic runtime values before callable dispatch."""
    error_type = type(target)
    if ERROR_TYPES.get(error_type.__name__) is error_type:
        return ResolvedLiveTarget(
            kind="error_briefing",
            surface="semantic",
            error_name=error_type.__name__,
            original=target,
        )
    if isinstance(target, type) and ERROR_TYPES.get(target.__name__) is target:
        return ResolvedLiveTarget(
            kind="error_contract",
            surface="semantic",
            error_name=target.__name__,
        )
    contract = TYPE_CONTRACTS.get(type(target))
    if contract is not None:
        return ResolvedLiveTarget(
            kind="type_contract",
            surface="semantic",
            type_name=contract.name,
            original=target,
        )
    return None


def _build_surface() -> LiveSurface:
    """Build the immutable semantic help surface from the private registry."""
    type_index = MappingProxyType(
        {type_obj: contract.name for type_obj, contract in TYPE_CONTRACTS.items()}
    )
    return LiveSurface(
        registry=REGISTRY,
        type_index=type_index,
        error_types=ERROR_TYPES,
        error_base=_NeverSemanticError,
        default_suggestions=("load", "verify_object", "preview", "readiness", "help"),
        help_target_error=_help_target_error,
        enrich=_enrich,
        suggestion_index=build_suggestion_index(REGISTRY),
    )


SEMANTIC_LIVE_SURFACE = _build_surface()
