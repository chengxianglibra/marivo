"""Source-native temporal helpers used by query preparation."""

from __future__ import annotations

from collections.abc import Callable

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir


def _epoch_definition(value: ir.Value) -> ir.Value:
    raise NotImplementedError("SQL builtin")


def _part_definition(part: ir.Value, value: ir.Value) -> ir.Value:
    raise NotImplementedError("SQL builtin")


duration_ticks: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="epoch_us", signature=((dt.Interval("us"),), dt.int64)
)(_epoch_definition)
interval_part: Callable[[ir.Value, ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="date_part", signature=((dt.string, dt.Interval("us")), dt.int64)
)(_part_definition)
