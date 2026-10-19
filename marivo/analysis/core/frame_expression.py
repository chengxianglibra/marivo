"""Closed data expressions and pure current-structure inference."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Literal, TypeAlias, overload

import ibis

from marivo._definition.correspondence import CalendarCorrespondence
from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    PresenceSlot,
    ReduceValues,
    SelectValues,
    Statistic,
    TopValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.core.data_schema import (
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.frame_structure import (
    AxisRole,
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    PendingFrameAxis,
    ValueKind,
    axis_role,
    column_kind,
    value_name,
)
from marivo.analysis.core.frozen_calendar import (
    require_calendar_match_rule,
    require_compatible_calendar_axes,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import reject

FrameUnaryOperationKind: TypeAlias = SelectValues | FilterValues | TopValues | ReduceValues


@dataclass(frozen=True, slots=True)
class AlignFrameValues:
    pairs: tuple[tuple[int, int], ...]
    how: Literal["exact", "outer"] = "exact"
    match: CalendarCorrespondence | None = None


@dataclass(frozen=True, slots=True)
class CompareFrameValues:
    pairs: tuple[tuple[int, int], ...]
    how: Literal["exact", "outer"] = "exact"
    zero_denominator: Literal["null", "error"] = "null"
    match: CalendarCorrespondence | None = None


FrameBinaryOperationKind: TypeAlias = AlignFrameValues | CompareFrameValues
FrameOperationKind: TypeAlias = FrameUnaryOperationKind | FrameBinaryOperationKind


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
    if isinstance(expression, ValueUnary):
        admitted(expression.operand)
    elif isinstance(expression, ValueBinary):
        admitted(expression.left)
        admitted(expression.right)


def expressions(operation: FrameUnaryOperationKind) -> tuple[ValueExpression, ...]:
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
    values = tuple(ibis.param(ibis_type(c.dtype)) for c in schema.values)
    coordinates = tuple(ibis.param(ibis_type(a.columns[0].dtype)) for a in schema.axes)
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
def infer_frame_schema(schema: DataSchema, operation: FrameUnaryOperationKind) -> DataSchema: ...
@overload
def infer_frame_schema(
    schema: PendingDataSchema, operation: FrameUnaryOperationKind
) -> PendingDataSchema: ...


def infer_frame_schema(
    schema: DataSchema | PendingDataSchema, operation: FrameUnaryOperationKind
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


def _compare_type(left: DataType, right: DataType) -> DataType:
    if isinstance(left, DurationDataType) or isinstance(right, DurationDataType):
        if left == right:
            return left
    elif all(
        isinstance(dtype, DecimalDataType)
        or dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
        for dtype in (left, right)
    ):
        if ScalarDataType("float64") in (left, right):
            return ScalarDataType("float64")
        decimals = tuple(dtype for dtype in (left, right) if isinstance(dtype, DecimalDataType))
        if not decimals:
            return ScalarDataType("int64")
        scale = max(dtype.scale for dtype in decimals)
        integer = max(
            dtype.precision - dtype.scale if isinstance(dtype, DecimalDataType) else 19
            for dtype in (left, right)
        )
        return DecimalDataType(min(38, integer + scale), scale)
    reject(
        "compatible numeric types or matching exact Duration units",
        repr((left, right)),
        "Select compatible numeric values before compare.",
        "frame.compare",
    )


def _pending_column(column: DataColumn | PendingDataColumn) -> PendingDataColumn:
    return PendingDataColumn(
        column.slot_id, column.name, column_kind(column), column.nullable, column.unit
    )


def _paired_layout(
    left: DataSchema | PendingDataSchema, right: DataSchema | PendingDataSchema, index: int
) -> tuple[str, DataColumn]:
    tokens = {axis.token for schema in (left, right) for axis in schema.axes}
    slots = {column.slot_id for schema in (left, right) for column in schema.columns}
    suffix = 0
    while True:
        token = f"frame.paired.{index}" + (f".{suffix}" if suffix else "")
        slot = f"{token}.key"
        if not {token, f"{token}.left", f"{token}.right"} & tokens and slot not in slots:
            return token, DataColumn(slot, "pair", ScalarDataType("int64"), False)
        suffix += 1


def _paired_axis(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    index: int,
    partner: int,
) -> PairedDataAxis:
    a, b = left.axes[index], right.axes[partner]
    assert isinstance(a, CalendarDataAxis) and isinstance(b, CalendarDataAxis)
    assert a.frozen is not None and b.frozen is not None
    token, key = _paired_layout(left, right, index)
    used = {
        axis.frozen.directory_name
        for schema in (left, right)
        for axis in schema.axes
        if isinstance(axis, CalendarDataAxis) and axis.frozen is not None
    }

    def name(base: str) -> str:
        candidate, n = base, 0
        while candidate in used:
            n += 1
            candidate = f"{base}.{n}"
        used.add(candidate)
        return candidate

    return PairedDataAxis(
        token,
        (key,),
        replace(
            a,
            token=f"{token}.left",
            frozen=replace(a.frozen, directory_name=name(f"calendar.{token}.left")),
        ),
        replace(
            b,
            token=f"{token}.right",
            frozen=replace(b.frozen, directory_name=name(f"calendar.{token}.right")),
        ),
        name(f"mapping.{token}"),
        name=a.name,
    )


@overload
def infer_frame_combine_schema(
    left: DataSchema, right: DataSchema, operation: FrameBinaryOperationKind
) -> DataSchema: ...
@overload
def infer_frame_combine_schema(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    operation: FrameBinaryOperationKind,
) -> DataSchema | PendingDataSchema: ...


def infer_frame_combine_schema(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    operation: FrameBinaryOperationKind,
) -> DataSchema | PendingDataSchema:
    """Derive one complete binary layout from captured current structures only."""
    if type(operation.pairs) is not tuple or any(
        type(pair) is not tuple or len(pair) != 2 or any(type(i) is not int for i in pair)
        for pair in operation.pairs
    ):
        reject(
            "immutable integer axis-index pairs",
            repr(operation.pairs),
            "Resolve complete axis handles before planning.",
            "frame.combine",
        )
    if (
        (operation.match is not None and type(operation.match) is not CalendarCorrespondence)
        or operation.how not in ("exact", "outer")
        or (
            isinstance(operation, CompareFrameValues)
            and operation.zero_denominator not in ("null", "error")
        )
    ):
        reject(
            "closed binary policies",
            repr(operation),
            "Use exact/outer and null/error policies.",
            "frame.combine",
        )
    left_axes: tuple[DataAxis | PendingDataAxis, ...] = left.axes
    right_axes: tuple[DataAxis | PendingDataAxis, ...] = right.axes
    left_values: tuple[DataColumn | PendingDataColumn, ...] = left.values
    right_values: tuple[DataColumn | PendingDataColumn, ...] = right.values
    left_roles: tuple[AxisRole, ...] = tuple(
        a.role if isinstance(a, PendingDataAxis) else axis_role(a) for a in left_axes
    )
    right_roles: tuple[AxisRole, ...] = tuple(
        a.role if isinstance(a, PendingDataAxis) else axis_role(a) for a in right_axes
    )
    if "paired" in (*left_roles, *right_roles):
        reject(
            "inputs without paired axes",
            repr((left_roles, right_roles)),
            "Reduce away paired axes or construct new source inputs.",
            "frame.combine",
        )
    if len(left.axes) != len(right.axes):
        reject(
            "equal complete axis counts",
            repr((len(left.axes), len(right.axes))),
            "Reduce or reconstruct inputs with compatible axes.",
            "frame.combine",
        )
    if (
        len(operation.pairs) != len(left.axes)
        or {a for a, _ in operation.pairs} != set(range(len(left.axes)))
        or {b for _, b in operation.pairs} != set(range(len(right.axes)))
    ):
        reject(
            "a complete axis bijection",
            repr(operation.pairs),
            "Bind every complete axis exactly once.",
            "frame.combine",
        )
    partners = dict(operation.pairs)
    axis_columns: list[tuple[DataColumn | PendingDataColumn, ...]] = []
    for i, axis in enumerate(left_axes):
        other = right_axes[partners[i]]
        if (
            left_roles[i] != right_roles[partners[i]]
            or len(axis.columns) != len(other.columns)
            or axis.at != other.at
        ):
            reject(
                "matching roles, complete key arity and time interpretation",
                repr((axis, other)),
                "Bind compatible complete axes.",
                "frame.combine",
            )
        columns: list[DataColumn | PendingDataColumn] = []
        left_components: tuple[DataColumn | PendingDataColumn, ...] = axis.columns
        right_components: tuple[DataColumn | PendingDataColumn, ...] = other.columns
        for a, b in zip(left_components, right_components, strict=True):
            if isinstance(a, DataColumn) and isinstance(b, DataColumn):
                compatible = a.dtype == b.dtype
            else:
                ak, bk = column_kind(a), column_kind(b)
                compatible = ak == bk or "unresolved" in (ak, bk)
            if not compatible:
                reject(
                    "equal complete physical key types",
                    repr((a, b)),
                    "Bind axes with matching complete key component types.",
                    "frame.combine",
                )
            nullable = (
                True
                if a.nullable is True or b.nullable is True
                else None
                if a.nullable is None or b.nullable is None
                else False
            )
            columns.append(
                replace(a, nullable=nullable)
                if isinstance(a, PendingDataColumn)
                else replace(_pending_column(a), nullable=nullable)
                if nullable is None
                else replace(a, nullable=nullable)
            )
        if isinstance(axis, CalendarDataAxis) and isinstance(other, CalendarDataAxis):
            require_compatible_calendar_axes(axis, other)
            if operation.match is not None:
                require_calendar_match_rule(axis, operation.match)
        axis_columns.append(tuple(columns))
    calendars = tuple(i for i, role in enumerate(left_roles) if role == "calendar")
    matched = calendars[0] if operation.match is not None and len(calendars) == 1 else None
    if operation.match is not None and matched is None:
        reject(
            "exactly one compatible calendar axis pair for match",
            repr((left_roles, right_roles)),
            "Use equality without match, or explicitly construct one compatible calendar axis pair.",
            "frame.combine",
        )
    if matched is not None:
        axis_columns[matched] = (_paired_layout(left, right, matched)[1],)
    compare = isinstance(operation, CompareFrameValues)
    if compare and (not left.value_paths or set(left.value_paths) != set(right.value_paths)):
        reject(
            "equal nonempty complete value path sets",
            repr((left.value_paths, right.value_paths)),
            "Select and name the same complete numeric value paths on both inputs.",
            "frame.compare",
        )
    absolute_types: list[DataType | None] = []
    if compare:
        for path, a in zip(left.value_paths, left_values, strict=True):
            b = right_values[right.value_paths.index(path)]
            kinds = (column_kind(a), column_kind(b))
            if (
                a.unit != b.unit
                or any(k not in ("numeric", "duration", "unresolved") for k in kinds)
                or ("duration" in kinds and "numeric" in kinds)
            ):
                reject(
                    "matching units and comparable numeric values",
                    repr((a, b)),
                    "Select compatible values with equal units before compare.",
                    "frame.compare",
                )
            absolute_types.append(
                _compare_type(a.dtype, b.dtype)
                if isinstance(a, DataColumn) and isinstance(b, DataColumn)
                else None
            )
    paths = tuple(
        (side, *path)
        for side, schema in (("left", left), ("right", left if compare else right))
        for path in schema.value_paths
    )
    if compare:
        paths += tuple(
            (side, *path) for side in ("absolute", "relative") for path in left.value_paths
        )
    presence_paths = (
        ("left_present",),
        ("right_present",),
        *(
            (side, *path)
            for side, schema in (("left", left), ("right", right))
            for path in schema.presence_paths
        ),
    )
    pending = isinstance(left, PendingDataSchema) or isinstance(right, PendingDataSchema)
    values: list[DataColumn | PendingDataColumn] = []
    count = len(left.values)
    for index, path in enumerate(paths):
        side, original = path[0], path[1:]
        source = right if side == "right" else left
        column = source.values[source.value_paths.index(original)]
        nullable = (
            True
            if side in ("absolute", "relative") or operation.how == "outer"
            else column.nullable
        )
        unit = None if side == "relative" else column.unit
        if pending:
            kind = "numeric" if side == "relative" else column_kind(column)
            values.append(PendingDataColumn(f"value.{index}", ".".join(path), kind, nullable, unit))
        else:
            assert isinstance(column, DataColumn)
            dtype = (
                ScalarDataType("float64")
                if side == "relative"
                else absolute_types[index % count]
                if side == "absolute"
                else column.dtype
            )
            assert dtype is not None
            values.append(DataColumn(f"value.{index}", ".".join(path), dtype, bool(nullable), unit))
    if pending:
        pending_axes: list[PendingFrameAxis] = []
        for i, axis in enumerate(left_axes):
            if i == matched:
                pending_axes.append(_paired_axis(left, right, i, partners[i]))
            elif isinstance(axis, CalendarDataAxis):
                pending_axes.append(
                    replace(
                        axis, columns=tuple(c for c in axis_columns[i] if isinstance(c, DataColumn))
                    )
                )
            else:
                role = axis_role(axis)
                if role not in ("field", "identity"):
                    reject(
                        "ordinary unresolved axes or closed calendar descriptors",
                        repr(axis),
                        "Retain captured calendar facts in the current structure.",
                        "frame.combine",
                    )
                pending_axes.append(
                    PendingDataAxis(
                        axis.token,
                        axis.name,
                        role,
                        tuple(_pending_column(c) for c in axis_columns[i]),
                        axis.at,
                    )
                )
        return PendingDataSchema(
            tuple(pending_axes),
            tuple(_pending_column(c) for c in values),
            paths,
            tuple(
                PendingDataColumn(f"presence.{i}", ".".join(p), "boolean", False)
                for i, p in enumerate(presence_paths)
            ),
            presence_paths,
        )
    concrete_axes: list[DataAxis] = []
    for i, axis in enumerate(left_axes):
        assert isinstance(axis, DataAxis)
        concrete_columns = tuple(c for c in axis_columns[i] if isinstance(c, DataColumn))
        if i == matched:
            concrete_axes.append(_paired_axis(left, right, i, partners[i]))
        else:
            concrete_axes.append(replace(axis, columns=concrete_columns))
    return DataSchema(
        tuple(concrete_axes),
        tuple(c for c in values if isinstance(c, DataColumn)),
        tuple(
            DataColumn(f"presence.{i}", ".".join(p), ScalarDataType("boolean"), False)
            for i, p in enumerate(presence_paths)
        ),
        value_paths=paths,
        presence_paths=presence_paths,
    )
