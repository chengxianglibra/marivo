"""Public rendering through the single native Dataset descriptor registry."""

from marivo.analysis._capabilities.dataset_model import Descriptor
from marivo.analysis._capabilities.dataset_render import render
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.introspection.live.resolve import ResolvedLiveTarget


def render_root_help() -> str:
    return render(REGISTRY)


def render_help_target(target: ResolvedLiveTarget[Descriptor], *, original_target: object) -> str:
    del target
    return render(REGISTRY, original_target)
