"""Closed data expressions and pure current-structure inference."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, TypeAlias, overload

import ibis

from marivo._definition.correspondence import CalendarCorrespondence
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
    ValueSlot,
    ValueUnary,
)
from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo.analysis.core.attribute_game import AttributeGameValues
from marivo.analysis.core.data_schema import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    ComparisonValueBinding,
    DataAxis,
    DataColumn,
    DataComparison,
    DataSchema,
    EndpointPresence,
    IntervalComparisonBinding,
    IntervalDataAxis,
    OrdinaryComparisonBinding,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.frame_attribute_merge import AttributeMergeValues
from marivo.analysis.core.frame_fix import FixFrameValues, validate_fixed_key
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

FrameUnaryOperationKind: TypeAlias = (
    SelectValues | FilterValues | TopValues | ReduceValues | FixFrameValues
)

if TYPE_CHECKING:
    from marivo.analysis.binding.attribute_rules import AttributeRule


@dataclass(frozen=True, slots=True)
class AlignFrameValues:
    pairs: tuple[tuple[int, int], ...]
    match: CalendarCorrespondence | None = None


@dataclass(frozen=True, slots=True)
class NamedFrameComparison:
    alias: str
    pairs: tuple[tuple[int, int], ...]
    match: CalendarCorrespondence | None = None


@dataclass(frozen=True, slots=True)
class CompareFrameValues:
    baselines: tuple[NamedFrameComparison, ...]


@dataclass(frozen=True, slots=True)
class AttributeFrameValues:
    """Endpoint parent positions refer to complete source axis groups."""

    left_parents: tuple[int, ...]
    right_parents: tuple[int, ...]
    how: Literal["exact", "outer"]
    left_projection: str
    right_projection: str


@dataclass(frozen=True, slots=True)
class AttributeInputsValues:
    """Private preparation matrix, never a successful contribution result."""

    mapping: AttributeFrameValues
    source_indices: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class FrameAttributeTarget:
    path: tuple[str, ...]
    mode: Literal["level", "absolute", "relative"]
    observed: int


@dataclass(frozen=True, slots=True)
class AttributeContributionValues:
    """Explicit contribution consumer in the ordinary Frame expression."""

    mapping: AttributeFrameValues
    rules: tuple[tuple[tuple[str, AttributeRule], ...], tuple[tuple[str, AttributeRule], ...]]
    target: FrameAttributeTarget | None = None
    single_endpoint: bool = False
    source_rules: (
        tuple[tuple[tuple[str, AttributeRule], ...], tuple[tuple[str, AttributeRule], ...]] | None
    ) = None
    source_indices: tuple[int, int] | None = None
    source_sides: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class AttributeProjectValues:
    """One side of the selected frozen parent domain, with original keys retained."""

    side: Literal["left", "right"]
    ordinary: tuple[int, ...]
    calendar: int | None
    target_calendar: CalendarCoordinate | IntervalCoordinate | None = None
    how: Literal["exact", "outer"] = "exact"
    comparison_alias: str | None = None
    presence: tuple[str, ...] | None = None


FrameOperationKind: TypeAlias = (
    FrameUnaryOperationKind
    | AlignFrameValues
    | CompareFrameValues
    | AttributeInputsValues
    | AttributeContributionValues
    | AttributeProjectValues
    | AttributeMergeValues
    | AttributeGameValues
)


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
    from marivo.analysis.core.value_types import data_type, expression_value, ibis_type

    admitted(value)
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
        from marivo.analysis.core.value_types import literal_kind

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
def infer_frame_schema(schema: DataSchema, operation: FrameUnaryOperationKind) -> DataSchema: ...
@overload
def infer_frame_schema(
    schema: PendingDataSchema, operation: FrameUnaryOperationKind
) -> PendingDataSchema: ...


def infer_frame_schema(
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
    concrete: list[DataColumn] = []
    for index, (name, item) in enumerate(operation.values):
        if isinstance(item, PairStatistic):
            _validate_pair_statistic(schema, item)
            dtype = ScalarDataType("int64" if item.method == "pair_count" else "float64")
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


def _decimal_column_type(
    left: DataType, right: DataType, *, multiply: bool = False
) -> DecimalDataType:
    """Describe the supported carrier without rounding native scalar arithmetic."""
    p1, s1 = (left.precision, left.scale) if isinstance(left, DecimalDataType) else (19, 0)
    p2, s2 = (right.precision, right.scale) if isinstance(right, DecimalDataType) else (19, 0)
    scale = s1 + s2 if multiply else max(s1, s2)
    precision = p1 + p2 if multiply else max(p1 - s1, p2 - s2) + scale + 1
    return DecimalDataType(min(38, precision), scale)


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
        return _decimal_column_type(left, right)
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
    assert isinstance(a, (CalendarDataAxis, IntervalDataAxis)) and isinstance(
        b, (CalendarDataAxis, IntervalDataAxis)
    )
    assert a.frozen is not None and b.frozen is not None
    token, key = _paired_layout(left, right, index)
    used = {
        axis.frozen.directory_name
        for schema in (left, right)
        for axis in schema.axes
        if isinstance(axis, (CalendarDataAxis, IntervalDataAxis)) and axis.frozen is not None
    }

    def name(base: str) -> str:
        candidate, n = base, 0
        while candidate in used:
            n += 1
            candidate = f"{base}.{n}"
        used.add(candidate)
        return candidate

    def endpoint(
        axis: CalendarDataAxis | IntervalDataAxis, side: str
    ) -> CalendarDataAxis | IntervalDataAxis:
        assert axis.frozen is not None
        directory = name(f"{axis_role(axis)}.{token}.{side}")
        if isinstance(axis, CalendarDataAxis):
            return replace(
                axis,
                token=f"{token}.{side}",
                frozen=replace(axis.frozen, directory_name=directory, direct=None),
            )
        return replace(
            axis, token=f"{token}.{side}", frozen=replace(axis.frozen, directory_name=directory)
        )

    return PairedDataAxis(
        token,
        (key,),
        endpoint(a, "left"),
        endpoint(b, "right"),
        name(f"mapping.{token}"),
        name=a.name,
    )


@overload
def infer_frame_combine_schema(
    left: DataSchema, right: DataSchema, operation: AlignFrameValues
) -> DataSchema: ...
@overload
def infer_frame_combine_schema(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    operation: AlignFrameValues,
) -> DataSchema | PendingDataSchema: ...


def infer_frame_combine_schema(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    operation: AlignFrameValues,
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
    if operation.match is not None and type(operation.match) is not CalendarCorrespondence:
        reject(
            "closed binary policies",
            repr(operation),
            "Use a closed calendar correspondence rule.",
            "frame.combine",
        )
    left_axes: tuple[DataAxis | PendingDataAxis, ...] = left.axes
    right_axes: tuple[DataAxis | PendingDataAxis, ...] = right.axes
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
        if (
            isinstance(axis, IntervalDataAxis)
            and operation.match is not None
            and operation.match.kind != "ordinal"
        ):
            reject(
                "ordinal for interval coordinates",
                repr(operation.match),
                "Use ordinal for one declared interval on each side.",
                "frame.combine",
            )
        axis_columns.append(tuple(columns))
    calendars = tuple(i for i, role in enumerate(left_roles) if role in ("calendar", "interval"))
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
    paths = tuple(
        (side, *path)
        for side, schema in (("left", left), ("right", right))
        for path in schema.value_paths
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
    for index, path in enumerate(paths):
        side, original = path[0], path[1:]
        source = right if side == "right" else left
        column = source.values[source.value_paths.index(original)]
        nullable = True
        unit = column.unit
        if pending:
            kind = column_kind(column)
            values.append(PendingDataColumn(f"value.{index}", ".".join(path), kind, nullable, unit))
        else:
            assert isinstance(column, DataColumn)
            dtype = column.dtype
            values.append(DataColumn(f"value.{index}", ".".join(path), dtype, bool(nullable), unit))
    if pending:
        pending_axes: list[PendingFrameAxis] = []
        for i, axis in enumerate(left_axes):
            if i == matched:
                pending_axes.append(_paired_axis(left, right, i, partners[i]))
            elif isinstance(axis, (CalendarDataAxis, IntervalDataAxis)):
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
            (
                *transport_comparisons(
                    left,
                    tuple(pending_axes),
                    value_prefix=("left",),
                    presence_prefix=("left",),
                    namespace="left",
                    axis_pairs=tuple((i, i) for i in range(len(left.axes))),
                    endpoint_path=("left_present",),
                ),
                *transport_comparisons(
                    right,
                    tuple(pending_axes),
                    value_prefix=("right",),
                    presence_prefix=("right",),
                    namespace="right",
                    axis_pairs=tuple((j, i) for i, j in operation.pairs),
                    endpoint_path=("right_present",),
                ),
            ),
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
        comparisons=(
            *transport_comparisons(
                left,
                tuple(concrete_axes),
                value_prefix=("left",),
                presence_prefix=("left",),
                namespace="left",
                axis_pairs=tuple((i, i) for i in range(len(left.axes))),
                endpoint_path=("left_present",),
            ),
            *transport_comparisons(
                right,
                tuple(concrete_axes),
                value_prefix=("right",),
                presence_prefix=("right",),
                namespace="right",
                axis_pairs=tuple((j, i) for i, j in operation.pairs),
                endpoint_path=("right_present",),
            ),
        ),
    )


@overload
def infer_frame_compare_schema(
    inputs: tuple[DataSchema, ...], operation: CompareFrameValues
) -> DataSchema: ...
@overload
def infer_frame_compare_schema(
    inputs: tuple[DataSchema | PendingDataSchema, ...], operation: CompareFrameValues
) -> DataSchema | PendingDataSchema: ...


def infer_frame_compare_schema(
    inputs: tuple[DataSchema | PendingDataSchema, ...], operation: CompareFrameValues
) -> DataSchema | PendingDataSchema:
    """Infer one current layout and all named baseline value groups together."""
    from marivo.analysis.core.frame_structure import validate_paths

    if (
        type(operation.baselines) is not tuple
        or not operation.baselines
        or any(type(item) is not NamedFrameComparison for item in operation.baselines)
        or len(inputs) != len(operation.baselines) + 1
    ):
        reject(
            "one current input and ordered named baseline bindings",
            repr(operation),
            "Bind a nonempty named baseline mapping.",
            "frame.compare",
        )
    aliases = tuple(item.alias for item in operation.baselines)
    validate_paths(tuple((alias,) for alias in aliases), location="frame.compare.aliases")
    if "current" in aliases:
        reject(
            "baseline aliases other than current",
            repr(aliases),
            "Rename the baseline alias.",
            "frame.compare",
        )
    current = inputs[0]
    current_values: tuple[DataColumn | PendingDataColumn, ...] = current.values
    pending = any(isinstance(schema, PendingDataSchema) for schema in inputs)
    axes = current.axes
    paths = current.value_paths
    values: list[DataColumn | PendingDataColumn] = []
    facts: list[DataComparison] = []
    key_slots = {column.slot_id for axis in axes for column in axis.columns}
    key_nullability = {
        column.slot_id: column.nullable for axis in current.axes for column in axis.columns
    }

    def value(
        path: tuple[str, ...],
        source: DataColumn | PendingDataColumn,
        dtype: DataType | None = None,
        *,
        relative: bool = False,
        difference: bool = False,
    ) -> None:
        slot = f"compare.value.{len(values)}"
        while slot in key_slots:
            slot += ".value"
        nullable = True
        unit = None if relative else source.unit
        if pending:
            values.append(
                PendingDataColumn(
                    slot,
                    ".".join(path),
                    "numeric" if relative else column_kind(source),
                    nullable,
                    unit,
                )
            )
        else:
            assert isinstance(source, DataColumn)
            values.append(
                DataColumn(slot, ".".join(path), dtype or source.dtype, bool(nullable), unit)
            )

    for path, column in zip(current.value_paths, current_values, strict=True):
        value(path, column)
    for item, baseline in zip(operation.baselines, inputs[1:], strict=True):
        baseline_values: tuple[DataColumn | PendingDataColumn, ...] = baseline.values
        validated = infer_frame_combine_schema(current, baseline, AlignFrameValues(item.pairs))
        axes = validated.axes
        for left_position, right_position in item.pairs:
            left_components: tuple[DataColumn | PendingDataColumn, ...] = current.axes[
                left_position
            ].columns
            right_components: tuple[DataColumn | PendingDataColumn, ...] = baseline.axes[
                right_position
            ].columns
            for left_component, right_component in zip(
                left_components, right_components, strict=True
            ):
                flags = (key_nullability[left_component.slot_id], right_component.nullable)
                key_nullability[left_component.slot_id] = (
                    True if True in flags else None if None in flags else False
                )
        temporal = tuple(
            (i, j) for i, j in item.pairs if axis_role(current.axes[i]) in ("calendar", "interval")
        )
        if len(temporal) > 1 or (item.match is not None and not temporal):
            reject(
                "at most one compatible time axis pair, required for match",
                repr(temporal),
                "Construct inputs with one time axis or compare ordinary keys without match.",
                "frame.compare",
            )
        binding: (
            OrdinaryComparisonBinding | CalendarComparisonBinding | IntervalComparisonBinding
        ) = OrdinaryComparisonBinding()
        if temporal:
            i, j = temporal[0]
            left, right = current.axes[i], baseline.axes[j]
            token = f"comparison.{item.alias}.baseline.{right.token}"
            components: tuple[DataColumn | PendingDataColumn, ...] = right.columns
            columns = tuple(
                replace(c, slot_id=f"{token}.key.{k}")
                for k, c in enumerate(components)
                if isinstance(c, DataColumn)
            )
            name = f"comparison.{item.alias}.directory"
            if isinstance(left, CalendarDataAxis) and isinstance(right, CalendarDataAxis):
                if item.match is not None:
                    require_calendar_match_rule(left, item.match)
                assert right.frozen is not None
                endpoint = replace(
                    right,
                    token=token,
                    columns=columns,
                    frozen=replace(right.frozen, directory_name=name, direct=None),
                )
                binding = CalendarComparisonBinding(
                    left.token, endpoint, f"comparison.{item.alias}.map"
                )
            else:
                assert isinstance(left, IntervalDataAxis) and isinstance(right, IntervalDataAxis)
                if item.match is not None and item.match.kind != "ordinal":
                    reject(
                        "ordinal for interval coordinates",
                        repr(item.match),
                        "Use ordinal for declared interval endpoints.",
                        "frame.compare",
                    )
                endpoint_interval = replace(
                    right,
                    token=token,
                    columns=columns,
                    frozen=replace(right.frozen, directory_name=name),
                )
                binding = IntervalComparisonBinding(
                    left.token, endpoint_interval, f"comparison.{item.alias}.map"
                )
        elif item.match is not None:
            reject(
                "a time axis for match",
                repr(item.match),
                "Compare ordinary keys without match.",
                "frame.compare",
            )
        if not current.value_paths or set(current.value_paths) != set(baseline.value_paths):
            reject(
                "equal nonempty complete value path sets",
                repr((current.value_paths, baseline.value_paths)),
                "Select and name the same complete numeric value paths on every input.",
                "frame.compare",
            )
        types: list[DataType | None] = []
        for path, left_value in zip(current.value_paths, current_values, strict=True):
            right_value = baseline_values[baseline.value_paths.index(path)]
            kinds = (column_kind(left_value), column_kind(right_value))
            if (
                left_value.unit != right_value.unit
                or any(kind not in ("numeric", "duration", "unresolved") for kind in kinds)
                or ("duration" in kinds and "numeric" in kinds)
            ):
                reject(
                    "matching units and comparable numeric values",
                    repr((left_value, right_value)),
                    "Select compatible values with equal units before compare.",
                    "frame.compare",
                )
            types.append(
                _compare_type(left_value.dtype, right_value.dtype)
                if isinstance(left_value, DataColumn) and isinstance(right_value, DataColumn)
                else None
            )
        bindings = [ComparisonValueBinding("current", path, path) for path in current.value_paths]
        for role in ("baseline", "absolute", "relative"):
            for index, path in enumerate(current.value_paths):
                output = (item.alias, role, *path)
                if output in paths:
                    reject(
                        "distinct generated comparison paths",
                        ".".join(output),
                        "Select and rename conflicting input values before comparing.",
                        "frame.compare",
                    )
                source = (
                    baseline_values[baseline.value_paths.index(path)]
                    if role == "baseline"
                    else current_values[index]
                )
                value(
                    output,
                    source,
                    ScalarDataType("float64")
                    if role == "relative"
                    else types[index]
                    if role == "absolute"
                    else None,
                    relative=role == "relative",
                    difference=role != "baseline",
                )
                paths += (output,)
                bindings.append(ComparisonValueBinding(role, path, output))
        facts.append(
            DataComparison(
                item.alias,
                tuple(bindings),
                binding,
                EndpointPresence(
                    ("current", "present"),
                    tuple(("current", "presence", *path) for path in current.presence_paths),
                ),
                EndpointPresence(
                    (item.alias, "present"),
                    tuple((item.alias, "presence", *path) for path in baseline.presence_paths),
                ),
            )
        )
    presence_paths = (
        ("current", "present"),
        *((item.alias, "present") for item in operation.baselines),
        *(
            (name, "presence", *path)
            for name, schema in zip(("current", *aliases), inputs, strict=True)
            for path in schema.presence_paths
        ),
    )
    facts.extend(
        transport_comparisons(
            current,
            axes,
            value_prefix=(),
            presence_prefix=("current", "presence"),
            namespace="current",
            axis_pairs=tuple((i, i) for i in range(len(axes))),
            endpoint_path=("current", "present"),
        )
    )
    for item, baseline in zip(operation.baselines, inputs[1:], strict=True):
        facts.extend(
            transport_comparisons(
                baseline,
                axes,
                value_prefix=(item.alias, "baseline"),
                presence_prefix=(item.alias, "presence"),
                namespace=item.alias,
                axis_pairs=tuple((j, i) for i, j in item.pairs),
                endpoint_path=(item.alias, "present"),
            )
        )
    validate_paths(paths, location="frame.compare.values")
    validate_paths(presence_paths, location="frame.compare.presence")
    if set(paths) & set(presence_paths):
        reject(
            "disjoint generated value and presence paths",
            repr(paths),
            "Select and rename conflicting input values.",
            "frame.compare",
        )
    if pending:
        pending_axes: list[PendingFrameAxis] = []
        for axis in axes:
            if isinstance(
                axis, (PendingDataAxis, CalendarDataAxis, IntervalDataAxis, PairedDataAxis)
            ):
                pending_axes.append(axis)
            else:
                role = axis_role(axis)
                assert role in ("field", "identity")
                pending_axes.append(
                    PendingDataAxis(
                        axis.token,
                        axis.name,
                        role,
                        tuple(
                            replace(_pending_column(c), nullable=key_nullability[c.slot_id])
                            for c in axis.columns
                        ),
                        axis.at,
                    )
                )
        return PendingDataSchema(
            tuple(pending_axes),
            tuple(_pending_column(c) for c in values),
            paths,
            tuple(
                PendingDataColumn(f"compare.presence.{i}", ".".join(path), "boolean", False)
                for i, path in enumerate(presence_paths)
            ),
            presence_paths,
            tuple(facts),
        )
    return DataSchema(
        tuple(
            replace(
                axis,
                columns=tuple(
                    replace(c, nullable=bool(key_nullability[c.slot_id])) for c in axis.columns
                ),
            )
            for axis in axes
            if isinstance(axis, DataAxis)
        ),
        tuple(c for c in values if isinstance(c, DataColumn)),
        tuple(
            DataColumn(f"compare.presence.{i}", ".".join(path), ScalarDataType("boolean"), False)
            for i, path in enumerate(presence_paths)
        ),
        value_paths=paths,
        presence_paths=presence_paths,
        comparisons=tuple(facts),
    )


def transport_comparisons(
    schema: DataSchema | PendingDataSchema,
    output_axes: tuple[DataAxis | PendingDataAxis, ...],
    *,
    value_prefix: tuple[str, ...],
    presence_prefix: tuple[str, ...],
    namespace: str,
    axis_pairs: tuple[tuple[int, int], ...],
    endpoint_path: tuple[str, ...],
    same_domains: frozenset[str] | None = None,
) -> tuple[DataComparison, ...]:
    """Transport exact captured bindings; finite runtime authority may remove certification."""
    positions = dict(axis_pairs)
    wrapper = EndpointPresence(
        endpoint_path, tuple((*presence_prefix, *path) for path in schema.presence_paths)
    )

    def endpoint(record: EndpointPresence) -> EndpointPresence:
        return EndpointPresence(
            (*presence_prefix, *record.path),
            tuple((*presence_prefix, *path) for path in record.inherited_paths),
        )

    result: list[DataComparison] = []
    for fact in schema.comparisons:
        scoped = (namespace, *fact.namespace)
        binding = fact.binding
        values = tuple(
            replace(value, value_path=(*value_prefix, *value.value_path)) for value in fact.values
        )
        if isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding)):
            source = next(
                i for i, axis in enumerate(schema.axes) if axis.token == binding.current_axis_token
            )
            target = output_axes[positions[source]]
            expected = (
                CalendarDataAxis
                if isinstance(binding, CalendarComparisonBinding)
                else IntervalDataAxis
            )
            if type(target) is not expected or (
                same_domains is not None and binding.current_axis_token not in same_domains
            ):
                binding = OrdinaryComparisonBinding()
                values = ()
            else:
                prefix = ".".join(("comparison", "inherited", *scoped, fact.alias))
                baseline = binding.baseline
                assert baseline.frozen is not None
                columns = tuple(
                    replace(column, slot_id=f"{prefix}.key.{i}")
                    for i, column in enumerate(baseline.columns)
                )
                if isinstance(binding, CalendarComparisonBinding):
                    assert isinstance(baseline, CalendarDataAxis) and baseline.frozen is not None
                    copied = replace(
                        baseline,
                        token=f"{prefix}.baseline",
                        columns=columns,
                        frozen=replace(
                            baseline.frozen, directory_name=f"{prefix}.directory", direct=None
                        ),
                    )
                    binding = CalendarComparisonBinding(target.token, copied, f"{prefix}.map")
                else:
                    assert isinstance(baseline, IntervalDataAxis)
                    copied_interval = replace(
                        baseline,
                        token=f"{prefix}.baseline",
                        columns=columns,
                        frozen=replace(baseline.frozen, directory_name=f"{prefix}.directory"),
                    )
                    binding = IntervalComparisonBinding(
                        target.token, copied_interval, f"{prefix}.map"
                    )
        result.append(
            DataComparison(
                fact.alias,
                values,
                binding,
                endpoint(fact.current_presence),
                endpoint(fact.baseline_presence),
                tuple(
                    dict.fromkeys(
                        (*tuple(endpoint(record) for record in fact.enclosing_presence), wrapper)
                    )
                ),
                scoped,
            )
        )
    return tuple(result)
