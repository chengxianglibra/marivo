"""Concrete lowering of compiler-owned temporal operations without source transfer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.compiler.source_time import _localize_native, _render_native


def _convert_signature(value: datetime, source: str, target: str) -> datetime:
    raise NotImplementedError("native timezone conversion")


def _zone_signature(value: datetime, zone: str) -> datetime:
    raise NotImplementedError("native timezone interpretation")


def _clock_signature(value: str, precision: int, zone: str) -> datetime:
    raise NotImplementedError("native microsecond clock interpretation")


_mysql_convert: Callable[[ir.TimestampValue, str, str], ir.TimestampValue] = (
    ibis.udf.scalar.builtin(
        _convert_signature,
        name="convert_tz",
        signature=((dt.timestamp, dt.string, dt.string), dt.Timestamp(scale=6)),
    )
)
_mysql_localize: Callable[[ir.TimestampValue, str, str], ir.TimestampValue] = (
    ibis.udf.scalar.builtin(
        _convert_signature,
        name="convert_tz",
        signature=((dt.timestamp, dt.string, dt.string), dt.Timestamp(timezone="UTC", scale=6)),
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
_clickhouse_text: Callable[[ir.TimestampValue, str], ir.StringValue] = ibis.udf.scalar.builtin(
    _zone_signature,
    name="toString",
    signature=((dt.Timestamp(timezone="UTC", scale=6), dt.string), dt.string),
)
_clickhouse_clock: Callable[[ir.StringValue, int, str], ir.TimestampValue] = (
    ibis.udf.scalar.builtin(
        _clock_signature,
        name="toDateTime64",
        signature=((dt.string, dt.int64, dt.string), dt.Timestamp(timezone="UTC", scale=6)),
    )
)
_clickhouse_zone: Callable[[ir.TimestampValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _zone_signature,
    name="toTimeZone",
    signature=(
        (dt.Timestamp(timezone="UTC", scale=6), dt.string),
        dt.Timestamp(timezone="UTC", scale=6),
    ),
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
    """Replace only the compiler's closed native temporal operations.

    The rewrite reads every child through ``kwargs``. Ibis operations may carry
    a ``results`` argument, so the traversal's result map uses a distinct name.
    """

    from marivo.analysis.materialization.time_filter import cancel_utc, optimize_time

    # Most numeric reads have no temporal rewrite. Inspect without rebuilding
    # the whole graph three times, including its ordinary filter predicates.
    candidates = expression.op().find((_LOCALIZE, _RENDER, ops.Literal, ops.TimestampTruncate))
    if not any(
        governed_temporal_operation(node)
        or (
            isinstance(node, ops.Literal)
            and isinstance(node.dtype, dt.Timestamp)
            and node.dtype.scale is None
        )
        or (engine == "clickhouse" and isinstance(node, ops.TimestampTruncate))
        for node in candidates
    ):
        return expression
    # Generic UTC cancellation can cast a civil clock to a zoneless timestamp.
    # ClickHouse must retain the UTC-labelled carrier even for UTC calendars.
    expression = optimize_time(expression if engine == "clickhouse" else cancel_utc(expression))

    def rewrite(
        node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object
    ) -> ops.Node:
        if engine == "clickhouse" and type(node) is _LOCALIZE:
            identity_zone, rendered = node.args
            if type(rendered) is _RENDER:
                render_zone, instant = rendered.args
                if identity_zone == render_zone:
                    # Civil clocks lose the fold identity. Keep the original
                    # instant when this compiler pair returns to the same zone.
                    assert isinstance(instant, ops.Value)
                    return _results[instant]
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
                zone = "+00:00" if zone == "UTC" else zone
                source, target = (zone, "+00:00") if localize else ("+00:00", zone)
                convert = _mysql_localize if localize else _mysql_convert
                return convert(timestamp, source, target).op()
            if engine == "trino":
                if localize:
                    return _trino_localize(timestamp, zone).op()
                return _trino_render(timestamp, zone).cast(dt.Timestamp(scale=6)).op()
            if engine == "clickhouse":
                carrier = timestamp.cast(dt.Timestamp(timezone="UTC", scale=6))
                assert isinstance(carrier, ir.TimestampValue)
                text = _clickhouse_text(carrier, "UTC" if localize else zone)
                converted = _clickhouse_clock(text, 6, zone if localize else "UTC")
                result = _clickhouse_zone(converted, "UTC")
                # A naive DateTime64 adopts the ClickHouse server zone, so keep the UTC label.
                return result.op()
        if engine == "clickhouse" and isinstance(value, ops.TimestampTruncate):
            argument = value.arg
            if (
                isinstance(argument, ops.Cast)
                and argument.arg.dtype.is_date()
                and isinstance(argument.to, dt.Timestamp)
                and argument.to.timezone is None
            ):
                # DATE is already civil: avoid interpreting midnight in the server zone.
                timestamp = argument.arg.to_expr().cast(dt.Timestamp(timezone="UTC", scale=6))
            else:
                timestamp = argument.to_expr()
            assert isinstance(timestamp, ir.TimestampValue)
            # Ibis wraps dateTrunc in toDateTime, narrowing the supported range.
            return _clickhouse_truncate(value.unit.singular.lower(), timestamp).op()
        return value

    return expression.op().map(rewrite)[expression.op()].to_expr()
