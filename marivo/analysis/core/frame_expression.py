"""Closed data expressions and pure current-structure inference."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TypeAlias, overload

import ibis

from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    PresenceSlot,
    ReduceValues,
    SelectValues,
    Statistic,
    TopValues,
    ValueBinary,
    ValueBroadcast,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.core.data_schema import DataColumn, DataSchema
from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.frame_structure import (
    PendingDataColumn,
    PendingDataSchema,
    ValueKind,
    column_kind,
    value_name,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import reject

FrameOperationKind: TypeAlias = SelectValues | FilterValues | TopValues | ReduceValues


@dataclass(frozen=True, slots=True)
class SourceFrameInput:
    input_id: str
    graph: BoundGraph


@dataclass(frozen=True, slots=True)
class FixedFrameInput:
    input_id: str
    schema: DataSchema


@dataclass(frozen=True, slots=True)
class FrameOperation:
    inputs: tuple[FrameExpression, ...]
    operation: FrameOperationKind


FrameExpression: TypeAlias = SourceFrameInput | FixedFrameInput | FrameOperation


def admitted(expression: ValueExpression) -> None:
    if isinstance(expression, ValueBroadcast):
        reject(
            "a data-only Column expression",
            "source broadcast",
            "Align explicit Frame inputs.",
            "frame.expression",
        )
    if isinstance(expression, ValueUnary):
        admitted(expression.operand)
    elif isinstance(expression, ValueBinary):
        admitted(expression.left)
        admitted(expression.right)


def expressions(operation: FrameOperationKind) -> tuple[ValueExpression, ...]:
    if isinstance(operation, SelectValues):
        result = tuple(value for _, value in operation.values)
    elif isinstance(operation, FilterValues):
        if operation.domains or not isinstance(
            operation.predicate,
            (ValueSlot, CoordinateSlot, PresenceSlot, ValueLiteral, ValueUnary, ValueBinary),
        ):
            reject(
                "a same-input boolean Column",
                "source Population predicate",
                "Use Frame Columns.",
                "frame.where",
            )
        result = (operation.predicate,)
    elif isinstance(operation, TopValues):
        if (
            operation.partitions
            or type(operation.n) is not int
            or operation.n < 0
            or type(operation.descending) is not bool
        ):
            reject(
                "a nonnegative global top with boolean descending",
                repr(operation),
                "Use frame.top(n, by=column).",
                "frame.top",
            )
        result = (operation.score,)
    else:
        if operation.calendar_maps:
            reject(
                "reduction by current complete axes",
                "calendar coarsening",
                "Construct a new source observation.",
                "frame.reduce",
            )
        result = tuple(stat.value for _, stat in operation.values if stat.value is not None)
    for expression in result:
        admitted(expression)
    return result


def expression_column(
    schema: DataSchema | PendingDataSchema, value: ValueExpression
) -> DataColumn | PendingDataColumn:
    if isinstance(value, ValueSlot):
        if not 0 <= value.index < len(schema.values):
            reject(
                "a current value slot",
                repr(value),
                "Acquire the Column from this Frame.",
                "frame.expression",
            )
        return schema.values[value.index]
    if isinstance(value, CoordinateSlot):
        if not 0 <= value.index < len(schema.axes) or len(schema.axes[value.index].columns) != 1:
            reject(
                "one complete scalar coordinate",
                repr(value),
                "Use the complete axis for grouping.",
                "frame.coord",
            )
        return schema.axes[value.index].columns[0]
    if isinstance(value, PresenceSlot):
        if value.path not in schema.presence_paths:
            reject(
                "a current presence path",
                repr(value.path),
                "Acquire current presence from the Frame.",
                "frame.expression",
            )
        return schema.presence[schema.presence_paths.index(value.path)]
    reject("a direct current column slot", repr(value), "Use a current Column.", "frame.expression")


def expression_unit(schema: DataSchema | PendingDataSchema, value: ValueExpression) -> str | None:
    if isinstance(value, (ValueSlot, CoordinateSlot, PresenceSlot)):
        return expression_column(schema, value).unit
    if isinstance(value, ValueLiteral):
        return None
    if isinstance(value, ValueUnary):
        return (
            None
            if value.operation in ("not", "is_null")
            else expression_unit(schema, value.operand)
        )
    if isinstance(value, ValueBroadcast):
        admitted(value)
    assert isinstance(value, ValueBinary)
    a, b = expression_unit(schema, value.left), expression_unit(schema, value.right)
    if value.operation in ("add", "sub"):
        if (
            not isinstance(value.left, ValueLiteral)
            and not isinstance(value.right, ValueLiteral)
            and a != b
        ):
            reject("matching units", repr((a, b)), "Use compatible Columns.", "frame.expression")
        return b if isinstance(value.left, ValueLiteral) else a
    if value.operation == "mul":
        if isinstance(value.left, ValueLiteral):
            return b
        if isinstance(value.right, ValueLiteral):
            return a
        return None if a is None or b is None else b if a == "1" else a if b == "1" else f"{a}*{b}"
    if value.operation == "div":
        if isinstance(value.right, ValueLiteral):
            return a
        if isinstance(value.left, ValueLiteral):
            return None if b is None else "1" if b == "1" else f"1/{b}"
        return None if a is None or b is None else "1" if a == b else a if b == "1" else f"{a}/{b}"
    return None


def expression_type(schema: DataSchema, value: ValueExpression) -> DataType:
    from marivo.analysis.compiler.observation import data_type, ibis_type
    from marivo.analysis.compiler.queries import expression_value

    admitted(value)
    if isinstance(value, (ValueSlot, CoordinateSlot, PresenceSlot)):
        column = expression_column(schema, value)
        assert isinstance(column, DataColumn)
        return column.dtype
    if isinstance(value, ValueUnary) and value.operation in ("neg", "abs"):
        return expression_type(schema, value.operand)
    if isinstance(value, ValueBinary):
        left, right = expression_type(schema, value.left), expression_type(schema, value.right)
        if isinstance(left, DurationDataType) or isinstance(right, DurationDataType):
            if (
                isinstance(value.left, ValueLiteral)
                and value.left.value == timedelta(0)
                and isinstance(right, DurationDataType)
            ):
                left = right
            if (
                isinstance(value.right, ValueLiteral)
                and value.right.value == timedelta(0)
                and isinstance(left, DurationDataType)
            ):
                right = left

            def numeric(dtype: DataType) -> bool:
                return isinstance(dtype, DecimalDataType) or dtype in (
                    ScalarDataType("int64"),
                    ScalarDataType("float64"),
                )

            if (
                isinstance(left, DurationDataType)
                and isinstance(right, DurationDataType)
                and left == right
            ):
                if value.operation in ("add", "sub"):
                    return left
                if value.operation == "div":
                    return ScalarDataType("float64")
                if value.operation in ("eq", "ne", "lt", "le", "gt", "ge"):
                    return ScalarDataType("boolean")
            elif (
                isinstance(left, DurationDataType)
                and numeric(right)
                and value.operation in ("mul", "div")
            ):
                return left
            elif isinstance(right, DurationDataType) and numeric(left) and value.operation == "mul":
                return right
            reject(
                "compatible exact Duration units and arithmetic",
                repr((left, right, value.operation)),
                "Use matching Duration units or a numeric scale.",
                "frame.expression",
            )
    values = tuple(ibis.literal(None, type=ibis_type(c.dtype)) for c in schema.values)
    coordinates = tuple(ibis.literal(None, type=ibis_type(a.columns[0].dtype)) for a in schema.axes)
    try:
        return data_type(expression_value(value, values, coordinates).type())
    except (TypeError, ValueError) as error:
        reject(
            "compatible concrete Column types",
            str(error),
            "Use compatible arithmetic and predicates.",
            "frame.expression",
        )


def _pending_kind(schema: PendingDataSchema, expression: ValueExpression) -> ValueKind:
    if isinstance(expression, (ValueSlot, CoordinateSlot, PresenceSlot)):
        return column_kind(expression_column(schema, expression))
    if isinstance(expression, ValueLiteral):
        from marivo.analysis.dsl.column import literal_kind

        kind = literal_kind(expression.value)
        if kind in ("numeric", "boolean", "string", "date", "timestamp", "duration"):
            return kind
        return "unresolved"
    if isinstance(expression, ValueUnary):
        return (
            "boolean"
            if expression.operation in ("not", "is_null")
            else _pending_kind(schema, expression.operand)
        )
    assert isinstance(expression, ValueBinary)
    if expression.operation not in ("add", "sub", "mul", "div"):
        return "boolean"
    a, b = _pending_kind(schema, expression.left), _pending_kind(schema, expression.right)
    return (
        "duration"
        if "duration" in (a, b) and not (a == b == "duration" and expression.operation == "div")
        else "unresolved"
        if "unresolved" in (a, b)
        else "numeric"
    )


@overload
def infer_frame_schema(schema: DataSchema, operation: FrameOperationKind) -> DataSchema: ...
@overload
def infer_frame_schema(
    schema: PendingDataSchema, operation: FrameOperationKind
) -> PendingDataSchema: ...


def infer_frame_schema(
    schema: DataSchema | PendingDataSchema, operation: FrameOperationKind
) -> DataSchema | PendingDataSchema:
    values = expressions(operation)
    for operand in values:
        expression_unit(schema, operand)
    if isinstance(operation, (FilterValues, TopValues)):
        if isinstance(schema, DataSchema):
            dtype = expression_type(schema, values[0])
            if isinstance(operation, FilterValues) and dtype != ScalarDataType("boolean"):
                reject(
                    "a boolean Column predicate",
                    repr(dtype),
                    "Compare compatible Columns.",
                    "frame.where",
                )
        return schema
    paths = tuple(value_name(name, location="frame.values") for name, _ in operation.values)
    if isinstance(operation, ReduceValues) and (
        len(set(operation.positions)) != len(operation.positions)
        or any(type(i) is not int or not 0 <= i < len(schema.axes) for i in operation.positions)
    ):
        reject(
            "distinct complete current axes",
            repr(operation.positions),
            "Acquire by axes from this Frame.",
            "frame.reduce",
        )
    if isinstance(schema, PendingDataSchema):
        pending: list[PendingDataColumn] = []
        for index, (name, expression) in enumerate(operation.values):
            value = expression.value if isinstance(expression, Statistic) else expression
            assert value is None or isinstance(
                value,
                (
                    ValueSlot,
                    CoordinateSlot,
                    PresenceSlot,
                    ValueLiteral,
                    ValueUnary,
                    ValueBinary,
                    ValueBroadcast,
                ),
            )
            kind = "numeric" if value is None else _pending_kind(schema, value)
            pending.append(
                PendingDataColumn(
                    f"value.{index}",
                    name,
                    kind,
                    value is not None,
                    None if value is None else expression_unit(schema, value),
                )
            )
        return PendingDataSchema(
            tuple(
                schema.axes
                if isinstance(operation, SelectValues)
                else (schema.axes[i] for i in operation.positions)
            ),
            tuple(pending),
            paths,
            schema.presence if isinstance(operation, SelectValues) else (),
            schema.presence_paths if isinstance(operation, SelectValues) else (),
        )
    concrete: list[DataColumn] = []
    for index, (name, item) in enumerate(operation.values):
        if isinstance(operation, SelectValues):
            assert isinstance(
                item,
                (
                    ValueSlot,
                    CoordinateSlot,
                    PresenceSlot,
                    ValueLiteral,
                    ValueUnary,
                    ValueBinary,
                    ValueBroadcast,
                ),
            )
            dtype, unit, nullable = (
                expression_type(schema, item),
                expression_unit(schema, item),
                True,
            )
        else:
            assert not isinstance(
                item,
                (
                    ValueSlot,
                    CoordinateSlot,
                    PresenceSlot,
                    ValueLiteral,
                    ValueUnary,
                    ValueBinary,
                    ValueBroadcast,
                ),
            )
            dtype = (
                ScalarDataType("int64")
                if item.value is None
                else expression_type(schema, item.value)
            )
            if item.method == "mean" and dtype == ScalarDataType("int64"):
                dtype = ScalarDataType("float64")
            unit, nullable = (
                None if item.value is None else expression_unit(schema, item.value),
                item.method != "row_count",
            )
        concrete.append(DataColumn(f"value.{index}", name, dtype, nullable, unit))
    return DataSchema(
        tuple(
            schema.axes
            if isinstance(operation, SelectValues)
            else (schema.axes[i] for i in operation.positions)
        ),
        tuple(concrete),
        schema.presence if isinstance(operation, SelectValues) else (),
        schema.projection_columns if isinstance(operation, SelectValues) else (),
        paths,
        schema.presence_paths if isinstance(operation, SelectValues) else (),
    )
