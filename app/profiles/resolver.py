from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Literal

from app.contracts.errors import DomainError

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
    env: Mapping[str, str] | None = None,
) -> ProfileMode:
    """Skeleton: returns the entry-point default. Precedence sources
    added in subsequent tasks."""
    return _DEFAULT_BY_ENTRY[entry_point]
