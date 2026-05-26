"""Map a project datasource entry to a live ibis backend."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Final

from marivo.analysis_py.errors import (
    DatasourceBackendTypeUnsupportedError,
    DatasourceEnvVarMissingError,
    DatasourceFieldInvalidError,
)
from marivo.datasource_py.ir import DatasourceIR

SUPPORTED_BACKEND_TYPES: Final[tuple[str, ...]] = ("duckdb", "trino", "mysql", "postgres")


def _effective_kwargs(datasource: DatasourceIR) -> dict[str, Any]:
    resolved: dict[str, Any] = dict(datasource.fields)
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
        env_value = os.environ.get(env_var)
        if env_value is None or env_value == "":
            raise DatasourceEnvVarMissingError(
                message=(
                    f"env var {env_var!r} for datasource {datasource.name!r} "
                    f"field {stem!r} is not set"
                ),
                details={"datasource": datasource.name, "field": stem, "env_var": env_var},
            )
        resolved[stem] = env_value
    return resolved


def build_backend(datasource: DatasourceIR) -> Any:
    """Open and return a live ibis backend for the given datasource."""
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
    kwargs = _effective_kwargs(datasource)
    if datasource.backend_type == "duckdb":
        return _build_duckdb(datasource.name, kwargs)
    if datasource.backend_type == "trino":
        return _build_trino(datasource.name, kwargs)
    if datasource.backend_type == "mysql":
        return _build_mysql(datasource.name, kwargs)
    if datasource.backend_type == "postgres":
        return _build_postgres(datasource.name, kwargs)
    raise DatasourceBackendTypeUnsupportedError(  # pragma: no cover
        message=f"backend_type={datasource.backend_type!r} unhandled",
        details={
            "backend_type": datasource.backend_type,
            "supported": list(SUPPORTED_BACKEND_TYPES),
        },
    )


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
    connect_kwargs: dict[str, Any] = {"host": host, "database": catalog}
    for key in ("port", "user", "schema", "source", "http_scheme", "password"):
        if key in kwargs:
            connect_kwargs[key] = kwargs[key]
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
    user = _require(name, kwargs, "user")
    database = _require(name, kwargs, "database")
    connect_kwargs: dict[str, Any] = {"host": host, "user": user, "database": database}
    for key in ("port", "password"):
        if key in kwargs:
            connect_kwargs[key] = kwargs[key]
    return ibis.mysql.connect(**connect_kwargs)


def _build_postgres(name: str, kwargs: Mapping[str, Any]) -> Any:
    import ibis

    host = _require(name, kwargs, "host")
    user = _require(name, kwargs, "user")
    database = _require(name, kwargs, "database")
    connect_kwargs: dict[str, Any] = {"host": host, "user": user, "database": database}
    for key in ("port", "password", "schema"):
        if key in kwargs:
            connect_kwargs[key] = kwargs[key]
    return ibis.postgres.connect(**connect_kwargs)
