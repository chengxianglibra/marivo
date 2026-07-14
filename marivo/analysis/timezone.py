"""Central timezone resolution for marivo.analysis."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo.analysis.errors import TimezoneInvalidError


@dataclass(frozen=True)
class ResolvedTimezone:
    """Result of system timezone resolution with provenance metadata."""

    name: str
    tz: tzinfo
    resolution: str  # 'iana' or 'fixed_offset'
    warning: str | None = None


def zoneinfo_from_name(name: str) -> ZoneInfo:
    """Convert an IANA timezone name to a ZoneInfo, raising TimezoneInvalidError on failure."""
    if not isinstance(name, str):
        raise TimezoneInvalidError(
            message=f"timezone name must be a string, got {type(name).__name__}",
            context={"kind": "TimezoneNameInvalid", "tz": repr(name)},
        )
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise TimezoneInvalidError(
            message=f"timezone {name!r} was not found",
            context={"kind": "TimezoneNotFound", "tz": name},
        ) from exc


def resolve_system_timezone() -> ResolvedTimezone:
    """Resolve the system timezone from environment and OS signals.

    Resolution order:
    1. TZ environment variable (IANA if valid, fallback if invalid).
    2. /etc/localtime symlink pointing into zoneinfo/ (Linux).
    3. Local offset fallback from datetime.now().astimezone().
    """
    env_tz = os.environ.get("TZ")
    if env_tz:
        try:
            return ResolvedTimezone(name=env_tz, tz=zoneinfo_from_name(env_tz), resolution="iana")
        except TimezoneInvalidError:
            return _fixed_offset_fallback()

    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        target = str(localtime.resolve())
        marker = "/zoneinfo/"
        if marker in target:
            candidate = target.split(marker, 1)[1]
            try:
                return ResolvedTimezone(
                    name=candidate, tz=zoneinfo_from_name(candidate), resolution="iana"
                )
            except TimezoneInvalidError:
                return _fixed_offset_fallback()

    return _fixed_offset_fallback()


def _fixed_offset_fallback() -> ResolvedTimezone:
    """Build a fixed-offset fallback from the local system clock."""
    local_tz = datetime.now().astimezone().tzinfo
    if local_tz is None:
        local_tz = ZoneInfo("UTC")
    return ResolvedTimezone(
        name=str(local_tz),
        tz=local_tz,
        resolution="fixed_offset",
        warning="system timezone could not be resolved as IANA; fixed offset fallback is in use",
    )
