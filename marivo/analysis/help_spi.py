"""Analysis Help manifest and renderer for the public coordinator."""

from functools import partial

from marivo.analysis._help_specs.manifest import REGISTRY
from marivo.analysis._help_specs.render import render_descriptor, render_error
from marivo.analysis.errors import HelpTargetError
from marivo.introspection.capabilities.provider import SurfaceRenderer, bind_provider

ANALYSIS_LIVE_SURFACE = REGISTRY.live_surface()
PROVIDER = bind_provider(
    ANALYSIS_LIVE_SURFACE,
    SurfaceRenderer(
        root=partial(render_descriptor, REGISTRY, REGISTRY.by_canonical_id("")),
        descriptor=partial(render_descriptor, REGISTRY),
        type_page=lambda name: render_descriptor(REGISTRY, REGISTRY.by_canonical_id(name)),
        error_page=render_error,
    ),
    target_error=HelpTargetError,
)
