"""Native Help bindings for the cross-surface coordinator."""

from marivo.ontology._capabilities.registry import TYPE_CONTRACTS, OntologyDescriptor
from marivo.ontology._capabilities.render import render_help_target, render_root_help
from marivo.ontology._capabilities.surface import ONTOLOGY_LIVE_SURFACE

__all__ = [
    "ONTOLOGY_LIVE_SURFACE",
    "TYPE_CONTRACTS",
    "OntologyDescriptor",
    "render_help_target",
    "render_root_help",
]
