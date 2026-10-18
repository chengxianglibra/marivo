"""Dependency-neutral IANA and fixed-offset timezone parsing."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SystemTimezoneCandidate = tuple[str, tzinfo, Literal["iana", "fixed_offset"]]


def parse_timezone(value: str) -> tuple[str, tzinfo]:
    """Normalize IANA or explicit offsets without guessing timezone abbreviations."""
    offset = value[3:] if value.startswith("UTC") else value
    if re.fullmatch(r"[+-]\d{2}:\d{2}(?::\d{2})?", offset):
        hours, minutes, *seconds = (int(part) for part in offset[1:].split(":"))
        if hours >= 24 or minutes >= 60 or (seconds and seconds[0] >= 60):
            raise ValueError("invalid fixed timezone offset")
        zone = datetime.fromisoformat("2000-01-01T00:00:00" + offset).tzinfo
        if zone is None:
            raise ValueError("missing fixed timezone offset")
        return "UTC" + offset, zone
    return value, ZoneInfo(value)


def system_timezone_candidate(*, allow_fixed_offset: bool) -> _SystemTimezoneCandidate | None:
    """Resolve an environment or symlink timezone without owning fallback policy."""
    env_tz = os.environ.get("TZ")
    if env_tz:
        if allow_fixed_offset:
            try:
                name, tz = parse_timezone(env_tz)
            except (ValueError, ZoneInfoNotFoundError):
                return None
            resolution: Literal["iana", "fixed_offset"] = (
                "fixed_offset" if isinstance(tz, timezone) else "iana"
            )
            return name, tz, resolution
        try:
            return env_tz, ZoneInfo(env_tz), "iana"
        except ZoneInfoNotFoundError:
            return None

    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        target = str(localtime.resolve())
        marker = "/zoneinfo/"
        if marker in target:
            candidate = target.split(marker, 1)[1]
            try:
                return candidate, ZoneInfo(candidate), "iana"
            except ZoneInfoNotFoundError:
                return None
    return None


def fixed_offset_timezone_candidate() -> _SystemTimezoneCandidate:
    """Build a fixed-offset candidate from the local system clock."""
    local_tz = datetime.now().astimezone().tzinfo
    if local_tz is None:
        local_tz = ZoneInfo("UTC")
    return (
        str(timezone(local_tz.utcoffset(None) or timedelta(0))),
        local_tz,
        "fixed_offset",
    )
