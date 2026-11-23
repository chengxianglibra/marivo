"""Ontology Help manifest and renderer for the public coordinator."""

from typing import NoReturn

from marivo.introspection.capabilities.provider import SurfaceRenderer, adapt_surface, bind_provider
from marivo.introspection.live.errors import build_help_target_error_payload
from marivo.ontology._help_specs.manifest import ERROR_TYPES, REGISTRY, TYPE_CONTRACTS
from marivo.ontology._help_specs.render import (
    render_descriptor,
    render_error_contract,
    render_root_help,
    render_type,
)
from marivo.ontology.errors import OntologyHelpTargetError


def _help_target_error(target: object, suggestions: tuple[str, ...]) -> NoReturn:
    raise OntologyHelpTargetError(
        build_help_target_error_payload(target, surface="ontology", candidates=suggestions)
    )


ONTOLOGY_LIVE_SURFACE = adapt_surface(
    REGISTRY,
    TYPE_CONTRACTS,
    ERROR_TYPES,
    help_target_error=_help_target_error,
    default_suggestions=("authoring", "influences", "related_to"),
)
PROVIDER = bind_provider(
    ONTOLOGY_LIVE_SURFACE,
    SurfaceRenderer(render_root_help, render_descriptor, render_type, render_error_contract),
    type_contracts=TYPE_CONTRACTS,
    target_error=OntologyHelpTargetError,
)
