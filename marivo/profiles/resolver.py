from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from marivo.contracts.errors import DomainError, ErrorCode

if TYPE_CHECKING:
    from marivo.config import MarivoConfig

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
    workspace_config_path: Path | None = None,
    service_config: MarivoConfig | None = None,
) -> ProfileMode:
    env_map = env if env is not None else os.environ

    # Normalize empty strings to None.
    explicit = explicit or None
    env_value = env_map.get("MARIVO_PROFILE") or None

    candidate: str | None = explicit or env_value
    source = "explicit" if explicit else ("env" if env_value else None)

    # Service config (loaded MarivoConfig) — server entry only per parent §7.
    if candidate is None and entry_point == "server_http" and service_config is not None:
        cfg_value = getattr(service_config, "profile", None) or None
        if cfg_value:
            candidate = str(cfg_value)
            source = "service_config"

    # Workspace .marivo/marivo.toml — local entry only.
    if candidate is None and entry_point == "local_stdio" and workspace_config_path is not None:
        toml_value = _read_profile_from_toml(workspace_config_path)
        if toml_value:
            candidate = toml_value
            source = "workspace_toml"

    if candidate is None:
        candidate = _DEFAULT_BY_ENTRY[entry_point]
        source = "default"

    if candidate not in ("local", "server"):
        logger.error(
            "profile.unknown entry_point=%s source=%s candidate=%r",
            entry_point,
            source,
            candidate,
        )
        raise ProfileResolutionError(
            ErrorCode.VALIDATION,
            f"Unknown profile {candidate!r}; expected 'local' or 'server' "
            f"(source={source}, entry_point={entry_point})",
        )

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

    return candidate  # type: ignore[return-value, unused-ignore]


def _read_profile_from_toml(path: Path) -> str | None:
    """Read top-level `profile` from .marivo/marivo.toml, or None if absent.

    Wraps malformed-TOML errors as ProfileResolutionError so stdio start
    fails cleanly instead of crashing with a bare TOMLDecodeError.
    """
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileResolutionError(
            ErrorCode.VALIDATION,
            f"Malformed TOML in {path}: {exc}",
        ) from exc
    value = data.get("profile")
    return str(value) if value else None
