"""Native Help bindings for the cross-surface coordinator."""

from marivo.semantic._capabilities.model import SemanticHelpDescriptor
from marivo.semantic._capabilities.registry import TYPE_CONTRACTS
from marivo.semantic._capabilities.render import render_help_target, render_root_help
from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE

__all__ = [
    "SEMANTIC_LIVE_SURFACE",
    "TYPE_CONTRACTS",
    "SemanticHelpDescriptor",
    "render_help_target",
    "render_root_help",
]
