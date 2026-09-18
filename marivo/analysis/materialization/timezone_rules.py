"""Exact ZoneInfo offset intervals for bounded source-side temporal validation."""

from __future__ import annotations

import re
import struct
import zoneinfo
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from importlib.resources import files
from itertools import pairwise
from pathlib import Path

from dateutil.tz import tzstr

_EPOCH = datetime(1970, 1, 1)


@dataclass(frozen=True)
class OffsetInterval:
    start: datetime
    end: datetime
    seconds: int


def _tzif(key: str) -> bytes:
    for directory in zoneinfo.TZPATH:
        path = Path(directory) / key
        if path.is_file():
            return path.read_bytes()
    return files("tzdata.zoneinfo").joinpath(*key.split("/")).read_bytes()


def _transitions(data: bytes) -> tuple[tuple[int, ...], str]:
    """Read TZif transition instants and its POSIX continuation, not its offsets."""
    position = 0
    width = 4
    while True:
        if data[position : position + 4] != b"TZif":
            raise ValueError("invalid TZif header")
        version = data[position + 4 : position + 5]
        utc, standard, leaps, count, types, chars = struct.unpack_from(">6I", data, position + 20)
        start = position + 44
        end = start + count * (width + 1) + types * 6 + chars + leaps * (width + 4) + standard + utc
        if width == 4 and version in (b"2", b"3", b"4"):
            position, width = end, 8
            continue
        values = struct.unpack_from(f">{count}{'q' if width == 8 else 'i'}", data, start)
        transitions = tuple(int(value) for value in values)
        tail = data[end:].strip(b"\n").decode("ascii") if width == 8 else ""
        return transitions, tail


def _future_transitions(tail: str, first: int, last: int) -> tuple[datetime, ...]:
    if not tail or "," not in tail:
        return ()
    # dateutil exposes POSIX transitions in standard-wall coordinates. Numeric
    # angle-bracket abbreviations are names only and do not affect the rule.
    normalized = re.sub(r"<[^>]+>", "STD", tail, count=1)
    normalized = re.sub(r"<[^>]+>", "DST", normalized, count=1)
    match = re.match(r"[A-Za-z]+([+-]?)(\d+)(?::(\d+))?(?::(\d+))?", normalized)
    if match is None:
        raise ValueError("unreadable POSIX standard offset")
    sign, hours, minutes, seconds = match.groups()
    west = (int(hours) * 3600 + int(minutes or 0) * 60 + int(seconds or 0)) * (
        -1 if sign == "-" else 1
    )
    rule = tzstr(normalized, posix_offset=True)
    result: list[datetime] = []
    for year in range(first, last + 1):
        pair = rule.transitions(year)
        if pair is None:
            raise ValueError("missing POSIX continuation transitions")
        result.extend(value + timedelta(seconds=west) for value in pair)
    return tuple(result)


def offset_intervals(zone: tzinfo, start: datetime, end: datetime) -> tuple[OffsetInterval, ...]:
    """Partition UTC [start, end) using the runtime ZoneInfo rules, to the second."""
    fixed = zone.utcoffset(None)
    if fixed is not None:
        return (OffsetInterval(start, end, int(fixed.total_seconds())),)
    if not isinstance(zone, zoneinfo.ZoneInfo) or zone.key is None:
        raise ValueError("a named runtime ZoneInfo is required")
    values, tail = _transitions(_tzif(zone.key))
    points = {start, end}
    lower = int((start - _EPOCH).total_seconds())
    upper = int((end - _EPOCH).total_seconds())
    points.update(_EPOCH + timedelta(seconds=value) for value in values if lower < value < upper)
    final = values[-1] if values else None
    if final is None or upper > final:
        points.update(
            value
            for value in _future_transitions(tail, max(1, start.year - 1), min(9998, end.year + 1))
            if start < value < end and (final is None or (value - _EPOCH).total_seconds() > final)
        )
    ordered = sorted(points)
    result = []
    for left, right in pairwise(ordered):
        offset = left.replace(tzinfo=timezone.utc).astimezone(zone).utcoffset()
        if offset is None:
            raise ValueError("missing runtime timezone offset")
        # Keep ZoneInfo as the offset authority; TZif only locates boundaries.
        result.append(OffsetInterval(left, right, int(offset.total_seconds())))
    return tuple(result)
