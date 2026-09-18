"""Concrete lowering of compiler-owned temporal operations without source transfer."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.compiler.source_time import _localize_native, _render_native
from marivo.datasource.timezone import parse_timezone


def _convert_signature(value: datetime, source: str, target: str) -> datetime:
    raise NotImplementedError("native timezone conversion")


def _zone_signature(value: datetime, zone: str) -> datetime:
    raise NotImplementedError("native timezone interpretation")


def _shift_signature(value: datetime, seconds: int) -> datetime:
    raise NotImplementedError("native exact timestamp shift")


def _truncate_signature(value: datetime, unit: str) -> datetime:
    raise NotImplementedError("native temporal bucket")


_mysql_convert: Callable[[ir.TimestampValue, str, str], ir.TimestampValue] = (
    ibis.udf.scalar.builtin(
        _convert_signature,
        name="convert_tz",
        signature=((dt.timestamp, dt.string, dt.string), dt.Timestamp(scale=6)),
    )
)
_trino_localize: Callable[[ir.TimestampValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _zone_signature,
    name="with_timezone",
    signature=((dt.timestamp, dt.string), dt.Timestamp(timezone="UTC", scale=6)),
)
_trino_render: Callable[[ir.TimestampValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _zone_signature,
    name="at_timezone",
    signature=((dt.Timestamp(timezone="UTC"), dt.string), dt.Timestamp(timezone="UTC", scale=6)),
)
_sqlite_localize: Callable[[ir.TimestampValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _zone_signature,
    name="_marivo_localize",
    signature=((dt.timestamp, dt.string), dt.Timestamp(timezone="UTC", scale=6)),
)
_sqlite_render: Callable[[ir.TimestampValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _zone_signature,
    name="_marivo_render",
    signature=((dt.Timestamp(timezone="UTC"), dt.string), dt.Timestamp(scale=6)),
)
_sqlite_truncate: Callable[[ir.TimestampValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _truncate_signature,
    name="_marivo_truncate",
    signature=((dt.timestamp, dt.string), dt.Timestamp(scale=6)),
)
_sqlite_shift: Callable[[ir.TimestampValue, int], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _shift_signature,
    name="_marivo_shift",
    signature=((dt.timestamp, dt.int64), dt.Timestamp(scale=6)),
)
_clickhouse_localize: Callable[[ir.TimestampValue, str], ir.TimestampValue] = (
    ibis.udf.scalar.builtin(
        _zone_signature,
        name="toUTCTimestamp",
        signature=((dt.timestamp, dt.string), dt.Timestamp(timezone="UTC", scale=6)),
    )
)
_clickhouse_render: Callable[[ir.TimestampValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _zone_signature,
    name="fromUTCTimestamp",
    signature=((dt.Timestamp(timezone="UTC"), dt.string), dt.Timestamp(scale=6)),
)
_clickhouse_truncate: Callable[[str, ir.TimestampValue], ir.TimestampValue] = (
    ibis.udf.scalar.builtin(
        _zone_signature,
        name="dateTrunc",
        signature=((dt.string, dt.timestamp), dt.Timestamp(scale=6)),
    )
)

_probe = ibis.timestamp("2000-01-01")
_LOCALIZE = type(_localize_native("UTC", _probe).op())
_RENDER = type(_render_native("UTC", _probe).op())


def governed_temporal_operation(node: ops.Node) -> bool:
    return type(node) in (_LOCALIZE, _RENDER)


def lower_temporal(expression: ir.Expr, engine: str) -> ir.Expr:
    """Replace only the compiler's closed native temporal operations."""

    def rewrite(node: ops.Node, results: dict[ops.Node, ops.Node], **kwargs: object) -> ops.Node:
        value = node.copy(**kwargs)
        if (
            isinstance(value, ops.Literal)
            and isinstance(value.dtype, dt.Timestamp)
            and value.dtype.scale is None
        ):
            return value.copy(dtype=value.dtype.copy(scale=6))
        if governed_temporal_operation(value):
            zone_node, argument = value.args
            assert isinstance(zone_node, ops.Literal) and isinstance(zone_node.value, str)
            assert isinstance(argument, ops.Value)
            zone = zone_node.value
            timestamp = argument.to_expr()
            assert isinstance(timestamp, ir.TimestampValue)
            localize = type(value) is _LOCALIZE
            if engine == "mysql":
                source, target = (zone, "+00:00") if localize else ("+00:00", zone)
                return _mysql_convert(timestamp, source, target).op()
            if engine == "sqlite":
                return (_sqlite_localize if localize else _sqlite_render)(timestamp, zone).op()
            if engine == "trino":
                if localize:
                    return _trino_localize(timestamp, zone).op()
                return _trino_render(timestamp, zone).cast(dt.Timestamp(scale=6)).op()
            if engine == "clickhouse":
                naive = ops.Cast(timestamp.op(), dt.Timestamp(scale=6)).to_expr()
                assert isinstance(naive, ir.TimestampValue)
                return (_clickhouse_localize if localize else _clickhouse_render)(naive, zone).op()
        if engine == "sqlite" and isinstance(value, (ops.TimestampAdd, ops.TimestampSub)):
            interval = value.right
            if (
                isinstance(interval, ops.Literal)
                and isinstance(interval.dtype, dt.Interval)
                and interval.dtype.unit.short in {"s", "m", "h", "D"}
            ):
                seconds = (
                    int(interval.value)
                    * {"s": 1, "m": 60, "h": 3600, "D": 86400}[interval.dtype.unit.short]
                )
                timestamp = value.left.to_expr()
                assert isinstance(timestamp, ir.TimestampValue)
                return _sqlite_shift(
                    timestamp, seconds if isinstance(value, ops.TimestampAdd) else -seconds
                ).op()
        if engine == "sqlite" and isinstance(value, ops.TimestampTruncate):
            timestamp = value.arg.to_expr()
            assert isinstance(timestamp, ir.TimestampValue)
            return _sqlite_truncate(timestamp, value.unit.short).op()
        if engine == "clickhouse" and isinstance(value, ops.TimestampTruncate):
            timestamp = value.arg.to_expr()
            assert isinstance(timestamp, ir.TimestampValue)
            # Ibis wraps dateTrunc in toDateTime, narrowing the supported range.
            return _clickhouse_truncate(value.unit.singular.lower(), timestamp).op()
        return value

    return expression.op().map(rewrite)[expression.op()].to_expr()


def sqlite_localize(value: str | None, zone: str) -> str | None:
    if value is None:
        return None
    wall = datetime.fromisoformat(value)
    instant = wall.replace(tzinfo=parse_timezone(zone)[1]).astimezone(timezone.utc)
    return instant.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def sqlite_render(value: str | None, zone: str) -> str | None:
    if value is None:
        return None
    instant = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return (
        instant.astimezone(parse_timezone(zone)[1])
        .replace(tzinfo=None)
        .isoformat(sep=" ", timespec="microseconds")
    )


def sqlite_truncate(value: str | None, unit: str) -> str | None:
    if value is None:
        return None
    wall = datetime.fromisoformat(value)
    if unit == "h":
        wall = wall.replace(minute=0, second=0, microsecond=0)
    elif unit == "D":
        wall = wall.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError("only qualified hour/day timestamp buckets are supported")
    return wall.isoformat(sep=" ", timespec="microseconds")


def sqlite_shift(value: str | None, seconds: int) -> str | None:
    from datetime import timedelta

    if value is None:
        return None
    return (datetime.fromisoformat(value) + timedelta(seconds=seconds)).isoformat(
        sep=" ", timespec="microseconds"
    )
