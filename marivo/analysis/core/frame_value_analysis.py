"""Infer unary Frame types from exact current values and complete groups."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import overload

import ibis

from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    PairStatistic,
    PresenceSlot,
    ReduceValues,
    SelectValues,
    Statistic,
    TopValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValuePartition,
    ValueShare,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.core.frame_fix import validate_fixed_key
from marivo.analysis.core.frame_interpretation import (
    unary_facts,
)
from marivo.analysis.core.frame_operations import (
    FixFrameValues,
    FrameUnaryOperationKind,
)
from marivo.analysis.core.value_normalization import physical_expression
from marivo.analysis.core.value_types import data_type, expression_value, ibis_type, literal_kind
from marivo.analysis.model.data_schema import (
    DataColumn,
    DataComparison,
    DataSchema,
)
from marivo.analysis.model.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_structure import (
    PendingDataColumn,
    PendingDataSchema,
    ValueKind,
    column_kind,
)
from marivo.analysis.model.value_names import value_name


def admitted(expression: ValueExpression) -> None:
    if isinstance(expression, ValueUnary):
        admitted(expression.operand)
    elif isinstance(expression, ValueBinary):
        admitted(expression.left)
        admitted(expression.right)
    elif isinstance(expression, ValueShare):
        admitted(expression.value)
    elif isinstance(expression, ValuePartition):
        for value in statistic_expressions(expression.statistic):
            admitted(value)


def statistic_expressions(statistic: Statistic | PairStatistic) -> tuple[ValueExpression, ...]:
    if isinstance(statistic, PairStatistic):
        return statistic.left, statistic.right
    return () if statistic.value is None else (statistic.value,)


def expressions(operation: FrameUnaryOperationKind) -> tuple[ValueExpression, ...]:
    if isinstance(operation, FixFrameValues):
        return ()
    if isinstance(operation, SelectValues):
        result = tuple(value for _, value in operation.values)
    elif isinstance(operation, FilterValues):
        result = (operation.predicate,)
    elif isinstance(operation, TopValues):
        if (
            type(operation.n) is not int
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
        result = tuple(
            value
            for _, stat in operation.values
            for value in (
                (stat.left, stat.right)
                if isinstance(stat, PairStatistic)
                else ()
                if stat.value is None
                else (stat.value,)
            )
        )
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
    if isinstance(value, ValueShare):
        return expression_unit(schema, physical_expression(value, len(schema.axes)))
    if isinstance(value, (ValueSlot, CoordinateSlot, PresenceSlot)):
        return expression_column(schema, value).unit
    if isinstance(value, ValuePartition):
        return (
            infer_frame_schema(
                schema, ReduceValues((("partition", value.statistic),), value.positions)
            )
            .values[0]
            .unit
        )
    if isinstance(value, ValueLiteral):
        return None
    if isinstance(value, ValueUnary):
        return (
            None
            if value.operation in ("not", "is_null")
            else expression_unit(schema, value.operand)
        )
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

    admitted(value)
    if isinstance(value, ValueShare):
        return expression_type(schema, physical_expression(value, len(schema.axes)))
    if isinstance(value, (ValueSlot, CoordinateSlot, PresenceSlot)):
        column = expression_column(schema, value)
        assert isinstance(column, DataColumn)
        return column.dtype
    if isinstance(value, ValuePartition):
        return (
            infer_frame_schema(
                schema, ReduceValues((("partition", value.statistic),), value.positions)
            )
            .values[0]
            .dtype
        )
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
    if isinstance(value, ValueBinary) and value.operation in ("add", "sub", "mul"):
        left, right = expression_type(schema, value.left), expression_type(schema, value.right)
        if isinstance(left, DecimalDataType) or isinstance(right, DecimalDataType):
            if ScalarDataType("float64") in (left, right):
                return ScalarDataType("float64")
            return _decimal_column_type(left, right, multiply=value.operation == "mul")
    values = tuple(ibis.param(ibis_type(c.dtype)) for c in schema.values)
    coordinates = tuple(ibis.param(ibis_type(a.columns[0].dtype)) for a in schema.axes)
    try:
        return data_type(
            expression_value(
                value,
                values,
                coordinates,
                partition_type=lambda node: expression_type(schema, node),
                for_type_inference=True,
            ).type()
        )
    except (TypeError, ValueError) as error:
        reject(
            "compatible concrete Column types",
            str(error),
            "Use compatible arithmetic and predicates.",
            "frame.expression",
        )


def _pending_kind(schema: PendingDataSchema, expression: ValueExpression) -> ValueKind:
    if isinstance(expression, ValueShare):
        return _pending_kind(schema, physical_expression(expression, len(schema.axes)))
    if isinstance(expression, (ValueSlot, CoordinateSlot, PresenceSlot)):
        return column_kind(expression_column(schema, expression))
    if isinstance(expression, ValuePartition):
        return (
            infer_frame_schema(
                schema, ReduceValues((("partition", expression.statistic),), expression.positions)
            )
            .values[0]
            .kind
        )
    if isinstance(expression, ValueLiteral):
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


def _validate_pair_statistic(
    schema: DataSchema | PendingDataSchema, statistic: PairStatistic
) -> None:
    for side, expression in (("left", statistic.left), ("right", statistic.right)):
        received: str
        if isinstance(schema, PendingDataSchema):
            kind = _pending_kind(schema, expression)
            if kind in ("numeric", "unresolved"):
                continue
            received = kind
        else:
            dtype = expression_type(schema, expression)
            if isinstance(dtype, DecimalDataType) or dtype in (
                ScalarDataType("int64"),
                ScalarDataType("float64"),
            ):
                continue
            received = repr(dtype)
        reject(
            "a numeric Column (int64, float64 or Decimal)",
            f"{side} endpoint: {received}",
            "Construct a source expression with an explicit numeric unit, or use external computation.",
            statistic.location,
        )


@overload
def _infer_frame_schema(schema: DataSchema, operation: FrameUnaryOperationKind) -> DataSchema: ...
@overload
def _infer_frame_schema(
    schema: PendingDataSchema, operation: FrameUnaryOperationKind
) -> PendingDataSchema: ...


def _infer_frame_schema(
    schema: DataSchema | PendingDataSchema, operation: FrameUnaryOperationKind
) -> DataSchema | PendingDataSchema:
    if isinstance(operation, FixFrameValues):
        validate_fixed_key(schema.axes[operation.position], operation.key)
        if isinstance(schema, PendingDataSchema):
            return replace(
                schema, axes=tuple(a for i, a in enumerate(schema.axes) if i != operation.position)
            )
        return replace(
            schema, axes=tuple(a for i, a in enumerate(schema.axes) if i != operation.position)
        )
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
    comparisons = (
        tuple(
            replace(
                fact,
                values=tuple(
                    replace(binding, value_path=path)
                    for (path, (_, expression)) in zip(paths, operation.values, strict=True)
                    if isinstance(expression, ValueSlot)
                    for binding in fact.values
                    if binding.value_path == schema.value_paths[expression.index]
                ),
            )
            for fact in schema.comparisons
        )
        if isinstance(operation, SelectValues)
        else ()
    )
    if isinstance(schema, PendingDataSchema):
        pending: list[PendingDataColumn] = []
        for index, (name, expression) in enumerate(operation.values):
            kind: ValueKind
            if isinstance(expression, PairStatistic):
                _validate_pair_statistic(schema, expression)
                kind = "numeric"
                nullable = expression.method != "pair_count"
                unit = None if expression.method == "pair_count" else "1"
            else:
                value = expression.value if isinstance(expression, Statistic) else expression
                kind = "numeric" if value is None else _pending_kind(schema, value)
                nullable = value is not None
                unit = None if value is None else expression_unit(schema, value)
            pending.append(
                PendingDataColumn(
                    f"value.{index}",
                    name,
                    kind,
                    nullable,
                    unit,
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
            comparisons,
        )
    return _complete_unary_schema(schema, operation, paths, comparisons)


def _complete_unary_schema(
    schema: DataSchema,
    operation: SelectValues | ReduceValues,
    paths: tuple[tuple[str, ...], ...],
    comparisons: tuple[DataComparison, ...],
) -> DataSchema:
    concrete: list[DataColumn] = []
    for index, (name, item) in enumerate(operation.values):
        if isinstance(item, PairStatistic):
            _validate_pair_statistic(schema, item)
            dtype: DataType = ScalarDataType("int64" if item.method == "pair_count" else "float64")
            unit = None if item.method == "pair_count" else "1"
            nullable = item.method != "pair_count"
        elif not isinstance(item, Statistic):
            dtype, unit, nullable = (
                expression_type(schema, item),
                expression_unit(schema, item),
                True,
            )
        else:
            dtype = (
                ScalarDataType("int64")
                if item.value is None
                else expression_type(schema, item.value)
            )
            if item.method == "sum" and isinstance(dtype, DecimalDataType):
                dtype = DecimalDataType(38, dtype.scale)
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
        comparisons,
    )


@overload
def infer_frame_schema(schema: DataSchema, operation: FrameUnaryOperationKind) -> DataSchema: ...
@overload
def infer_frame_schema(
    schema: PendingDataSchema, operation: FrameUnaryOperationKind
) -> PendingDataSchema: ...


def infer_frame_schema(
    schema: DataSchema | PendingDataSchema, operation: FrameUnaryOperationKind
) -> DataSchema | PendingDataSchema:

    return unary_facts(schema, _infer_frame_schema(schema, operation), operation)


def _decimal_column_type(
    left: DataType, right: DataType, *, multiply: bool = False
) -> DecimalDataType:
    """Describe the supported carrier without rounding native scalar arithmetic."""
    p1, s1 = (left.precision, left.scale) if isinstance(left, DecimalDataType) else (19, 0)
    p2, s2 = (right.precision, right.scale) if isinstance(right, DecimalDataType) else (19, 0)
    scale = s1 + s2 if multiply else max(s1, s2)
    precision = p1 + p2 if multiply else max(p1 - s1, p2 - s2) + scale + 1
    return DecimalDataType(min(38, precision), scale)


@overload
def select_attribute_source_schema(schema: DataSchema, index: int) -> DataSchema: ...
@overload
def select_attribute_source_schema(
    schema: DataSchema | PendingDataSchema, index: int
) -> DataSchema | PendingDataSchema: ...
def select_attribute_source_schema(
    schema: DataSchema | PendingDataSchema, index: int
) -> DataSchema | PendingDataSchema:
    """View one selected source output without changing its exact source slot."""
    if isinstance(schema, PendingDataSchema):
        return replace(
            schema,
            values=(replace(schema.values[index], name="target"),),
            value_paths=(("target",),),
            comparisons=(),
        )
    return replace(
        schema,
        values=(replace(schema.values[index], name="target"),),
        value_paths=(("target",),),
        comparisons=(),
    )
