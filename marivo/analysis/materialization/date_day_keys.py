"""Delay qualified DATE day keys until after their source aggregation."""

from collections.abc import Callable, Mapping
from datetime import date, datetime

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.types import CalendarCoordinate
from marivo._timezones import normalize_timezone
from marivo.analysis.compiler.calendar import KEY_TYPE, reusable_date_day
from marivo.analysis.compiler.source_time import _raw_lineage
from marivo.analysis.materialization.time_filter import _raw_for, _resolved
from marivo.datasource._temporal_expressions import localize, timestamp


def _day_signature(day: date, zone: str) -> datetime:
    raise NotImplementedError("qualified source DATE day start")


_day_start: Callable[[ir.DateValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _day_signature,
    name="_marivo_date_day_start",
    signature=((dt.date, dt.string), KEY_TYPE),
)
_DAY_START = type(_day_start(ibis.literal(date(2000, 1, 1)), "UTC").op())


def _native_signature(day: date, precision: int, zone: str) -> datetime:
    raise NotImplementedError("native ClickHouse DATE conversion")


_native_start: Callable[[ir.DateValue, int, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _native_signature,
    name="toDateTime64",
    signature=((dt.date, dt.int64, dt.string), KEY_TYPE),
)


def deferred_day_key(
    table: ir.Table,
    raw: ir.Value,
    calendar: CalendarCoordinate,
    date_timezone: str,
    engine: str,
) -> ir.TimestampValue | None:
    """Mark a physical DATE whose selected day buckets are injective."""
    if not raw.type().is_date() or not _raw_lineage(raw):
        return None
    if not reusable_date_day(calendar, date_timezone, engine):
        return None
    rebound = _raw_for(raw.op(), table.op())
    if not isinstance(rebound, ir.DateValue):
        return None
    return _day_start(rebound, date_timezone)


def has_deferred_day_keys(expression: ir.Expr) -> bool:
    return bool(expression.op().find(_DAY_START))


def lower_day_keys(expression: ir.Expr, engine: str) -> ir.Expr:
    """Group on the same physical DATE, then restore the original UTC key type."""

    def lift(node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object) -> ops.Node:
        value = node.copy(**kwargs)
        if not isinstance(value, ops.Aggregate):
            return value
        groups = dict(value.groups)
        zones: dict[str, str] = {}
        for name, group in value.groups.items():
            key = _resolved(group)
            if type(key) is not _DAY_START:
                continue
            raw, zone = key.args
            assert isinstance(raw, ops.Value) and isinstance(zone, ops.Literal)
            assert isinstance(zone.value, str)
            rebound = _raw_for(raw, value.parent)
            if rebound is not None:
                groups[name] = rebound.op()
                zones[name] = zone.value
        if not zones:
            return value
        grouped = ops.Aggregate(value.parent, groups, value.metrics).to_expr()
        return grouped.mutate(
            **{name: _day_start(grouped[name], zone) for name, zone in zones.items()}
        ).op()

    lifted = expression.op().map(lift)[expression.op()]

    def inline(node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object) -> ops.Node:
        value = node.copy(**kwargs)
        if type(value) is not _DAY_START:
            return value
        raw, zone = value.args
        assert isinstance(raw, ops.Value) and isinstance(zone, ops.Literal)
        assert isinstance(zone.value, str)
        day = raw.to_expr()
        assert isinstance(day, ir.DateValue)
        normalized = normalize_timezone(zone.value)
        if engine == "clickhouse" and normalized.kind != "fixed_offset":
            from marivo.datasource._temporal_lowering import _clickhouse_zone

            return _clickhouse_zone(_native_start(day, 6, normalized.name), "UTC").op()
        return localize(zone.value, timestamp(day)).cast(KEY_TYPE).op()

    return lifted.map(inline)[lifted].to_expr()
