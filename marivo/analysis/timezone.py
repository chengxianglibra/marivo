"""Central timezone resolution for marivo.analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo._timezones import (
    fixed_offset_timezone_candidate,
    parse_timezone,
    system_timezone_candidate,
)
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
    candidate = system_timezone_candidate(allow_fixed_offset=False)
    if candidate is None:
        return _fixed_offset_fallback()
    name, tz, resolution = candidate
    return ResolvedTimezone(name=name, tz=tz, resolution=resolution)


def _fixed_offset_fallback() -> ResolvedTimezone:
    """Build a fixed-offset fallback from the local system clock."""
    name, local_tz, resolution = fixed_offset_timezone_candidate()
    return ResolvedTimezone(
        name=name,
        tz=local_tz,
        resolution=resolution,
        warning="system timezone could not be resolved as IANA; fixed offset fallback is in use",
    )


def restore_timezone(name: str, resolution: str) -> tzinfo:
    """Restore a persisted IANA name or canonical fixed UTC offset."""
    if resolution == "iana":
        return zoneinfo_from_name(name)
    if resolution == "fixed_offset":
        if name == "UTC":
            return timezone.utc
        try:
            normalized, tz = parse_timezone(name)
        except (ValueError, ZoneInfoNotFoundError):
            pass
        else:
            if normalized == name and isinstance(tz, timezone):
                return tz
    raise TimezoneInvalidError(
        message="Persisted report timezone is not a canonical IANA name or fixed UTC offset.",
        context={"kind": "PersistedTimezoneInvalid", "tz": name, "resolution": resolution},
    )
