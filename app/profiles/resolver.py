from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Literal

from app.contracts.errors import DomainError, ErrorCode

logger = logging.getLogger(__name__)

ProfileMode = Literal["local", "server"]
EntryPoint = Literal["local_stdio", "server_http"]


class ProfileResolutionError(DomainError):
    """Entry point cannot resolve a valid profile, or the resolved
    profile is incompatible with the entry point."""


_DEFAULT_BY_ENTRY: dict[EntryPoint, ProfileMode] = {
    "local_stdio": "local",
    "server_http": "server",
}

_ALLOWED_BY_ENTRY: dict[EntryPoint, frozenset[ProfileMode]] = {
    "local_stdio": frozenset({"local"}),
    "server_http": frozenset({"server"}),
}


def resolve_profile(
    *,
    entry_point: EntryPoint,
    explicit: str | None = None,
    env: Mapping[str, str] | None = None,
) -> ProfileMode:
    env_map = env if env is not None else os.environ

    # Normalize empty strings to None: MARIVO_PROFILE="" is missing, not "".
    explicit = explicit or None
    env_value = env_map.get("MARIVO_PROFILE") or None

    raw: str | None = explicit or env_value
    source = "explicit" if explicit else ("env" if env_value else None)

    if raw is None:
        return _DEFAULT_BY_ENTRY[entry_point]

    # raw is now a non-empty string; validate it is a known profile.
    if raw not in ("local", "server"):
        logger.error(
            "profile.unknown entry_point=%s source=%s candidate=%r",
            entry_point,
            source,
            raw,
        )
        raise ProfileResolutionError(
            ErrorCode.VALIDATION,
            f"Unknown profile {raw!r}; expected 'local' or 'server' "
            f"(source={source}, entry_point={entry_point})",
        )

    candidate: ProfileMode = raw  # type: ignore[assignment]

    if candidate not in _ALLOWED_BY_ENTRY[entry_point]:
        logger.error(
            "profile.incompatible entry_point=%s source=%s candidate=%s",
            entry_point,
            source,
            candidate,
        )
        raise ProfileResolutionError(
            ErrorCode.VALIDATION,
            f"Profile {candidate!r} (source={source}) is not allowed at "
            f"entry point {entry_point!r}",
        )

    return candidate
