"""Map a project datasource entry to a live ibis backend."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from marivo.datasource import secrets
from marivo.datasource.authoring import SENSITIVE_FIELD_STEMS
from marivo.datasource.engines import (
    SUPPORTED_BACKEND_TYPES as SUPPORTED_BACKEND_TYPES,
)
from marivo.datasource.engines import (
    require_profile_for_backend_type,
)
from marivo.datasource.errors import DatasourceFieldInvalidError, DatasourceMetadataError
from marivo.datasource.ir import DatasourceIR, JsonSourceIR


@dataclass(frozen=True)
class EffectiveDatasourceKwargs:
    kwargs: dict[str, Any]
    env_sourced_secrets: tuple[secrets.ResolvedSecret, ...]


def _effective_kwargs(datasource: DatasourceIR) -> EffectiveDatasourceKwargs:
    resolved: dict[str, Any] = dict(datasource.fields)
    env_sourced: list[secrets.ResolvedSecret] = []
    for stem, env_var in datasource.env_refs.items():
        if not isinstance(env_var, str) or not env_var:
            raise DatasourceFieldInvalidError(
                message=(
                    f"datasource {datasource.name!r} field {stem}_env must be a non-empty "
                    "env var name"
                ),
                details={
                    "datasource": datasource.name,
                    "field": f"{stem}_env",
                    "reason": "env_ref value must be a non-empty string",
                },
            )
        resolved_secret = secrets.resolve(env_var, datasource=datasource.name, field=stem)
        resolved[stem] = resolved_secret.value
        if isinstance(resolved_secret.provider, secrets.EnvProvider):
            env_sourced.append(resolved_secret)
    # Conventional env var fallback: for sensitive fields not already resolved,
    # try the conventional name MARIVO_{DATASOURCE_NAME}_{FIELD_STEM}.
    for stem in SENSITIVE_FIELD_STEMS:
        if stem in resolved:
            continue
        conventional = secrets.conventional_env_var(datasource.name, stem)
        conventional_secret = secrets.resolve_optional(conventional)
        if conventional_secret is not None:
            resolved[stem] = conventional_secret.value
            if isinstance(conventional_secret.provider, secrets.EnvProvider):
                env_sourced.append(conventional_secret)
    return EffectiveDatasourceKwargs(
        kwargs=resolved,
        env_sourced_secrets=tuple(env_sourced),
    )


@dataclass(frozen=True)
class BuiltDatasourceBackend:
    backend: Any
    env_sourced_secrets: tuple[secrets.ResolvedSecret, ...]


_HTTP_SCHEME = re.compile(r"^https?://", re.IGNORECASE)


def apply_json_http_settings(backend: object, source: object) -> None:
    """Enable force_download for http(s) JSON sources; no-op for local paths."""
    if not isinstance(source, JsonSourceIR):
        return
    if not _HTTP_SCHEME.match(source.path):
        return
    raw_sql = getattr(backend, "raw_sql", None)
    if not callable(raw_sql):
        raise DatasourceMetadataError(
            message=(
                f"json source {source.path!r} is http(s), but this datasource "
                "backend cannot read remote JSON. md.json(...) is a DuckDB "
                "file-source (local path, glob, or httpfs URL), not a generic "
                "HTTP API reader."
            ),
            details={
                "path": source.path,
                "reason": "backend_lacks_httpfs",
                "location": f"md.json({source.path!r})",
                "cause": "backend lacks callable raw_sql (DuckDB httpfs not available)",
                "fix_snippet": (
                    "# Use a local path or glob pattern instead of an http(s):// URL:\n"
                    "import marivo.datasource as md\n"
                    'source = md.json("data/events/*.json", format="newline_delimited")'
                ),
            },
        )
    raw_sql("SET force_download=true")


def build_backend_with_secrets(
    datasource: DatasourceIR,
    *,
    read_only: bool = False,
) -> BuiltDatasourceBackend:
    """Open an ibis backend and return any env-sourced secret provenance."""
    profile = require_profile_for_backend_type(datasource.backend_type)
    effective = _effective_kwargs(datasource)
    kwargs = dict(effective.kwargs)
    if read_only:
        kwargs = profile.apply_read_only_kwargs(kwargs)
    backend = profile.connect(datasource.name, kwargs)
    return BuiltDatasourceBackend(
        backend=backend,
        env_sourced_secrets=effective.env_sourced_secrets,
    )


def build_backend(datasource: DatasourceIR, *, read_only: bool = False) -> Any:
    """Open and return a live ibis backend for the given datasource."""
    return build_backend_with_secrets(datasource, read_only=read_only).backend
