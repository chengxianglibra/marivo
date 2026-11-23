"""Native Help bindings for the cross-surface coordinator."""

from marivo.datasource._capabilities.registry import REGISTRY, TYPE_CONTRACTS
from marivo.datasource._capabilities.render import render_help_target, render_root_help
from marivo.datasource._capabilities.surface import DATASOURCE_LIVE_SURFACE

__all__ = [
    "DATASOURCE_LIVE_SURFACE",
    "REGISTRY",
    "TYPE_CONTRACTS",
    "render_help_target",
    "render_root_help",
]
