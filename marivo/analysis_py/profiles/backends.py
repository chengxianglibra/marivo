"""Map a stored profile entry to a live ibis backend."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Final

from marivo.analysis_py.errors import (
    ProfileBackendTypeUnsupportedError,
    ProfileEnvVarMissingError,
    ProfileFieldInvalidError,
)
from marivo.analysis_py.profiles.store import StoredProfile

SUPPORTED_BACKEND_TYPES: Final[tuple[str, ...]] = ("duckdb", "trino", "mysql", "postgres")


def _resolve_env_refs(profile: StoredProfile) -> dict[str, Any]:
    """Return a plain dict of effective kwargs with *_env keys resolved.

    For every ``<stem>_env`` field, the corresponding environment variable is
    read and surfaced as the ``<stem>`` kwarg. Both keys are exposed when the
    caller wrote literal values for the stem (rare but allowed when the field
    is not on the sensitive whitelist).
    """
    resolved: dict[str, Any] = {}
    for key, value in profile.fields.items():
        if key.endswith("_env") and len(key) > len("_env"):
            stem = key[: -len("_env")]
            if not isinstance(value, str) or not value:
                raise ProfileFieldInvalidError(
                    message=(
                        f"profile {profile.name!r} field {key!r} must be a non-empty env var name"
                    ),
                    details={
                        "datasource": profile.name,
                        "field": key,
                        "reason": "env_ref value must be a non-empty string",
                    },
                )
            env_value = os.environ.get(value)
            if env_value is None or env_value == "":
                raise ProfileEnvVarMissingError(
                    message=(
                        f"env var {value!r} for profile {profile.name!r} field {stem!r} is not set"
                    ),
                    details={"datasource": profile.name, "field": stem, "env_var": value},
                )
            resolved[stem] = env_value
        else:
            resolved[key] = value
    return resolved


def build_backend(profile: StoredProfile) -> Any:
    """Open and return a live ibis backend for the given stored profile."""
    if profile.backend_type not in SUPPORTED_BACKEND_TYPES:
        raise ProfileBackendTypeUnsupportedError(
            message=(
                f"profile {profile.name!r} backend_type={profile.backend_type!r} is not "
                "supported by mv.profiles"
            ),
            details={
                "backend_type": profile.backend_type,
                "supported": list(SUPPORTED_BACKEND_TYPES),
            },
        )
    kwargs = _resolve_env_refs(profile)
    if profile.backend_type == "duckdb":
        return _build_duckdb(profile.name, kwargs)
    if profile.backend_type == "trino":
        return _build_trino(profile.name, kwargs)
    if profile.backend_type == "mysql":
        return _build_mysql(profile.name, kwargs)
    if profile.backend_type == "postgres":
        return _build_postgres(profile.name, kwargs)
    # Unreachable: SUPPORTED_BACKEND_TYPES check above
    raise ProfileBackendTypeUnsupportedError(  # pragma: no cover
        message=f"backend_type={profile.backend_type!r} unhandled",
        details={"backend_type": profile.backend_type, "supported": list(SUPPORTED_BACKEND_TYPES)},
    )


def _require(name: str, kwargs: Mapping[str, Any], key: str) -> Any:
    if key not in kwargs:
        raise ProfileFieldInvalidError(
            message=f"profile {name!r} missing required field {key!r}",
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

    # Field mapping: profile uses 'catalog' (Trino-native name); ibis expects 'database'.
    # Profile users do not need to learn the ibis-specific naming.
    host = _require(name, kwargs, "host")
    catalog = _require(name, kwargs, "catalog")
    connect_kwargs: dict[str, Any] = {
        "host": host,
        "database": catalog,
    }
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
