"""Datasource Help manifest and renderer for the public coordinator."""

from dataclasses import replace
from typing import NoReturn

from marivo._kernel.refs import Ref
from marivo.datasource._help_specs.manifest import ERROR_TYPES, REGISTRY, TYPE_CONTRACTS
from marivo.datasource._help_specs.render import (
    render_descriptor,
    render_error_contract,
    render_root_help,
    render_type,
)
from marivo.datasource.errors import DatasourceHelpTargetError
from marivo.introspection.capabilities.provider import SurfaceRenderer, adapt_surface, bind_provider
from marivo.introspection.live.errors import build_help_target_error_payload


def _help_target_error(target: object, suggestions: tuple[str, ...]) -> NoReturn:
    owner = _cross_surface_owner(target)
    payload = build_help_target_error_payload(target, surface="datasource", candidates=suggestions)
    if owner is not None:
        payload = replace(
            payload,
            message=(
                f"{payload.message} This target belongs to {owner}; "
                f'use marivo.help("{owner}.<target>").'
            ),
        )
    raise DatasourceHelpTargetError(payload)


def _cross_surface_owner(target: object) -> str | None:
    if type(target) is Ref:
        return "semantic"
    callable_target = getattr(target, "__func__", target)
    module = getattr(callable_target, "__module__", None)
    if not isinstance(module, str):
        module = type(target).__module__
    if module.startswith("marivo.analysis"):
        return "analysis"
    if module.startswith("marivo.semantic"):
        return "semantic"
    return None


DATASOURCE_LIVE_SURFACE = adapt_surface(
    REGISTRY,
    TYPE_CONTRACTS,
    ERROR_TYPES,
    help_target_error=_help_target_error,
    default_suggestions=("inspect", "duckdb", "register", "load"),
)
PROVIDER = bind_provider(
    DATASOURCE_LIVE_SURFACE,
    SurfaceRenderer(render_root_help, render_descriptor, render_type, render_error_contract),
    type_contracts=TYPE_CONTRACTS,
    target_error=DatasourceHelpTargetError,
)
