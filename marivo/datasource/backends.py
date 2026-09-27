"""Map a project datasource entry to a live ibis backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from marivo.datasource import secrets
from marivo.datasource.engines import (
    SUPPORTED_BACKEND_TYPES as SUPPORTED_BACKEND_TYPES,
)
from marivo.datasource.engines import (
    require_profile_for_backend_type,
)
from marivo.datasource.errors import DatasourceFieldInvalidError, repair
from marivo.datasource.ir import DatasourceIR


@dataclass(frozen=True)
class EffectiveDatasourceKwargs:
    kwargs: dict[str, Any]
    env_sourced_secrets: tuple[secrets.ResolvedSecret, ...]


def _reject_unqualified_http_auth(datasource: DatasourceIR) -> None:
    if datasource.backend_type == "duckdb" and any(
        stem == "http_bearer_token" or stem.startswith("http_header:")
        for stem in datasource.env_refs
    ):
        raise DatasourceFieldInvalidError(
            message="Authenticated HTTP sources are blocked pending a qualified credential path.",
            expected="an unauthenticated HTTP source or a qualified public credential API",
            received="authenticated HTTP datasource",
            location=f"datasource {datasource.name!r}",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action="Use an unauthenticated source or stage authenticated data upstream before registering it.",
            ),
        )


def _effective_kwargs(datasource: DatasourceIR) -> EffectiveDatasourceKwargs:
    _reject_unqualified_http_auth(datasource)
    resolved: dict[str, Any] = dict(datasource.fields)
    env_sourced: list[secrets.ResolvedSecret] = []
    for stem, env_var in datasource.env_refs.items():
        if not isinstance(env_var, str) or not env_var:
            raise DatasourceFieldInvalidError(
                message=(
                    f"datasource {datasource.name!r} field {stem}_env must be a non-empty "
                    "env var name"
                ),
                expected="a non-empty environment variable name",
                received=repr(env_var),
                location=f"models/datasources/ entry {datasource.name!r} field {stem}_env",
                repair=repair(
                    kind="environment",
                    canonical_id="test",
                    action="Set a non-empty environment variable reference.",
                ),
            )
        resolved_secret = secrets.resolve(env_var, datasource=datasource.name, field=stem)
        resolved[stem] = resolved_secret.value
        if isinstance(resolved_secret.provider, secrets.EnvProvider):
            env_sourced.append(resolved_secret)
    return EffectiveDatasourceKwargs(
        kwargs=resolved,
        env_sourced_secrets=tuple(env_sourced),
    )


@dataclass(frozen=True)
class BuiltDatasourceBackend:
    backend: Any
    env_sourced_secrets: tuple[secrets.ResolvedSecret, ...]


def build_backend_with_secrets(
    datasource: DatasourceIR,
    *,
    read_only: bool = False,
    terminal_timeout_seconds: int | None = None,
) -> BuiltDatasourceBackend:
    """Open an ibis backend and return any env-sourced secret provenance."""
    require_profile_for_backend_type(datasource.backend_type)
    effective = _effective_kwargs(datasource)
    return _build_backend_from_effective(
        datasource,
        effective,
        read_only=read_only,
        terminal_timeout_seconds=terminal_timeout_seconds,
    )


def _build_backend_from_effective(
    datasource: DatasourceIR,
    effective: EffectiveDatasourceKwargs,
    *,
    read_only: bool = False,
    terminal_timeout_seconds: int | None = None,
) -> BuiltDatasourceBackend:
    """Open from already resolved operation-local credentials without resolving twice."""
    _reject_unqualified_http_auth(datasource)
    profile = require_profile_for_backend_type(datasource.backend_type)
    kwargs = dict(effective.kwargs)
    if datasource.backend_type == "duckdb":
        kwargs.pop("http_scope", None)
    if terminal_timeout_seconds is not None:
        if datasource.backend_type == "postgres":
            kwargs["autocommit"] = False
            kwargs["options"] = (
                f"-c statement_timeout={terminal_timeout_seconds * 1000} -c TimeZone=UTC"
            )
        elif datasource.backend_type == "trino":
            properties = kwargs.get("session_properties")
            kwargs["session_properties"] = {
                **(properties if isinstance(properties, dict) else {}),
                "query_max_run_time": f"{terminal_timeout_seconds}s",
            }
    if datasource.backend_type == "trino":
        kwargs["timezone"] = "UTC"
    if read_only:
        kwargs = profile.apply_read_only_kwargs(kwargs)
    backend = profile.connect(datasource.name, kwargs)
    try:
        if terminal_timeout_seconds is not None:
            if datasource.backend_type == "postgres":
                backend.con.read_only = True
            if datasource.backend_type in {"postgres", "trino"}:
                backend._marivo_terminal_timeout_seconds = terminal_timeout_seconds
    except BaseException:
        disconnect = getattr(backend, "disconnect", None)
        if callable(disconnect):
            disconnect()
        raise
    return BuiltDatasourceBackend(
        backend=backend,
        env_sourced_secrets=effective.env_sourced_secrets,
    )


def build_backend(
    datasource: DatasourceIR,
    *,
    read_only: bool = False,
    terminal_timeout_seconds: int | None = None,
) -> Any:
    """Open and return a live ibis backend for the given datasource."""
    return build_backend_with_secrets(
        datasource,
        read_only=read_only,
        terminal_timeout_seconds=terminal_timeout_seconds,
    ).backend
