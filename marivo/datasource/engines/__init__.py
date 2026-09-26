"""Closed, selected-on-demand registry of datasource engine providers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from functools import lru_cache
from importlib import import_module

from marivo.datasource.engines.base import (
    GENERIC_PROFILE as GENERIC_PROFILE,
)
from marivo.datasource.engines.base import (
    CursorFrame as CursorFrame,
)
from marivo.datasource.engines.base import (
    EngineProfile as EngineProfile,
)
from marivo.datasource.engines.base import (
    decode_cursor_frame as decode_cursor_frame,
)
from marivo.datasource.engines.base import (
    quote_identifier as quote_identifier,
)

SUPPORTED_BACKEND_TYPES: tuple[str, ...] = (
    "duckdb",
    "sqlite",
    "trino",
    "mysql",
    "postgres",
    "clickhouse",
)
_ALIASES = {
    "sqlite3": "sqlite",
    "presto": "trino",
    "postgresql": "postgres",
    "redshift": "postgres",
}


@lru_cache(maxsize=len(SUPPORTED_BACKEND_TYPES))
def _load_profile(backend_type: str) -> EngineProfile:
    profile: EngineProfile = import_module(f"marivo.datasource.engines.{backend_type}").PROFILE
    if profile.name != backend_type:
        raise RuntimeError(f"engine provider {backend_type!r} declared {profile.name!r}")
    return profile


class _ProfileRegistry(Mapping[str, EngineProfile]):
    def __iter__(self) -> Iterator[str]:
        return iter(SUPPORTED_BACKEND_TYPES)

    def __len__(self) -> int:
        return len(SUPPORTED_BACKEND_TYPES)

    def __getitem__(self, backend_type: str) -> EngineProfile:
        if backend_type not in SUPPORTED_BACKEND_TYPES:
            raise KeyError(backend_type)
        return _load_profile(backend_type)


ENGINE_PROFILES: Mapping[str, EngineProfile] = _ProfileRegistry()


def profile_for_backend_type(backend_type: str) -> EngineProfile | None:
    return ENGINE_PROFILES.get(backend_type)


def require_profile_for_backend_type(backend_type: str) -> EngineProfile:
    from marivo.datasource.errors import DatasourceBackendTypeUnsupportedError, repair

    profile = profile_for_backend_type(backend_type)
    if profile is None:
        raise DatasourceBackendTypeUnsupportedError(
            message=f"backend_type={backend_type!r} is not supported by md",
            expected="a registered datasource backend type",
            received=backend_type,
            location="md backend dispatch",
            repair=repair(
                kind="configure",
                canonical_id="register",
                action="Use a supported datasource backend type.",
                candidates=tuple(sorted(SUPPORTED_BACKEND_TYPES)),
            ),
        )
    return profile


def profile_for_backend_name(name: str | None) -> EngineProfile:
    if not name:
        return GENERIC_PROFILE
    normalized = name.lower()
    return ENGINE_PROFILES.get(_ALIASES.get(normalized, normalized)) or GENERIC_PROFILE


def profile_for_backend(backend: object) -> EngineProfile:
    raw = getattr(backend, "name", None)
    return profile_for_backend_name(str(raw).lower() if raw is not None else None)
