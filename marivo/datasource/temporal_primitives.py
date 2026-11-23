"""Shared temporal expression primitives without provider dispatch."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo._kernel.timezones import normalize_timezone


class TemporalExpressionError(ValueError):
    """An unsupported native temporal expression configuration."""

    def __init__(self, expected: str, received: str, repair: str | None = None) -> None:
        self.expected = expected
        self.received = received
        self.repair = repair
        super().__init__(f"Expected {expected}; received {received}")


def temporal_expression_error(
    expected: str, received: str, *, repair: str | None = None
) -> TemporalExpressionError:
    return TemporalExpressionError(expected, received, repair)


def _timezone_signature(zone: str, value: datetime) -> datetime:
    raise NotImplementedError("native DuckDB timezone signature")


_localize_native: Callable[[str, ir.TimestampValue], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _timezone_signature,
    name="timezone",
    signature=((dt.string, dt.timestamp), dt.Timestamp(timezone="UTC", scale=6)),
)
_render_native: Callable[[str, ir.TimestampValue], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _timezone_signature,
    name="timezone",
    signature=((dt.string, dt.Timestamp(timezone="UTC")), dt.Timestamp(scale=6)),
)


def timestamp(value: ir.Value) -> ir.TimestampValue:
    result = value if isinstance(value, ir.TimestampValue) else value.cast("timestamp")
    if not isinstance(result, ir.TimestampValue):
        raise temporal_expression_error("a temporal value", "invalid timestamp expression")
    return result


def localize(zone: str, value: ir.TimestampValue) -> ir.TimestampValue:
    normalized = normalize_timezone(zone)
    resolved = normalized.tz
    if normalized.kind == "fixed_offset":
        offset = resolved.utcoffset(None)
        if offset is None:
            raise temporal_expression_error("an exact fixed offset", "missing timezone offset")
        return _localize_native("UTC", value - ibis.interval(seconds=int(offset.total_seconds())))
    return _localize_native(zone, value)


def render(zone: str, value: ir.TimestampValue) -> ir.TimestampValue:
    normalized = normalize_timezone(zone)
    resolved = normalized.tz
    if normalized.kind == "fixed_offset":
        offset = resolved.utcoffset(None)
        if offset is None:
            raise temporal_expression_error("an exact fixed offset", "missing timezone offset")
        return _render_native("UTC", value) + ibis.interval(seconds=int(offset.total_seconds()))
    return _render_native(zone, value)
