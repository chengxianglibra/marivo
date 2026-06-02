"""Map a project datasource entry to a live ibis backend."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from marivo.analysis.datasources import secrets
from marivo.analysis.errors import (
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
    return EffectiveDatasourceKwargs(
        kwargs=resolved,
        env_sourced_secrets=tuple(env_sourced),
    )


@dataclass(frozen=True)
class BuiltDatasourceBackend:
    backend: Any
    env_sourced_secrets: tuple[secrets.ResolvedSecret, ...]


def build_backend_with_secrets(datasource: DatasourceIR) -> BuiltDatasourceBackend:
    """Open an ibis backend and return any env-sourced secret provenance."""
    if datasource.backend_type not in SUPPORTED_BACKEND_TYPES:
        raise DatasourceBackendTypeUnsupportedError(
            message=(
                f"datasource {datasource.name!r} backend_type={datasource.backend_type!r} "
                "is not supported by mv.datasources"
            ),
            details={
                "backend_type": datasource.backend_type,
                "supported": list(SUPPORTED_BACKEND_TYPES),
            },
        )
    effective = _effective_kwargs(datasource)
    kwargs = effective.kwargs
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


def build_backend(datasource: DatasourceIR) -> Any:
    """Open and return a live ibis backend for the given datasource."""
    return build_backend_with_secrets(datasource).backend


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
    if "secure" in kwargs:
        connect_kwargs["secure"] = bool(kwargs["secure"])
    if "settings" in kwargs and isinstance(kwargs["settings"], dict):
        connect_kwargs["settings"] = dict(kwargs["settings"])
    return ibis.clickhouse.connect(**connect_kwargs)
