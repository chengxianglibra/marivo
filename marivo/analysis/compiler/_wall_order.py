"""Constant-side bounds for the governed order of local source clocks."""

from datetime import datetime, timedelta, timezone, tzinfo

_TICK = timedelta(microseconds=1)


def _offset(point: datetime, zone: tzinfo) -> timedelta:
    offset = point.astimezone(zone).utcoffset()
    assert offset is not None
    return offset


def wall_order_bound(
    instant: datetime, zone: tzinfo, *, strict: bool = False
) -> tuple[datetime, bool]:
    """Return a wall cutoff and whether its lower comparison is inclusive.

    Repeated clocks denote their later instant; skipped clocks denote the
    transition instant. Only a transition adjacent to the boundary is searched,
    with the actual offset difference as its bracket, never a query horizon.
    ``strict`` asks for ``I(clock) > instant`` rather than ``>=``.
    """
    instant = instant.astimezone(timezone.utc)
    local = instant.astimezone(zone)
    wall = local.replace(tzinfo=None)
    earlier = wall.replace(tzinfo=zone, fold=0).utcoffset()
    later = wall.replace(tzinfo=zone, fold=1).utcoffset()
    assert earlier is not None and later is not None
    if earlier > later and local.fold == 0:
        # The boundary is in the first occurrence. All repeated clocks are
        # later than it, including when the comparison itself is strict.
        left, right = instant, instant + (earlier - later)
        original = _offset(left, zone)
        while right - left > _TICK:
            midpoint = left + (right - left) // 2
            if _offset(midpoint, zone) == original:
                left = midpoint
            else:
                right = midpoint
        return right.astimezone(zone).replace(tzinfo=None), True
    if instant > datetime.min.replace(tzinfo=timezone.utc):
        previous = _offset(instant - _TICK, zone)
        current = local.utcoffset()
        assert current is not None
        if current > previous:
            # At a forward transition the entire skipped range, plus the
            # first real clock after it, denotes this same instant.
            return (wall, False) if strict else (wall - (current - previous), True)
    return wall, not strict
