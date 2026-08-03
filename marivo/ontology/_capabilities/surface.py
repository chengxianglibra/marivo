"""Ontology-owned live help resolution inputs."""

from __future__ import annotations

from types import MappingProxyType
from typing import NoReturn

from marivo.introspection.live.errors import build_help_target_error_payload
from marivo.introspection.live.resolve import (
    LiveSurface,
    build_string_target_index,
    build_suggestion_index,
)
from marivo.ontology._capabilities.registry import REGISTRY, OntologyDescriptor
from marivo.ontology.catalog import OntologyCatalog
from marivo.ontology.errors import OntologyError, OntologyHelpTargetError
from marivo.ontology.types import SemanticEdgeRef


def _help_target_error(target: object, suggestions: tuple[str, ...]) -> NoReturn:
    raise OntologyHelpTargetError(
        build_help_target_error_payload(target, surface="ontology", candidates=suggestions)
    )


ONTOLOGY_LIVE_SURFACE = LiveSurface[OntologyDescriptor](
    registry=REGISTRY,
    type_index=MappingProxyType(
        {OntologyCatalog: "OntologyCatalog", SemanticEdgeRef: "SemanticEdgeRef"}
    ),
    error_types=MappingProxyType(
        {
            name: value
            for name, value in vars(__import__("marivo.ontology.errors", fromlist=["*"])).items()
            if isinstance(value, type) and issubclass(value, OntologyError)
        }
    ),
    error_base=OntologyError,
    default_suggestions=("authoring", "influences", "related_to"),
    help_target_error=_help_target_error,
    string_target_index=build_string_target_index(
        REGISTRY, public_type_names=frozenset({"OntologyCatalog", "SemanticEdgeRef"})
    ),
    suggestion_index=build_suggestion_index(REGISTRY),
)


__all__ = ["ONTOLOGY_LIVE_SURFACE"]
