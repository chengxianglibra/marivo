"""Pure source expressions for certified buckets and cumulative evaluation bounds."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir

from marivo._temporal import Grain, PeriodCalendarSnapshotV1, builtin_grain
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.predicates import _boolean
from marivo.semantic.metric_graph import CumulativeAnchorV1


def bucket(
    value: ir.Value, grain: Grain | None, snapshot: PeriodCalendarSnapshotV1 | None = None
) -> ir.Value:
    if grain is not None and grain.kind == "semantic":
        if snapshot is None or snapshot.calendar_ref != grain.calendar:
            raise compilation_error(
                "the exact certified calendar snapshot", "missing period authority"
            )
        if grain.level == "day":
            return value.cast("date").cast(value.type())
        cases = tuple(
            (
                _boolean((value >= period.start_date) & (value < period.end_date)),
                ibis.literal(period.start_date).cast(value.type()),
            )
            for period in snapshot.periods
            if period.level_name == grain.level
        )
        if not cases:
            raise compilation_error("a certified calendar level", "missing calendar periods")
        return ibis.cases(*cases, else_=ibis.null().cast(value.type()))
    if grain is None or grain.unit is None or grain.count is None:
        raise compilation_error(
            "registered builtin source time bucket", "missing certified bucket implementation"
        )
    units = {
        "second": "s",
        "minute": "m",
        "hour": "h",
        "day": "D",
        "week": "W",
        "month": "M",
        "quarter": "Q",
        "year": "Y",
    }
    if not isinstance(value, (ir.DateValue, ir.TimestampValue)):
        raise compilation_error("governed date or timestamp bucket", "non-temporal coordinate")
    if grain.count == 1:
        return value.truncate(units[grain.unit])
    timestamp = value.cast("timestamp")
    if not isinstance(timestamp, ir.TimestampValue):
        raise compilation_error("timestamp bucket input", "invalid time representation")
    interval = ibis.interval(**{grain.unit + "s": grain.count})
    bucket = timestamp.bucket(interval)
    return bucket.cast("date") if isinstance(value, ir.DateValue) else bucket


def bucket_end(
    start: ir.Value, grain: Grain, snapshot: PeriodCalendarSnapshotV1 | None
) -> ir.Value:
    if grain.kind == "builtin":
        if grain.unit is None or grain.count is None:
            raise compilation_error("an exact builtin grain", "missing bucket width")
        return start.cast("timestamp") + ibis.interval(**{grain.unit + "s": grain.count})
    if snapshot is None or snapshot.calendar_ref != grain.calendar:
        raise compilation_error("the bound certified calendar", "missing endpoint authority")
    if grain.level == "day":
        return start.cast("timestamp") + ibis.interval(days=1)
    cases = tuple(
        (
            _boolean(start == period.start_date),
            ibis.literal(period.end_date).cast("timestamp"),
        )
        for period in snapshot.periods
        if period.level_name == grain.level
    )
    if not cases:
        raise compilation_error("certified endpoint periods", "missing calendar level")
    return ibis.cases(*cases, else_=ibis.null().cast("timestamp"))


def endpoint_reset_start(
    end: ir.Value, grain: Grain, snapshot: PeriodCalendarSnapshotV1 | None
) -> ir.Value:
    """Select the reset period immediately before an exclusive endpoint."""
    if grain.kind == "semantic" and grain.level != "day":
        if snapshot is None or snapshot.calendar_ref != grain.calendar:
            raise compilation_error("the bound reset calendar", "missing reset authority")
        cases = tuple(
            (
                _boolean((end > period.start_date) & (end <= period.end_date)),
                ibis.literal(period.start_date).cast("timestamp"),
            )
            for period in snapshot.periods
            if period.level_name == grain.level
        )
        if not cases:
            raise compilation_error("certified reset periods", "missing calendar level")
        return ibis.cases(*cases, else_=ibis.null().cast("timestamp"))
    start = bucket(end, grain, snapshot).cast("timestamp")
    unit, count = (grain.unit, grain.count) if grain.kind == "builtin" else ("day", 1)
    if unit is None or count is None:
        raise compilation_error("an exact reset grain", "missing reset width")
    previous = start - ibis.interval(**{unit + "s": count})
    return (start == end).ifelse(previous, start)


def cumulative_start(
    anchor: CumulativeAnchorV1,
    end: ir.Value,
    snapshot: PeriodCalendarSnapshotV1 | None,
    *,
    bucket_start: ir.Value | None = None,
) -> ir.Value | None:
    """Resolve the authored lower bound for a bucket or one exclusive endpoint."""
    if anchor == "all_history":
        return None
    if anchor[0] == "trailing":
        return end - ibis.interval(**{anchor[2] + "s": anchor[1]})
    reset = builtin_grain(anchor[1]) if isinstance(anchor[1], str) else anchor[1]
    if bucket_start is not None:
        return bucket(bucket_start, reset, snapshot)
    return endpoint_reset_start(end, reset, snapshot)
