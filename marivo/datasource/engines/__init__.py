"""Closed registry of per-engine profiles.

This package is internal to ``marivo.datasource``; nothing from here
appears in the public ``marivo.datasource`` ``__all__``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

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
from marivo.datasource.engines.clickhouse import PROFILE as CLICKHOUSE_PROFILE
from marivo.datasource.engines.duckdb import PROFILE as DUCKDB_PROFILE
from marivo.datasource.engines.mysql import PROFILE as MYSQL_PROFILE
from marivo.datasource.engines.postgres import PROFILE as POSTGRES_PROFILE
from marivo.datasource.engines.trino import PROFILE as TRINO_PROFILE

ENGINE_PROFILES: Mapping[str, EngineProfile] = MappingProxyType(
    {
        "duckdb": DUCKDB_PROFILE,
        "trino": TRINO_PROFILE,
        "mysql": MYSQL_PROFILE,
        "postgres": POSTGRES_PROFILE,
        "clickhouse": CLICKHOUSE_PROFILE,
    }
)
SUPPORTED_BACKEND_TYPES: tuple[str, ...] = tuple(ENGINE_PROFILES)

_ALIASES: dict[str, EngineProfile] = {}
for _profile in ENGINE_PROFILES.values():
    for _alias in _profile.aliases:
        if _alias in _ALIASES:
            raise RuntimeError(f"duplicate engine profile alias {_alias!r}")
        _ALIASES[_alias] = _profile


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
    return ENGINE_PROFILES.get(normalized) or _ALIASES.get(normalized) or GENERIC_PROFILE


def profile_for_backend(backend: object) -> EngineProfile:
    raw = getattr(backend, "name", None)
    return profile_for_backend_name(str(raw).lower() if raw is not None else None)
