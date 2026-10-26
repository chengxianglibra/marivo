"""Closed Attribute programs, diagnostics and source-free structure inference."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, TypeAlias, overload

from marivo._definition.query import ValueBinary, ValueSlot
from marivo.analysis.binding.attribute_rules import (
    AdditiveSource,
    AttributeRule,
    AttributeSourceRequest,
    DirectRatio,
    NativeMean,
    NativeWeightedMean,
    RatioComponentRule,
    RuleAdd,
    RuleDivide,
    RuleNegate,
    RuleRescale,
    RuleScale,
    RuleSubtract,
)
from marivo.analysis.binding.attribute_units import ratio_component_units, rule_unit
from marivo.analysis.core.data_schema import (
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.frame_expression import (
    AttributeContributionValues,
    AttributeFrameValues,
    AttributeInputsValues,
    AttributeProjectValues,
    _compare_type,
    _pending_column,
    expression_type,
)
from marivo.analysis.core.frame_structure import (
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    PendingFrameAxis,
    axis_role,
    column_kind,
)
from marivo.analysis.core.frozen_calendar import FrozenKey
from marivo.analysis.core.model import CoreRuleError, reject

if TYPE_CHECKING:
    from marivo._definition.expressions import TimeBinding
    from marivo._definition.observations import ObservationPeriod
    from marivo._definition.types import CalendarCoordinate
    from marivo.analysis.compiler.observation import FormulaProgram
    from marivo.analysis.core.frame_method_facts import (
        FrameMethodCoordinate,
        FrameMethodParent,
        FrameMethodTarget,
        FrameMethodValue,
    )


@dataclass(frozen=True, slots=True)
class AttributeUseContext:
    endpoint: Literal["left", "right"]
    metric: str
    occurrence: tuple[int, ...]
    logical_occurrence: tuple[int, ...]
    root: str


@dataclass(frozen=True, slots=True)
class AttributeParentFiber:
    parent: FrozenKey
    parent_columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class AttributeFineFiber:
    parent: FrozenKey
    parent_columns: tuple[DataColumn, ...]
    fine: FrozenKey
    fine_columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class AttributeWindowFiber:
    fiber: AttributeParentFiber | AttributeFineFiber
    window_key: int
    period: ObservationPeriod
    time: TimeBinding
    calendar: CalendarCoordinate


@dataclass(frozen=True, slots=True)
class AttributeCoverageWitness:
    identity: FrozenKey


@dataclass(frozen=True, slots=True)
class AttributePartitionWitness:
    kind: Literal["root", "distinct"]
    identity: FrozenKey
    groups: tuple[FrozenKey, ...]


@dataclass(frozen=True, slots=True)
class AttributeAggregateWitness:
    scope: Literal["parent", "fine"]
    key: FrozenKey
    columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class AttributeCheckWitness:
    check: str
    source: str
    coordinates: tuple[FrozenKey, ...]
    columns: tuple[DataColumn, ...]


@dataclass(frozen=True, slots=True)
class AttributeNumericWitness:
    path: tuple[int, ...]
    operation: Literal["original", "add", "sub", "mul", "div", "denominator", "contribution"]
    values: tuple[FrameMethodValue, ...]


@dataclass(frozen=True, slots=True)
class AttributeClosureWitness:
    target: tuple[str, ...]
    kind: Literal["level", "absolute", "relative"]
    total: int | float | Decimal
    contribution_sum: int | float | Decimal
    residual: int | float | Decimal
    tolerance: int | float | Decimal
    policy: Literal["exact_integer", "duration_ticks", "decimal_rounding", "float64"]
    total_column: DataColumn


AttributeWitness: TypeAlias = (
    AttributeCoverageWitness
    | AttributePartitionWitness
    | AttributeAggregateWitness
    | AttributeCheckWitness
    | AttributeNumericWitness
    | AttributeClosureWitness
)


@dataclass(frozen=True, slots=True)
class AttributeFailureContext:
    uses: tuple[AttributeUseContext, ...]
    fiber: AttributeParentFiber | AttributeFineFiber | AttributeWindowFiber
    witness: AttributeWitness


@dataclass(frozen=True, slots=True)
class AttributeWindowInput:
    """Captured fixed source fiber; actual coverage remains an execution check."""

    window_key: int
    calendar_slot: str
    period: ObservationPeriod
    time: TimeBinding
    calendar: CalendarCoordinate


@dataclass(frozen=True, slots=True)
class AttributeMatrixSource:
    source: str
    use: AttributeUseContext
    window: AttributeWindowInput | None


@dataclass(frozen=True, slots=True)
class AttributeSourceParentBinding:
    parent_schema: DataSchema
    projection_input_index: int


@dataclass(frozen=True, slots=True)
class AttributeParentContext:
    parent_columns: tuple[DataColumn, ...]
    parent_mapping: tuple[tuple[FrozenKey, FrozenKey], ...]


@dataclass(frozen=True, slots=True)
class AttributeFormulaBinding:
    uses: tuple[AttributeUseContext, ...]
    parent_schema: DataSchema
    source_parent_columns: tuple[DataColumn, ...]
    projection_input_index: int


@dataclass(frozen=True, slots=True)
class AttributeFormulaContext:
    uses: tuple[AttributeUseContext, ...]
    keys: tuple[DataColumn, ...]
    source_parent_columns: tuple[DataColumn, ...]
    parent_columns: tuple[DataColumn, ...]
    fine_columns: tuple[DataColumn, ...]
    parent_mapping: tuple[tuple[FrozenKey, FrozenKey], ...]


AttributeInputReason = Literal[
    "missing_component_proof",
    "unqualified_contribution_route",
    "undefined_input",
    "overlapping_groups",
    "root_denominator_zero",
    "closure_mismatch",
]


class AttributeInputError(CoreRuleError):
    """An explicit contribution input lacks a captured or runtime premise."""

    def __init__(
        self,
        *,
        reason: AttributeInputReason,
        received: str,
        context: AttributeFailureContext | None = None,
    ) -> None:
        self.reason = reason
        self.attribute_context = context
        repairs = {
            "closure_mismatch": "Inspect the independent parent total and fine contributions, then explicitly rerun the request if the source facts changed.",
            "unqualified_contribution_route": "Use qualified native physical tables with the captured complete parent parameter dependencies.",
            "missing_component_proof": "Retain the exact component, complete parent mapping and qualified sample/window proof before requesting contribution.",
            "undefined_input": "Cover every scoped root identity with a complete joint classification; restrict the endpoint with where or members when needed.",
            "overlapping_groups": "Provide one joint classification tuple per counted identity inside each selected parent, or submit a new explicit finer observation.",
            "root_denominator_zero": "Provide nonzero complete-parent denominators for both endpoints, or build a finer observation to inspect the original nullable Metric.",
        }
        super().__init__(
            expected=(
                "closure_mismatch: independent parent and fine final target values within the numeric closure policy"
                if reason == "closure_mismatch"
                else f"{reason}: a qualified complete Attribute input"
            ),
            received=received,
            repair=repairs[reason],
            location="frame.attribute",
            canonical_id="analysis_frame.attribute",
        )


@dataclass(frozen=True, slots=True)
class AttributeDomainProgram:
    operation: AttributeFrameValues
    left_schema: DataSchema
    right_schema: DataSchema
    parent_schema: DataSchema
    left_domain_schema: DataSchema
    right_domain_schema: DataSchema
    output_schema: DataSchema


@dataclass(frozen=True, slots=True)
class AttributeMatrixColumn:
    side: Literal["left", "right"]
    scope: Literal["parent", "fine"]
    occurrence: tuple[int, ...]
    role: str
    target: DataColumn
    sources: tuple[AttributeMatrixSource, ...]


@dataclass(frozen=True, slots=True)
class AttributeReadBinding:
    input_index: int
    side: Literal["left", "right"]
    scope: Literal["parent", "fine"]
    uses: tuple[AttributeUseContext, ...]


@dataclass(frozen=True, slots=True)
class AttributeInputProgram:
    operation: AttributeInputsValues
    left_schema: DataSchema
    right_schema: DataSchema
    parent_schema: DataSchema
    matrix: tuple[AttributeMatrixColumn, ...]
    output_schema: DataSchema
    reads: tuple[AttributeReadBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class AttributeContributionProgram:
    operation: AttributeContributionValues
    input_program: AttributeInputProgram
    originals: tuple[FormulaProgram, FormulaProgram]
    output_schema: DataSchema
    exact_integer_closure: bool = False


@dataclass(frozen=True, slots=True)
class AttributeMethodPayload:
    """Independent inert producer data; final result identity belongs to completion."""

    output_schema: DataSchema
    parent_axis_count: int
    parents: tuple[FrameMethodParent, ...]
    coordinates: tuple[FrameMethodCoordinate, ...]
    targets: tuple[FrameMethodTarget, ...] = ()
    parent_schema: DataSchema | None = None


@dataclass(frozen=True, slots=True)
class AttributeMethodProduction:
    """Invocation-only association, outside the inert payload and final facts."""

    stage_id: str
    input_id: str
    explicit_index: int
    is_final: bool
    payload: AttributeMethodPayload


@dataclass(frozen=True, slots=True)
class AttributeProjectProgram:
    operation: AttributeProjectValues
    input_schema: DataSchema
    output_schema: DataSchema


@overload
def attribute_schema(
    left: DataSchema, right: DataSchema, parent: DataSchema, operation: AttributeFrameValues
) -> DataSchema: ...


@overload
def attribute_schema(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    parent: DataSchema | PendingDataSchema,
    operation: AttributeFrameValues,
) -> DataSchema | PendingDataSchema: ...


def attribute_schema(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    parent: DataSchema | PendingDataSchema,
    operation: AttributeFrameValues,
) -> DataSchema | PendingDataSchema:
    if not left.values or set(left.value_paths) != set(right.value_paths):
        reject(
            "equal nonempty metric names",
            repr((left.value_paths, right.value_paths)),
            "Provide the same named Metrics on both endpoints.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    fine = tuple(axis for i, axis in enumerate(left.axes) if i not in operation.left_parents)
    right_fine = tuple(
        axis for i, axis in enumerate(right.axes) if i not in operation.right_parents
    )
    if len(fine) != len(right_fine):
        reject(
            "the same ordered fine classification",
            repr((fine, right_fine)),
            "Use one shared by declaration.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    for left_axis, right_axis in zip(fine, right_fine, strict=True):
        if axis_role(left_axis) != axis_role(right_axis) or len(left_axis.columns) != len(
            right_axis.columns
        ):
            reject(
                "matching complete fine-axis roles",
                repr((left_axis, right_axis)),
                "Provide compatible shared classifications.",
                "frame.attribute",
                canonical_id="analysis_frame.attribute",
            )
        for x, y in zip(left_axis.columns, right_axis.columns, strict=True):
            if isinstance(x, DataColumn) and isinstance(y, DataColumn) and x.dtype != y.dtype:
                reject(
                    "equal actual fine-key physical types",
                    repr((x.dtype, y.dtype)),
                    "Use compatible classifications on both endpoints.",
                    "frame.attribute",
                    canonical_id="analysis_frame.attribute",
                )
    pending = any(isinstance(schema, PendingDataSchema) for schema in (left, right, parent))
    axes: list[PendingFrameAxis | DataAxis] = []
    offset = 0
    used_tokens = {axis.token for axis in parent.axes}
    used_slots = {column.slot_id for axis in parent.axes for column in axis.columns}

    def unique(base: str, used: set[str]) -> str:
        value, suffix = base, 0
        while value in used:
            suffix += 1
            value = f"{base}.{suffix}"
        used.add(value)
        return value

    for index, axis in enumerate((*parent.axes, *fine)):
        # Parent calendar metadata and attachment names stay exact; new fine
        # axes get unique data tokens and slots after the preserved parent keys.
        if index < len(parent.axes):
            axes.append(axis)
            offset += len(axis.columns)
            continue
        columns = tuple(
            replace(c, slot_id=unique(f"attribute.key.{offset + j}", used_slots))
            for j, c in enumerate(axis.columns)
        )
        token = unique(f"attribute.axis.{index}", used_tokens)
        axes.append(replace(axis, token=token, columns=columns))
        offset += len(columns)
    values: list[DataColumn | PendingDataColumn] = []
    paths: list[tuple[str, ...]] = []
    left_values: tuple[DataColumn | PendingDataColumn, ...] = left.values
    right_values: tuple[DataColumn | PendingDataColumn, ...] = right.values
    for path, a in zip(left.value_paths, left_values, strict=True):
        b = right_values[right.value_paths.index(path)]
        if a.unit != b.unit or any(
            column_kind(c) not in ("numeric", "duration", "unresolved") for c in (a, b)
        ):
            reject(
                "comparable numeric Metrics with equal units",
                repr((a, b)),
                "Provide compatible named endpoint Metrics.",
                "frame.attribute",
                canonical_id="analysis_frame.attribute",
            )
        dtype = (
            _compare_type(a.dtype, b.dtype)
            if isinstance(a, DataColumn) and isinstance(b, DataColumn)
            else None
        )
        for side, original in (("left", a), ("right", b), ("absolute", a)):
            slot = f"attribute.value.{len(values)}"
            paths.append((side, *path))
            if pending or dtype is None:
                values.append(
                    PendingDataColumn(
                        slot, ".".join((side, *path)), column_kind(original), True, original.unit
                    )
                )
            else:
                assert isinstance(original, DataColumn)
                values.append(
                    replace(
                        original,
                        slot_id=slot,
                        name=".".join((side, *path)),
                        nullable=True,
                        dtype=dtype if side == "absolute" else original.dtype,
                    )
                )
    presence = tuple(
        DataColumn(f"attribute.presence.{i}", name, ScalarDataType("boolean"), False)
        for i, name in enumerate(("left_present", "right_present"))
    )
    presence_paths = (("left_present",), ("right_present",))
    if pending:
        pending_axes: list[PendingFrameAxis] = []
        for axis in axes:
            if isinstance(axis, DataAxis) and type(axis) is DataAxis:
                pending_axes.append(
                    PendingDataAxis(
                        axis.token,
                        axis.name,
                        "identity" if axis.kind == "member" else "field",
                        tuple(_pending_column(c) for c in axis.columns),
                        at=axis.at,
                    )
                )
            else:
                assert isinstance(
                    axis, (PendingDataAxis, CalendarDataAxis, IntervalDataAxis, PairedDataAxis)
                )
                pending_axes.append(axis)
        return PendingDataSchema(
            tuple(pending_axes),
            tuple(_pending_column(c) for c in values),
            tuple(paths),
            tuple(_pending_column(c) for c in presence),
            presence_paths,
        )
    assert all(isinstance(a, DataAxis) for a in axes)
    assert all(isinstance(v, DataColumn) for v in values)
    return DataSchema(
        tuple(a for a in axes if isinstance(a, DataAxis)),
        tuple(v for v in values if isinstance(v, DataColumn)),
        presence,
        value_paths=tuple(paths),
        presence_paths=presence_paths,
    )


def pending_contribution_schema(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    parent: DataSchema | PendingDataSchema,
    operation: AttributeContributionValues,
) -> DataSchema | PendingDataSchema:
    """Describe an ordinary pending result without preparing source schemas."""
    if operation.target is not None:
        legacy = pending_contribution_schema(left, right, parent, replace(operation, target=None))
        return report_schema(legacy, parent, operation)
    base = attribute_schema(left, right, parent, operation.mapping)
    values: list[DataColumn | PendingDataColumn] = []
    paths: list[tuple[str, ...]] = []
    base_values: tuple[DataColumn | PendingDataColumn, ...] = base.values
    for index, (name, rule) in enumerate(operation.rules[0]):
        originals = base_values[index * 3 : index * 3 + 3]
        for prefix, original in zip(("left", "right", "absolute"), originals, strict=True):
            paths.append((prefix, name))
            values.append(replace(original, slot_id=f"attribute.result.{len(values)}"))
        for prefix in ("contribution", "contribution_share"):
            original = originals[2]
            paths.append((prefix, name))
            generated = replace(
                original,
                slot_id=f"attribute.result.{len(values)}",
                name=f"{prefix}.{name}",
                unit=(None if original.unit is None else "1")
                if prefix == "contribution_share"
                else original.unit,
            )
            if isinstance(generated, DataColumn) and prefix == "contribution_share":
                generated = replace(generated, dtype=_type("div", generated, generated))
            values.append(generated)
        if isinstance(rule, RatioComponentRule):
            for prefix in (
                "numerator_left",
                "numerator_right",
                "denominator_left",
                "denominator_right",
            ):
                component_rule = operation.rules[0 if prefix.endswith("left") else 1][index][1]
                assert isinstance(component_rule, RatioComponentRule)
                component_units = ratio_component_units(component_rule)
                paths.append((prefix, name))
                values.append(
                    replace(
                        originals[0],
                        slot_id=f"attribute.result.{len(values)}",
                        name=f"{prefix}.{name}",
                        unit=component_units[0 if prefix.startswith("numerator") else 1],
                    )
                )
        if isinstance(rule.operation, NativeWeightedMean):
            for side in ("left", "right"):
                prefix = "valid_count_" + side
                original = originals[0]
                counted = replace(
                    original,
                    slot_id=f"attribute.result.{len(values)}",
                    name=prefix + "." + name,
                    unit="1",
                )
                if isinstance(counted, DataColumn):
                    counted = replace(counted, dtype=ScalarDataType("int64"))
                values.append(counted)
                paths.append((prefix, name))
    if isinstance(base, PendingDataSchema):
        assert all(isinstance(c, PendingDataColumn) for c in values)
        return replace(
            base,
            values=tuple(c for c in values if isinstance(c, PendingDataColumn)),
            value_paths=tuple(paths),
        )
    assert all(isinstance(c, DataColumn) for c in values)
    return replace(
        base, values=tuple(c for c in values if isinstance(c, DataColumn)), value_paths=tuple(paths)
    )


def contribution_schema(
    program: AttributeInputProgram, operation: AttributeContributionValues
) -> DataSchema:
    if operation.target is not None:
        legacy = contribution_schema(program, replace(operation, target=None))

        def parent_original(side: Literal["left", "right"], rule: AttributeRule) -> DataColumn:
            index = (
                operation.source_indices[0 if side == "left" else 1]
                if operation.source_indices is not None
                else 0
            )
            column = _rule_column(program, rule, side)
            native = next(
                (
                    item.target
                    for item in program.matrix
                    if item.side == side
                    and item.scope == "parent"
                    and item.role == "native_value"
                    and item.occurrence == (index,)
                ),
                None,
            )
            return column if native is None else replace(column, dtype=native.dtype)

        left_rule, right_rule = operation.rules[0][0][1], operation.rules[1][0][1]
        fresh = parent_original("left", left_rule)
        target = operation.target
        parent_diagnostics: list[tuple[tuple[str, ...], DataColumn]] = [
            (("detail", "value" if target.mode == "level" else "current", *target.path), fresh)
        ]
        if operation.target.mode != "level":
            baseline = parent_original("right", right_rule)
            fresh = replace(fresh, dtype=_type("sub", fresh, baseline))
            parent_diagnostics.extend(
                (
                    (("detail", "baseline", *target.path), baseline),
                    (("detail", "absolute", *target.path), fresh),
                )
            )
        for side, role, rule in (
            (("left", "value", left_rule),)
            if target.mode == "level"
            else (("left", "current", left_rule), ("right", "baseline", right_rule))
        ):
            if isinstance(rule, RatioComponentRule):
                numerator, denominator = _component_columns(program, rule, side)
                parent_diagnostics.extend(
                    (
                        (("component", role, "numerator", *target.path), numerator),
                        (("component", role, "denominator", *target.path), denominator),
                    )
                )
        result = report_schema(
            legacy,
            program.parent_schema,
            operation,
            fresh=fresh,
            parent_diagnostics=tuple(parent_diagnostics),
        )
        assert isinstance(result, DataSchema)
        return result
    result = pending_contribution_schema(
        program.left_schema, program.right_schema, program.parent_schema, operation
    )
    assert isinstance(result, DataSchema)
    # Component columns retain their real compiled role carrier, rather than a
    # completed mean's output dtype. Recursive components resolve below.
    values = list(result.values)
    for index, ((name, left_rule), (_, right_rule)) in enumerate(
        zip(*operation.rules, strict=True)
    ):
        del index
        if not isinstance(left_rule, RatioComponentRule) or not isinstance(
            right_rule, RatioComponentRule
        ):
            continue
        for side, rule in (
            (("left", left_rule),)
            if operation.single_endpoint
            else (("left", left_rule), ("right", right_rule))
        ):
            n, d = _component_columns(program, rule, side, scope="fine")
            for prefix, column in (("numerator_" + side, n), ("denominator_" + side, d)):
                position = result.value_paths.index((prefix, name))
                values[position] = replace(
                    column, slot_id=values[position].slot_id, name=prefix + "." + name
                )
        if isinstance(left_rule.operation, NativeWeightedMean):
            for side, rule in (
                (("left", left_rule),)
                if operation.single_endpoint
                else (("left", left_rule), ("right", right_rule))
            ):
                assert isinstance(rule.operation, NativeWeightedMean)
                source = rule.operation.source
                component = next(
                    m.target
                    for m in program.matrix
                    if m.side == side
                    and m.scope == "fine"
                    and m.occurrence == _logical(source)
                    and m.role == "pair_count"
                )
                position = result.value_paths.index(("valid_count_" + side, name))
                values[position] = replace(
                    component,
                    slot_id=values[position].slot_id,
                    name="valid_count_" + side + "." + name,
                    unit="1",
                )
    return replace(result, values=tuple(values))


def _coefficient_column(value: int | float | Decimal) -> DataColumn:
    """Describe the captured scalar losslessly, separately from result typing."""
    if isinstance(value, Decimal):
        exponent = value.as_tuple().exponent
        assert isinstance(exponent, int)
        scale = max(0, -exponent)
        precision = max(1, max(0, value.adjusted() + 1) + scale)
        dtype: DataType = DecimalDataType(precision, scale)
    else:
        import ibis

        from marivo.analysis.core.value_types import data_type

        dtype = data_type(ibis.literal(value).type())
    return DataColumn("scale", "scale", dtype, False, "1")


def _type(operation: Literal["add", "sub", "mul", "div"], a: DataColumn, b: DataColumn) -> DataType:
    schema = DataSchema(
        (),
        (replace(a, slot_id="a", name="a"), replace(b, slot_id="b", name="b")),
        (),
        value_paths=(("a",), ("b",)),
    )
    return expression_type(schema, ValueBinary(operation, ValueSlot(0), ValueSlot(1)))


def _logical(source: AttributeSourceRequest) -> tuple[int, ...]:
    return source.binding.logical_occurrence or source.binding.occurrence


def _component_columns(
    program: AttributeInputProgram,
    rule: RatioComponentRule,
    side: str,
    *,
    scope: Literal["parent", "fine"] = "parent",
) -> tuple[DataColumn, DataColumn]:
    operation = rule.operation
    if isinstance(operation, DirectRatio):
        return (
            _rule_column(program, operation.numerator, side, scope=scope),
            _rule_column(program, operation.denominator, side, scope=scope),
        )
    source = operation.source
    names = (
        ("valid_value_sum", "valid_value_count")
        if isinstance(operation, NativeMean)
        else ("pair_product_sum", "pair_weight_sum")
    )
    columns = tuple(
        next(
            m.target
            for m in program.matrix
            if m.side == side
            and m.scope == scope
            and m.occurrence == _logical(source)
            and m.role == role
        )
        for role in names
    )
    return columns[0], columns[1]


def _rule_column(
    program: AttributeInputProgram,
    rule: AttributeRule,
    side: str,
    *,
    scope: Literal["parent", "fine"] = "parent",
) -> DataColumn:
    operation = rule.operation
    if isinstance(operation, AdditiveSource):
        column = next(
            m.target
            for m in program.matrix
            if m.side == side
            and m.scope == scope
            and m.occurrence == _logical(operation.source)
            and m.role == "additive_value"
        )
        original = next(
            item.target
            for item in program.matrix
            if item.side == side
            and item.scope == scope
            and item.role == "value"
            and any(
                source.use.logical_occurrence == _logical(operation.source)
                for source in item.sources
            )
        )
        # Retained additive mass uses integer ticks; the final rule quantity
        # keeps Duration through fixed linear arithmetic and report inference.
        return (
            replace(column, dtype=original.dtype)
            if isinstance(original.dtype, DurationDataType)
            else column
        )
    if isinstance(rule, RatioComponentRule):
        if isinstance(operation, (NativeMean, NativeWeightedMean)):
            return next(
                item.target
                for item in program.matrix
                if item.side == side
                and item.scope == scope
                and item.role == "value"
                and any(
                    source.use.logical_occurrence == _logical(operation.source)
                    for source in item.sources
                )
            )
        n, d = _component_columns(program, rule, side, scope=scope)
        return replace(n, dtype=_type("div", n, d), unit=rule_unit(rule))
    if isinstance(operation, (RuleAdd, RuleSubtract)):
        a, b = (
            _rule_column(program, operation.left, side, scope=scope),
            _rule_column(program, operation.right, side, scope=scope),
        )
        return replace(a, dtype=_type("add" if isinstance(operation, RuleAdd) else "sub", a, b))
    if isinstance(operation, (RuleDivide, RuleRescale)):
        a = _rule_column(program, operation.operand, side, scope=scope)
        return replace(
            a,
            dtype=_type("div", a, _coefficient_column(operation.scalar.value)),
            unit=operation.unit if isinstance(operation, RuleRescale) else a.unit,
        )
    if isinstance(operation, RuleNegate):
        return _rule_column(program, operation.operand, side, scope=scope)
    assert isinstance(operation, RuleScale)
    a = _rule_column(program, operation.operand, side, scope=scope)
    return replace(a, dtype=_type("mul", a, _coefficient_column(operation.scalar.value)))


def _common_report_type(columns: tuple[DataColumn, ...]) -> DataType:
    """Choose a common carrier without reducing scale or converting exact values to float."""
    dtypes = tuple(column.dtype for column in columns)
    if all(dtype == dtypes[0] for dtype in dtypes):
        return dtypes[0]
    if all(
        isinstance(dtype, DecimalDataType) or dtype == ScalarDataType("int64") for dtype in dtypes
    ):
        decimals = tuple(
            dtype if isinstance(dtype, DecimalDataType) else DecimalDataType(19, 0)
            for dtype in dtypes
        )
        scale = max(dtype.scale for dtype in decimals)
        precision = min(38, max(dtype.precision - dtype.scale for dtype in decimals) + scale)
        return DecimalDataType(precision, scale)
    reject(
        "losslessly compatible Attribute report carriers",
        repr(dtypes),
        "Use source methods whose historical, parent and fine values retain a compatible exact numeric or Duration carrier.",
        "frame.attribute",
        canonical_id="analysis_frame.attribute",
    )


def report_schema(
    legacy: DataSchema | PendingDataSchema,
    parent: DataSchema | PendingDataSchema,
    operation: AttributeContributionValues,
    *,
    fresh: DataColumn | None = None,
    parent_diagnostics: tuple[tuple[tuple[str, ...], DataColumn], ...] = (),
) -> DataSchema | PendingDataSchema:
    """Map one closed target to its report directory without parsing display names."""
    target = operation.target
    assert target is not None
    directory: dict[tuple[str, ...], DataColumn | PendingDataColumn] = dict(
        zip(legacy.value_paths, legacy.values, strict=True)
    )
    path = target.path
    level = target.mode == "level"
    source = directory[("left" if level else "contribution", "target")]
    values: list[DataColumn | PendingDataColumn] = []
    paths: list[tuple[str, ...]] = []
    entries: list[tuple[tuple[str, ...], DataColumn | PendingDataColumn]] = [
        (("contribution", *path), source),
        (("total", *path), source),
        (("observed_total", *path), parent.values[target.observed]),
        (("contribution_share", *path), directory[("contribution_share", "target")]),
    ]
    for prefix, role in (
        ((("detail", "value"), "left"),)
        if level
        else (
            (("detail", "current"), "left"),
            (("detail", "baseline"), "right"),
            (("detail", "absolute"), "absolute"),
        )
    ):
        entries.append(((*prefix, *path), directory[(role, "target")]))
    for side, role in (
        (("left", "value"),) if level else (("left", "current"), ("right", "baseline"))
    ):
        for component in ("numerator", "denominator", "valid_count"):
            column = directory.get((component + "_" + side, "target"))
            if column is not None:
                entries.append((("component", role, component, *path), column))
    common: DataType | None = None
    observed = parent.values[target.observed]
    if (
        isinstance(legacy, DataSchema)
        and isinstance(source, DataColumn)
        and isinstance(observed, DataColumn)
    ):
        actual = source
        if target.mode == "relative":
            actual = replace(actual, dtype=_type("div", actual, actual))
            if fresh is not None:
                fresh = replace(fresh, dtype=_type("div", fresh, fresh))
        common = _common_report_type((actual, observed, *((fresh,) if fresh is not None else ())))
    parent_directory = dict(parent_diagnostics)
    for output_path, column in entries:
        if isinstance(legacy, PendingDataSchema) and isinstance(column, DataColumn):
            column = _pending_column(column)
        if target.mode == "relative" and output_path[0] in ("contribution", "total"):
            original = column
            column = replace(column, unit=parent.values[target.observed].unit)
            if isinstance(column, DataColumn) and isinstance(original, DataColumn):
                column = replace(column, dtype=_type("div", original, original))
        if (
            common is not None
            and isinstance(column, DataColumn)
            and output_path[0] in ("contribution", "total", "observed_total")
        ):
            column = replace(column, dtype=common)
        parent_column = parent_directory.get(output_path)
        if isinstance(column, DataColumn) and parent_column is not None:
            column = replace(column, dtype=_common_report_type((column, parent_column)))
        if target.mode == "relative" and output_path[0] == "contribution_share":
            column = replace(
                column, unit=None if parent.values[target.observed].unit is None else "1"
            )
        column = replace(
            column, slot_id=f"attribute.report.{len(values)}", name=".".join(output_path)
        )
        values.append(column)
        paths.append(output_path)
    presence: tuple[DataColumn | PendingDataColumn, ...] = (
        legacy.presence[:1] if level else legacy.presence[:2]
    )
    presence_paths = tuple(
        ("detail", role, "present", *path)
        for role in (("value",) if level else ("current", "baseline"))
    )
    presence = tuple(
        replace(c, slot_id=f"attribute.report.present.{i}", name=".".join(p))
        for i, (c, p) in enumerate(zip(presence, presence_paths, strict=True))
    )
    if isinstance(legacy, PendingDataSchema):
        return replace(
            legacy,
            values=tuple(c for c in values if isinstance(c, PendingDataColumn)),
            value_paths=tuple(paths),
            presence=tuple(c for c in presence if isinstance(c, PendingDataColumn)),
            presence_paths=presence_paths,
        )
    return replace(
        legacy,
        values=tuple(c for c in values if isinstance(c, DataColumn)),
        value_paths=tuple(paths),
        presence=tuple(c for c in presence if isinstance(c, DataColumn)),
        presence_paths=presence_paths,
    )


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
