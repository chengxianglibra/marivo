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
    """Replace only the compiler's closed native temporal operations.

    The rewrite reads every child through ``kwargs``. Ibis operations may carry
    a ``results`` argument, so the traversal's result map uses a distinct name.
    """

    from marivo.analysis.materialization.time_filter import cancel_utc, optimize_time

    expression = optimize_time(cancel_utc(expression))

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
                naive = ops.Cast(timestamp.op(), dt.Timestamp(scale=6)).to_expr()
                assert isinstance(naive, ir.TimestampValue)
                return (_clickhouse_localize if localize else _clickhouse_render)(naive, zone).op()
        if engine == "clickhouse" and isinstance(value, ops.TimestampTruncate):
            timestamp = value.arg.to_expr()
            assert isinstance(timestamp, ir.TimestampValue)
            # Ibis wraps dateTrunc in toDateTime, narrowing the supported range.
            return _clickhouse_truncate(value.unit.singular.lower(), timestamp).op()
        return value

    return expression.op().map(rewrite)[expression.op()].to_expr()
