"""Compile explicit source attribute reads, literals and current scalar expressions."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ir

from marivo._definition.query import (
    CoordinateSlot,
    LiteralValues,
    PresenceSlot,
    ReadFields,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo._definition.relations import ReadQuery
from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.compiler.graph_plan import (
    LocalCompute,
    QueryPlan,
    SourceField,
    SourceSchema,
    plan_query,
)
from marivo.analysis.compiler.observation import data_type
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.graph import BoundGraph, read_input_graph
from marivo.analysis.core.model import reject

if TYPE_CHECKING:
    from marivo.analysis.compiler.optimization import InvocationMemo


def endpoint(graph: BoundGraph, plan: QueryPlan) -> ComparisonEndpoint:
    stage = next(item for item in plan.stages if item.stage_id == plan.publish)
    if (
        isinstance(stage, LocalCompute)
        and stage.result is not None
        and stage.result.output_layout is not None
    ):
        return stage.result.output_layout
    return ComparisonEndpoint(
        stage.columns,
        stage.keys,
        graph.signature.axes,
        plan.output_groups or key_groups(graph, stage.columns, stage.keys),
    )


def expression_value(
    expression: ValueExpression,
    values: tuple[ir.Value, ...],
    keys: tuple[ir.Value, ...],
) -> ir.Value:
    """Resolve only the finite result grammar over typed scalar placeholders."""
    if isinstance(expression, ValueSlot):
        return values[expression.index]
    if isinstance(expression, CoordinateSlot):
        return keys[expression.index]
    if isinstance(expression, PresenceSlot):
        return ibis.null().cast("boolean")
    if isinstance(expression, ValueLiteral):
        if isinstance(expression.value, timedelta) and expression.value == timedelta(0):
            return ibis.interval(0, unit="us")
        if type(expression.value) is int and not -(2**63) <= expression.value < 2**63:
            from decimal import Decimal

            digits = len(str(abs(expression.value)))
            if digits > 38:
                reject(
                    "a losslessly representable integer or Decimal literal",
                    repr(expression.value),
                    "Use a value within decimal(38,0) or int64.",
                    "Query.prepare",
                )
            return ibis.literal(Decimal(expression.value), type=f"decimal({digits},0)")
        return ibis.literal(expression.value)
    if isinstance(expression, ValueUnary):
        value = expression_value(expression.operand, values, keys)
        if expression.operation == "is_null":
            return value.isnull()
        if expression.operation == "not":
            return ~value
        if expression.operation == "abs":
            return value.abs()
        return -value
    left = expression_value(expression.left, values, keys)
    right = expression_value(expression.right, values, keys)
    if expression.operation in ("add", "sub", "mul", "div"):
        if not all(
            value.type().is_numeric() or value.type().is_interval() for value in (left, right)
        ):
            reject(
                "numeric or Duration arithmetic",
                repr((left.type(), right.type())),
                "Use arithmetic on compatible numeric Columns.",
                "Query.prepare",
            )
        from marivo.semantic._numeric_lowering import NumericValue, numeric_binary

        return numeric_binary(
            {"add": "add", "sub": "subtract", "mul": "multiply", "div": "divide"}[
                expression.operation
            ],
            NumericValue(left),
            NumericValue(right),
            zero_denominator=expression.zero_denominator,
        ).value
    if expression.operation in ("eq", "ne", "lt", "le", "gt", "ge"):
        left_type, right_type = left.type(), right.type()
        temporal = (
            left_type.is_date()
            or left_type.is_timestamp()
            or right_type.is_date()
            or right_type.is_timestamp()
        )
        if temporal and not (
            (left_type.is_date() and right_type.is_date())
            or (left_type.is_timestamp() and right_type.is_timestamp())
        ):
            reject(
                "matching temporal value kinds",
                f"{left_type}, {right_type}",
                "Compare dates with dates and timezone-aware timestamps with timestamps.",
                "Query.prepare",
            )
    if expression.operation == "add":
        return left + right
    if expression.operation == "sub":
        return left - right
    if expression.operation == "mul":
        return left * right
    if expression.operation == "div":
        return left / right
    if expression.operation == "eq":
        return left == right
    if expression.operation == "ne":
        return left != right
    if expression.operation == "lt":
        return left < right
    if expression.operation == "le":
        return left <= right
    if expression.operation == "gt":
        return left > right
    if expression.operation == "ge":
        return left >= right
    if expression.operation == "and":
        return left & right
    return left | right


def key_groups(
    graph: BoundGraph, columns: tuple[DataColumn, ...], keys: tuple[str, ...]
) -> tuple[tuple[str, ...], ...]:
    """Keep composite identities within their original coordinate boundary."""
    axes = graph.signature.axes
    if not axes:
        return ()
    observation = graph.signature.observation
    if observation is not None and observation.contributions:
        from marivo._definition.types import CalendarCoordinate, coordinate_read_keys

        contribution = observation.contributions[0]
        widths = iter(len(coordinate_read_keys(read)) for read in contribution.coordinates)
        result = []
        offset = 0
        for axis in axes:
            width = 1 if isinstance(axis, CalendarCoordinate) else next(widths)
            result.append(keys[offset : offset + width])
            offset += width
        return tuple(result)
    if len(axes) != len(keys):
        reject(
            "complete coordinate group metadata",
            repr(keys),
            "Preserve composite identity groups when building the input.",
            "query.keys",
        )
    return tuple((key,) for key in keys)


def plan_read(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    *,
    memo: InvocationMemo | None = None,
) -> QueryPlan:
    """Project requested attributes after the complete explicit identity-domain read."""
    root = graph.root
    assert isinstance(root, ReadQuery)
    source_graph = read_input_graph(graph)
    plan = plan_query(source_graph, schemas, memo=memo)
    source = next(stage for stage in plan.stages if stage.stage_id == plan.publish)
    groups = plan.output_groups
    columns = {column.slot_id: column for column in source.columns}
    keys = tuple(
        replace(columns[slot], slot_id=f"key.{index}") for index, slot in enumerate(groups[0])
    )
    key_names = tuple(column.slot_id for column in keys)
    values = tuple(
        replace(
            columns[groups[position][0]],
            slot_id=f"value.{index}",
            name=quantity.name,
            unit=quantity.unit,
            nullable=True,
        )
        for index, (position, quantity) in enumerate(
            zip(root.positions, graph.signature.quantities, strict=True)
        )
    )
    stage = LocalCompute(
        "local:read:" + graph.fingerprint,
        (source.stage_id,),
        "query.result@v1",
        (*keys, *values),
        key_names,
        result=ResultProgram(
            ReadFields(root.positions),
            source.columns,
            source.keys,
            source_graph.signature.axes,
            groups,
            (key_names,),
        ),
    )
    stages = (*plan.stages, stage)
    return QueryPlan(
        stages,
        plan.checks,
        tuple(
            (
                item.stage_id,
                tuple(consumer.stage_id for consumer in stages if item.stage_id in consumer.inputs),
            )
            for item in stages
        ),
        stage.stage_id,
        (key_names,),
    )


def plan_literal(
    graph: BoundGraph, operation: LiteralValues, schemas: tuple[SourceSchema, ...] = ()
) -> QueryPlan:
    """Infer finite literal carriers once and execute through a pandas input stage."""
    import math
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    import ibis.expr.datatypes as dt

    from marivo._definition.types import AxisBinding, LiteralValue

    normalized = []
    for index in range(len(operation.columns)):
        values = tuple(row[index] for _, row in operation.rows)
        floating = any(isinstance(value, float) for value in values)
        decimal = any(
            isinstance(value, Decimal) or (type(value) is int and not -(2**63) <= value < 2**63)
            for value in values
        )
        converted = tuple(
            float(value)
            if floating and isinstance(value, (int, float, Decimal))
            else Decimal(value)
            if decimal and isinstance(value, (int, Decimal))
            else value.astimezone(timezone.utc)
            if isinstance(value, datetime)
            else value
            for value in values
        )
        if any(isinstance(value, float) and not math.isfinite(value) for value in converted):
            reject(
                "finite promoted float64 values",
                repr(values),
                "Use values within the admitted numeric range.",
                "Session.values",
            )
        normalized.append(converted)
    operation = replace(
        operation,
        rows=tuple(
            (keys, tuple(normalized[index][position] for index in range(len(operation.columns))))
            for position, (keys, _) in enumerate(operation.rows)
        ),
    )

    def inferred(values: tuple[LiteralValue, ...]) -> DataType:
        try:
            present = tuple(value for value in values if value is not None)
            if present and all(isinstance(value, timedelta) for value in present):
                nonzero = tuple(value for value in present if value != timedelta(0))
                if not nonzero:
                    return DurationDataType("us")
                return data_type(dt.highest_precedence(dt.infer(value) for value in nonzero))
            decimals = tuple(value for value in values if isinstance(value, Decimal))
            if decimals:
                scales: list[int] = []
                for value in decimals:
                    exponent = value.as_tuple().exponent
                    assert isinstance(exponent, int)
                    scales.append(max(0, -exponent))
                scale = max(scales)
                precision = max(1, max(max(0, value.adjusted() + 1) for value in decimals) + scale)
                if precision > 38:
                    raise ValueError(f"Decimal literal precision {precision} exceeds 38")
                return DecimalDataType(precision, scale)
            return data_type(
                dt.highest_precedence(dt.infer(value) for value in values if value is not None)
            )
        except (TypeError, ValueError) as error:
            reject(
                "losslessly representable finite literal values",
                str(error),
                "Use compatible values within the admitted numeric precision.",
                "Session.values",
            )

    def check_key(value: LiteralValue, column: SourceField) -> None:
        from datetime import date, datetime, timedelta
        from decimal import Decimal, localcontext

        dtype = column.dtype
        valid = (
            column.nullable
            if value is None
            else (
                type(value) is int and -(2**63) <= value < 2**63
                if dtype == ScalarDataType("int64")
                else type(value) is float
                if dtype == ScalarDataType("float64")
                else type(value) is bool
                if dtype == ScalarDataType("boolean")
                else type(value) is str
                if dtype == ScalarDataType("string")
                else isinstance(value, date) and not isinstance(value, datetime)
                if dtype == ScalarDataType("date")
                else isinstance(value, datetime)
                and value.tzinfo is not None
                and value.utcoffset() is not None
                if isinstance(dtype, TimestampDataType)
                else isinstance(value, timedelta)
                if isinstance(dtype, DurationDataType)
                else isinstance(value, Decimal)
            )
        )
        if (
            valid
            and value is not None
            and isinstance(dtype, DecimalDataType)
            and isinstance(value, Decimal)
        ):
            with localcontext() as context:
                context.prec = 120
                valid = value == value.quantize(Decimal(1).scaleb(-dtype.scale)) and abs(
                    value
                ) < Decimal(10) ** (dtype.precision - dtype.scale)
        if not valid:
            reject(
                f"{column.field}: a lossless {dtype!r} coordinate",
                repr(value),
                "Use the declared Field kind or every complete identity key component.",
                "Session.values",
            )

    schema = next(
        (item for item in schemas if item.source_id == "literal:" + graph.fingerprint), None
    )
    groups: list[tuple[str, ...]] = []
    columns: list[DataColumn] = []
    for index, axis in enumerate(graph.signature.axes):
        assert isinstance(axis, AxisBinding)
        key = operation.rows[0][0][index]
        components = key if isinstance(key, tuple) else (key,)
        if schema is None or len(components) != len(schema.key_groups[index]):
            reject(
                "the complete declared coordinate key schema",
                repr(key),
                "Prepare the actual source schema and provide every identity component.",
                "Session.values",
            )
        group = []
        for component in range(len(components)):
            slot = f"key.{len(columns)}"
            samples_list: list[LiteralValue] = []
            for row, _ in operation.rows:
                value = row[index]
                samples_list.append(value[component] if isinstance(value, tuple) else value)
            samples = tuple(samples_list)
            for value in samples:
                check_key(value, schema.keys[len(columns)])
            columns.append(
                DataColumn(
                    slot,
                    axis.field.ref.path if axis.field is not None else axis.entity.ref.path,
                    schema.keys[len(columns)].dtype,
                    axis.field is not None,
                )
            )
            group.append(slot)
        groups.append(tuple(group))
    keys = tuple(column.slot_id for column in columns)
    columns.extend(
        DataColumn(
            f"value.{index}",
            name,
            inferred(tuple(values[index] for _, values in operation.rows)),
            False,
            quantity.unit,
        )
        for index, (name, quantity) in enumerate(
            zip(operation.columns, graph.signature.quantities, strict=True)
        )
    )
    stage = LocalCompute(
        "literal:" + graph.fingerprint,
        (),
        "query.result@v1",
        tuple(columns),
        keys,
        result=ResultProgram(operation, (), (), (), (), tuple(groups)),
    )
    return QueryPlan((stage,), (), (), stage.stage_id, tuple(groups))
