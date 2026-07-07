"""Map a project datasource entry to a live ibis backend."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from marivo.datasource import secrets
from marivo.datasource.authoring import SENSITIVE_FIELD_STEMS
from marivo.datasource.errors import (
    DatasourceBackendTypeUnsupportedError,
    DatasourceFieldInvalidError,
)
from marivo.datasource.ir import DatasourceIR

SUPPORTED_BACKEND_TYPES: Final[tuple[str, ...]] = (
    "duckdb",
    "trino",
    "mysql",
    "postgres",
    "clickhouse",
)


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


def _with_read_only_kwargs(
    backend_type: str,
    kwargs: Mapping[str, Any],
    read_only: bool,
) -> dict[str, Any]:
    """Return kwargs forced into read-only mode for connection-level backends.

    DuckDB and ClickHouse enforce read-only at connect time. Postgres, Trino, and
    MySQL have no connect-level read-only flag; they are enforced by the caller via
    a ``BEGIN/START TRANSACTION READ ONLY`` transaction, so their kwargs are unchanged.
    """
    if not read_only:
        return dict(kwargs)
    out = dict(kwargs)
    if backend_type == "duckdb":
        out["read_only"] = True
    elif backend_type == "clickhouse":
        settings = dict(out.get("settings") or {})
        settings["access_mode"] = "read_only"
        out["settings"] = settings
    return out


def build_backend_with_secrets(
    datasource: DatasourceIR,
    *,
    read_only: bool = False,
) -> BuiltDatasourceBackend:
    """Open an ibis backend and return any env-sourced secret provenance."""
    if datasource.backend_type not in SUPPORTED_BACKEND_TYPES:
        raise DatasourceBackendTypeUnsupportedError(
            message=(
                f"datasource {datasource.name!r} backend_type={datasource.backend_type!r} "
                "is not supported by md"
            ),
            details={
                "backend_type": datasource.backend_type,
                "supported": list(SUPPORTED_BACKEND_TYPES),
            },
        )
    effective = _effective_kwargs(datasource)
    kwargs = _with_read_only_kwargs(datasource.backend_type, effective.kwargs, read_only)
    if datasource.backend_type == "duckdb":
        backend = _build_duckdb(datasource.name, kwargs)
    elif datasource.backend_type == "trino":
        backend = _build_trino(datasource.name, kwargs)
    elif datasource.backend_type == "mysql":
        backend = _build_mysql(datasource.name, kwargs)
    elif datasource.backend_type == "postgres":
        backend = _build_postgres(datasource.name, kwargs)
    elif datasource.backend_type == "clickhouse":
        backend = _build_clickhouse(datasource.name, kwargs)
    else:
        raise DatasourceBackendTypeUnsupportedError(  # pragma: no cover
            message=f"backend_type={datasource.backend_type!r} unhandled",
            details={
                "backend_type": datasource.backend_type,
                "supported": list(SUPPORTED_BACKEND_TYPES),
            },
        )
    return BuiltDatasourceBackend(
        backend=backend,
        env_sourced_secrets=effective.env_sourced_secrets,
    )


def build_backend(datasource: DatasourceIR, *, read_only: bool = False) -> Any:
    """Open and return a live ibis backend for the given datasource."""
    return build_backend_with_secrets(datasource, read_only=read_only).backend


def _require(name: str, kwargs: Mapping[str, Any], key: str) -> Any:
    if key not in kwargs:
        raise DatasourceFieldInvalidError(
            message=f"datasource {name!r} missing required field {key!r}",
            details={"datasource": name, "field": key, "reason": "required field missing"},
        )
    return kwargs[key]


def _build_duckdb(name: str, kwargs: Mapping[str, Any]) -> Any:
    import ibis

    path = kwargs.get("path", ":memory:")
    read_only = bool(kwargs.get("read_only", False))
    return ibis.duckdb.connect(path, read_only=read_only)


def _build_trino(name: str, kwargs: Mapping[str, Any]) -> Any:
    import ibis

    host = _require(name, kwargs, "host")
    catalog = _require(name, kwargs, "catalog")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs.pop("catalog", None)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = catalog
    if "client_tags" in kwargs:
        tags = kwargs["client_tags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        connect_kwargs["client_tags"] = list(tags)
    if "session_properties" in kwargs and isinstance(kwargs["session_properties"], dict):
        connect_kwargs["session_properties"] = dict(kwargs["session_properties"])
    return ibis.trino.connect(**connect_kwargs)


def _build_mysql(name: str, kwargs: Mapping[str, Any]) -> Any:
    import ibis

    host = _require(name, kwargs, "host")
    database = _require(name, kwargs, "database")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = database
    return ibis.mysql.connect(**connect_kwargs)


def _build_postgres(name: str, kwargs: Mapping[str, Any]) -> Any:
    import ibis

    host = _require(name, kwargs, "host")
    database = _require(name, kwargs, "database")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = database
    return ibis.postgres.connect(**connect_kwargs)


def _build_clickhouse(name: str, kwargs: Mapping[str, Any]) -> Any:
    import ibis

    host = _require(name, kwargs, "host")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = kwargs.get("database", "default")
    connect_kwargs.setdefault("autogenerate_session_id", False)
    if "secure" in kwargs:
        connect_kwargs["secure"] = bool(kwargs["secure"])
    if "settings" in kwargs and isinstance(kwargs["settings"], dict):
        connect_kwargs["settings"] = dict(kwargs["settings"])
    return ibis.clickhouse.connect(**connect_kwargs)
