"""Native Help bindings for the cross-surface coordinator."""

from marivo.analysis._capabilities.render import render_help_target, render_root_help
from marivo.analysis._capabilities.surface import ANALYSIS_LIVE_SURFACE
from marivo.analysis.model.disclosure import Descriptor

__all__ = [
    "ANALYSIS_LIVE_SURFACE",
    "Descriptor",
    "render_help_target",
    "render_root_help",
]
