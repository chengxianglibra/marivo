"""Dependency-neutral IANA and fixed-offset timezone parsing."""

import re
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo


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
