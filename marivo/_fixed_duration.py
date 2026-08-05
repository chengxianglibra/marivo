"""Dependency-neutral fixed-duration normalization."""

from __future__ import annotations

_FIXED_UNIT_SECONDS: dict[str, int] = {
    "second": 1,
    "minute": 60,
    "hour": 3_600,
    "day": 86_400,
    "week": 604_800,
}


def fixed_duration_seconds(count: int, unit: str) -> int:
    """Return the exact second span for one positive fixed-duration value."""

    if type(count) is not int or count <= 0:
        raise ValueError("fixed duration count must be a positive integer")
    try:
        unit_seconds = _FIXED_UNIT_SECONDS[unit]
    except KeyError as exc:
        raise ValueError(f"unsupported fixed duration unit: {unit!r}") from exc
    return count * unit_seconds


__all__ = ["fixed_duration_seconds"]
