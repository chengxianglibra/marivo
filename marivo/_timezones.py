"""Dependency-neutral IANA and fixed-offset timezone parsing."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import timedelta, timezone, tzinfo
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_LOCALTIME_PATH = Path("/etc/localtime")
_SYSTEM_PLATFORM = sys.platform
_UTC_NAMES = frozenset(
    {
        "UTC",
        "Etc/UTC",
        "Etc/UCT",
        "UCT",
        "Etc/Universal",
        "Universal",
        "Etc/Zulu",
        "Zulu",
        "GMT",
        "Etc/GMT",
        "Etc/GMT0",
        "Etc/GMT+0",
        "Etc/GMT-0",
        "GMT0",
        "GMT+0",
        "GMT-0",
        "Etc/Greenwich",
        "Greenwich",
    }
)


@dataclass(frozen=True, slots=True)
class NormalizedTimezone:
    """A canonical timezone identity and its explicit implementation kind."""

    name: str
    tz: tzinfo
    kind: Literal["iana", "fixed_offset"]


def _offset_timezone(seconds: int) -> NormalizedTimezone:
    if seconds == 0:
        return NormalizedTimezone("UTC", ZoneInfo("UTC"), "iana")
    absolute = abs(seconds)
    hours, remainder = divmod(absolute, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    suffix = f":{remaining_seconds:02d}" if remaining_seconds else ""
    sign = "+" if seconds > 0 else "-"
    return NormalizedTimezone(
        f"UTC{sign}{hours:02d}:{minutes:02d}{suffix}",
        timezone(timedelta(seconds=seconds)),
        "fixed_offset",
    )


def normalize_timezone(value: str) -> NormalizedTimezone:
    """Normalize an IANA name or an unambiguous explicit UTC offset.

    UTC links and fixed-offset spellings share one identity. Other IANA links
    retain their given names; no host clock or source data is consulted.
    """
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value == "localtime"
        or value.startswith(("/", ":"))
    ):
        raise ValueError("expected an IANA timezone name or explicit UTC offset")
    offset = value[3:] if value.startswith("UTC") else value
    if re.fullmatch(r"[+-]\d{2}:\d{2}(?::\d{2})?", offset):
        hours, minutes, *seconds = (int(part) for part in offset[1:].split(":"))
        second = seconds[0] if seconds else 0
        if hours >= 24 or minutes >= 60 or second >= 60:
            raise ValueError("invalid fixed timezone offset")
        total = hours * 3600 + minutes * 60 + second
        return _offset_timezone(total if offset[0] == "+" else -total)
    if value in _UTC_NAMES:
        return NormalizedTimezone("UTC", ZoneInfo("UTC"), "iana")
    zone = ZoneInfo(value)
    if re.fullmatch(r"Etc/GMT[+-]\d{1,2}", value):
        return _offset_timezone(-int(value[7:]) * 3600)
    return NormalizedTimezone(value, zone, "iana")


def same_timezone(left: str, right: str) -> bool:
    """Compare normalized timezone identities, without merging other IANA links."""
    return normalize_timezone(left).name == normalize_timezone(right).name


class SystemTimezoneUnresolvedError(ValueError):
    """A system timezone signal that does not identify a loadable IANA zone."""

    def __init__(self, *, source: Literal["TZ", "localtime", "platform"], received: str) -> None:
        self.source = source
        self.received = received
        super().__init__(f"{source}: {received}")


def _system_iana(value: str, *, source: Literal["TZ", "localtime"]) -> NormalizedTimezone:
    try:
        if not value or value != value.strip() or value == "localtime":
            raise ValueError("not a system IANA name")
        # Check the original system signal before accepting explicit-offset syntax.
        ZoneInfo(value)
        return normalize_timezone(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise SystemTimezoneUnresolvedError(source=source, received=value) from exc


def resolve_system_timezone() -> NormalizedTimezone:
    """Resolve one strict IANA system signal, without libc or clock fallbacks."""
    if "TZ" in os.environ:
        original = os.environ["TZ"]
        candidate = original[1:] if original.startswith(":") else original
        try:
            return _system_iana(candidate, source="TZ")
        except SystemTimezoneUnresolvedError as exc:
            raise SystemTimezoneUnresolvedError(source="TZ", received=original) from exc
    if _SYSTEM_PLATFORM == "win32":
        raise SystemTimezoneUnresolvedError(source="platform", received="Windows without TZ")
    localtime = _LOCALTIME_PATH
    if localtime.is_symlink():
        if not localtime.exists():
            raise SystemTimezoneUnresolvedError(source="localtime", received="broken symlink")
        target = str(localtime.resolve())
        marker = "/zoneinfo/"
        if marker not in target:
            raise SystemTimezoneUnresolvedError(
                source="localtime", received="symlink outside zoneinfo"
            )
        return _system_iana(target.split(marker, 1)[1], source="localtime")
    if not localtime.exists():
        return normalize_timezone("UTC")
    raise SystemTimezoneUnresolvedError(source="localtime", received="not a zoneinfo symlink")
