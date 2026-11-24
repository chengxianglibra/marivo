"""Native Frame statistics whose local arithmetic and validity are preserved."""

from collections.abc import Callable

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo._definition.query import (
    PairStatistic,
    ReduceValues,
    SelectValues,
    Statistic,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValuePartition,
    ValueSlot,
)
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.compiler.source_predicates import aggregate_returned_keys, string_bytes
from marivo.analysis.core.value_types import ibis_type
from marivo.analysis.model.data_schema import DataSchema
from marivo.datasource.spi import SourceSession


def _slot(expression: ir.Table, schema: DataSchema, item: ValueExpression) -> ir.Value | None:
    if isinstance(item, ValueSlot):
        return expression[schema.values[item.index].slot_id]
    if isinstance(item, ValueLiteral):
        return ibis.literal(item.value)
    return None


def _partition_keys(schema: DataSchema, positions: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(
        column.slot_id for position in positions for column in schema.axes[position].columns
    )


def _statistic(
    expression: ir.Table,
    schema: DataSchema,
    statistic: Statistic | PairStatistic,
    *,
    singleton: bool,
    wrap_int64: bool,
) -> ir.Value | None:
    if not isinstance(statistic, Statistic):
        return None
    if statistic.method == "row_count":
        return expression.count()
    assert statistic.value is not None
    value = _slot(expression, schema, statistic.value)
    if value is None:
        return None
    if statistic.method == "sum" and wrap_int64 and value.type().is_int64():
        return value.sum()
    if not singleton or not (value.type().is_integer() or value.type().is_decimal()):
        return None
    # A singleton's sum/mean is its sole valid value. MAX keeps the exact
    # carrier and avoids choosing the source's general SUM or AVG accumulator.
    return value.max()


def _complete_statistic(
    aggregate: ir.Value,
    count: ir.Value,
    dtype: dt.DataType,
    *,
    empty_zero: bool,
) -> ir.Value:
    result = aggregate.cast(dtype)
    if not empty_zero:
        return result
    return (count == 0).ifelse(ibis.literal(0, type=dtype), result)


def lower_reduce(
    expression: ir.Table, program: FrameProgram, owner: SourceSession
) -> ir.Table | str:
    """Preserve current-row reducer carriers, erase presence and keep scalar empties."""
    operation = program.operation
    assert isinstance(operation, ReduceValues)
    schema = program.input_schema
    output = program.output_schema
    keys = _partition_keys(schema, operation.positions)
    singleton = set(schema.key_fields) <= set(keys)
    metrics: dict[str, ir.Value] = {}
    for column, (_, statistic) in zip(output.values, operation.values, strict=True):
        aggregate = _statistic(
            expression,
            schema,
            statistic,
            singleton=singleton,
            wrap_int64=owner.provider.dialect.frame_int64_sum_wraps,
        )
        if aggregate is None:
            return "current-row reduction retains its local arithmetic kernel"
        metrics[column.slot_id] = _complete_statistic(
            aggregate,
            expression.count(),
            ibis_type(column.dtype),
            empty_zero=not keys and isinstance(statistic, Statistic) and statistic.method == "sum",
        )
    return aggregate_returned_keys(expression, keys, metrics, owner.provider.name)


def _window_value(
    expression: ir.Table,
    schema: DataSchema,
    item: ValuePartition,
    owner: SourceSession,
) -> ir.Value | None:
    names = _partition_keys(schema, item.positions)
    keys = tuple(
        string_bytes(expression[name], owner.provider.name)
        if expression[name].type().is_string()
        else expression[name]
        for name in names
    )
    aggregate = _statistic(
        expression,
        schema,
        item.statistic,
        singleton=set(schema.key_fields) <= set(names),
        wrap_int64=owner.provider.dialect.frame_int64_sum_wraps
        and owner.provider.dialect.window_frame_nulls_safe,
    )
    return None if aggregate is None else aggregate.over(ibis.window(group_by=keys))


def _normalized_share(
    item: ValueBinary,
    evaluate_partition: Callable[[ValuePartition], ir.Value | None],
    expression: ir.Table,
    schema: DataSchema,
) -> ir.Value | None:
    if not (
        item.operation == "div"
        and isinstance(item.right, ValuePartition)
        and isinstance(item.right.statistic, Statistic)
        and item.right.statistic.method == "sum"
        and item.right.statistic.value == item.left
    ):
        return None
    numerator = _slot(expression, schema, item.left)
    denominator = evaluate_partition(item.right)
    if numerator is None or denominator is None or not numerator.type().is_integer():
        return None
    numerator = numerator.cast("float64")
    denominator = denominator.cast("float64")
    return numerator / denominator.nullif(0.0)


def has_partition(item: ValueExpression) -> bool:
    return isinstance(item, ValuePartition) or (
        isinstance(item, ValueBinary) and isinstance(item.right, ValuePartition)
    )


def lower_over(
    expression: ir.Table, program: FrameProgram, owner: SourceSession, *, order: tuple[str, ...]
) -> ir.Table | str:
    """Project supported broadcasts over the exact existing coordinates."""
    if not owner.provider.dialect.supports_sql_window_frames:
        return "this source has no qualified SQL window frame"
    operation = program.operation
    assert isinstance(operation, SelectValues)
    schema, output = program.input_schema, program.output_schema

    def partition(item: ValuePartition) -> ir.Value | None:
        return _window_value(expression, schema, item, owner)

    projections = {
        name: expression[name]
        for name in (*schema.key_fields, *(column.slot_id for column in schema.presence), *order)
    }
    for column, (_, item) in zip(output.values, operation.values, strict=True):
        value = (
            partition(item)
            if isinstance(item, ValuePartition)
            else _normalized_share(item, partition, expression, schema)
            if isinstance(item, ValueBinary)
            else _slot(expression, schema, item)
        )
        if value is None:
            return "partition retains its local arithmetic kernel"
        projections[column.slot_id] = value.cast(ibis_type(column.dtype))
    return expression.select(**projections)
