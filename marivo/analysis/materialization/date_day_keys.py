"""Delay qualified source day keys until after their source aggregation."""

# Keep __annotations__ in the class namespace for Ibis 12 on Python 3.14.
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.rules as rlz
import ibis.expr.types as ir

from marivo._definition.types import CalendarCoordinate
from marivo._timezones import normalize_timezone
from marivo.analysis.compiler.calendar import KEY_TYPE, reusable_date_day
from marivo.analysis.compiler.source_time import _raw_lineage, source_time
from marivo.analysis.core.time_authority import SourceTimeAuthority
from marivo.analysis.materialization.time_filter import _raw_for, _resolved
from marivo.datasource._temporal_expressions import localize, timestamp
from marivo.semantic.ir import StrptimeParse, TargetDimensionContract


class _DeferredDayKey(ops.Value):  # type: ignore[misc]  # Ibis operation bases have no typing stubs.
    """Keep a raw grouping key and its exact native restoration expression."""

    raw: ops.Value
    key: ops.Value
    dtype = KEY_TYPE
    shape = rlz.shape_like("raw")


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
    *,
    encoded: tuple[TargetDimensionContract, SourceTimeAuthority] | None = None,
) -> ir.TimestampValue | None:
    """Mark a physical DATE or canonical day encoding with an injective day key."""
    if not _raw_lineage(raw) or not reusable_date_day(calendar, date_timezone, engine):
        return None
    rebound = _raw_for(raw.op(), table.op())
    if rebound is None:
        return None
    if encoded is not None:
        axis, authority = encoded
        if (
            not isinstance(axis.parse, StrptimeParse)
            or axis.parse.format not in ("%Y%m%d", "%Y-%m-%d")
            or not (
                raw.type().is_string()
                or (raw.type().is_integer() and axis.parse.format == "%Y%m%d")
            )
            or authority.kind == "instant"
        ):
            return None
        key, _ = source_time(
            rebound,
            axis,
            boundary_timezone="UTC",
            source_timezone=None,
            engine=engine,
            frozen=authority,
        )
        if key.type().is_date():
            key = localize(date_timezone, timestamp(key))
    elif isinstance(rebound, ir.DateValue):
        normalized = normalize_timezone(date_timezone)
        if engine == "clickhouse" and normalized.kind != "fixed_offset":
            from marivo.datasource._temporal_lowering import _clickhouse_zone

            key = _clickhouse_zone(_native_start(rebound, 6, normalized.name), "UTC")
        else:
            key = localize(date_timezone, timestamp(rebound))
    else:
        return None
    return _DeferredDayKey(rebound.op(), key.cast(KEY_TYPE).op()).to_expr()


def has_deferred_day_keys(expression: ir.Expr) -> bool:
    return bool(expression.op().find(_DeferredDayKey))


def lower_day_keys(expression: ir.Expr) -> ir.Expr:
    """Group on raw source days and restore UTC keys on the aggregate results."""

    def lift(node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object) -> ops.Node:
        value = node.copy(**kwargs)
        if not isinstance(value, ops.Aggregate):
            return value
        groups = dict(value.groups)
        keys: dict[str, _DeferredDayKey] = {}
        for name, group in value.groups.items():
            key = _resolved(group)
            if not isinstance(key, _DeferredDayKey):
                continue
            rebound = _raw_for(key.raw, value.parent)
            if rebound is not None:
                groups[name] = rebound.op()
                keys[name] = key
        if not keys:
            return value
        grouped = ops.Aggregate(value.parent, groups, value.metrics).to_expr()
        return grouped.mutate(
            **{
                name: key.key.replace({key.raw: grouped[name].op()}).to_expr()
                for name, key in keys.items()
            }
        ).op()

    lifted = expression.op().map(lift)[expression.op()]

    def inline(node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object) -> ops.Node:
        value = node.copy(**kwargs)
        return value.key if isinstance(value, _DeferredDayKey) else value

    return lifted.map(inline)[lifted].to_expr()
