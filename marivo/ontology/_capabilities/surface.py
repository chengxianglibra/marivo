"""Ontology-owned live help resolution inputs."""

from __future__ import annotations

from types import MappingProxyType
from typing import NoReturn

from marivo.introspection.live.errors import build_help_target_error_payload
from marivo.introspection.live.resolve import (
    LiveSurface,
    ResolvedLiveTarget,
    build_string_target_index,
    build_suggestion_index,
)
from marivo.ontology._capabilities.registry import (
    ERROR_TYPES,
    REGISTRY,
    TYPE_CONTRACTS,
    OntologyDescriptor,
)
from marivo.ontology.errors import OntologyHelpTargetError


class _NeverOntologyError(Exception):
    """Prevent generic error-base resolution outside the registered catalog."""


def _help_target_error(target: object, suggestions: tuple[str, ...]) -> NoReturn:
    raise OntologyHelpTargetError(
        build_help_target_error_payload(target, surface="ontology", candidates=suggestions)
    )


def _enrich(target: object) -> ResolvedLiveTarget[OntologyDescriptor] | None:
    """Resolve concrete ontology values and errors before callable dispatch."""
    error_type = type(target)
    if ERROR_TYPES.get(error_type.__name__) is error_type:
        return ResolvedLiveTarget(
            kind="error_briefing",
            surface="ontology",
            error_name=error_type.__name__,
            original=target,
        )
    if isinstance(target, type) and ERROR_TYPES.get(target.__name__) is target:
        return ResolvedLiveTarget(
            kind="error_contract",
            surface="ontology",
            error_name=target.__name__,
        )
    contract = TYPE_CONTRACTS.get(type(target))
    if contract is not None:
        return ResolvedLiveTarget(
            kind="type_contract",
            surface="ontology",
            type_name=contract.name,
        )
    return None


_TYPE_INDEX = MappingProxyType(
    {type_obj: contract.name for type_obj, contract in TYPE_CONTRACTS.items()}
)


ONTOLOGY_LIVE_SURFACE = LiveSurface[OntologyDescriptor](
    registry=REGISTRY,
    type_index=_TYPE_INDEX,
    error_types=ERROR_TYPES,
    error_base=_NeverOntologyError,
    default_suggestions=("authoring", "influences", "related_to"),
    help_target_error=_help_target_error,
    enrich=_enrich,
    string_target_index=build_string_target_index(
        REGISTRY, public_type_names=frozenset(_TYPE_INDEX.values())
    ),
    suggestion_index=build_suggestion_index(REGISTRY),
)


__all__ = ["ONTOLOGY_LIVE_SURFACE"]
