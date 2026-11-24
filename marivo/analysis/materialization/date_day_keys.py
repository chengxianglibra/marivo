"""Delay qualified source day keys until after their source aggregation."""

# Keep __annotations__ in the class namespace for Ibis 12 on Python 3.14.
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import ibis.expr.operations as ops
import ibis.expr.rules as rlz
import ibis.expr.types as ir

from marivo._definition._ibis_typing import ValueOperation
from marivo._definition.types import CalendarCoordinate
from marivo.analysis.compiler.calendar import KEY_TYPE, reusable_date_day
from marivo.analysis.compiler.source_time import _raw_lineage, source_time
from marivo.analysis.materialization.time_filter import _raw_for, _resolved
from marivo.analysis.model.time_authority import SourceTimeAuthority
from marivo.datasource.spi import localize, query_dialect, timestamp
from marivo.semantic.spi import (
    StrptimeParse,
    TargetDimensionContract,
)


class _DeferredDayKey(ValueOperation):
    """Keep a raw grouping key and its exact native restoration expression."""

    raw: ops.Value
    key: ops.Value
    dtype = KEY_TYPE
    shape = rlz.shape_like("raw")

    if TYPE_CHECKING:

        def __init__(self, raw: ops.Value, key: ops.Value) -> None: ...


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
        key = query_dialect(engine).utc_day(rebound, date_timezone)
    else:
        return None
    return _DeferredDayKey(rebound.op(), key.cast(KEY_TYPE).op()).to_expr()


def physical_day_key(value: ops.Value, engine: str) -> ops.Value | None:
    """Return the raw DATE for an exact UTC day carrier."""
    if isinstance(value, _DeferredDayKey) and isinstance(raw := value.raw.to_expr(), ir.DateValue):
        expected = query_dialect(engine).utc_day(raw, "UTC").cast(KEY_TYPE)
        if expected.op() == value.key:
            return value.raw
    return None


def has_deferred_day_keys(expression: ir.Expr) -> bool:
    return bool(expression.op().find(_DeferredDayKey))


def lower_day_keys(expression: ir.Expr, *, lift_aggregates: bool = True) -> ir.Expr:
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

    root = expression.op()
    lifted = root.map(lift)[root] if lift_aggregates else root

    def inline(node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object) -> ops.Node:
        value = node.copy(**kwargs)
        return value.key if isinstance(value, _DeferredDayKey) else value

    return lifted.map(inline)[lifted].to_expr()
