"""Closed data expressions and pure current-structure inference."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal, overload

from marivo.analysis.core.frame_alignment_axes import align_axes, baseline_binding, paired_axis
from marivo.analysis.core.frame_interpretation import (
    align_facts,
    comparison_facts,
    remap_context,
)
from marivo.analysis.core.frame_operations import (
    AlignFrameValues,
    CompareFrameValues,
    NamedFrameComparison,
)
from marivo.analysis.core.frame_value_analysis import _decimal_column_type
from marivo.analysis.model.data_schema import (
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
from marivo.analysis.model.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_schema import comparison_id
from marivo.analysis.model.frame_structure import (
    PendingDataAxis,
    PendingDataColumn,
    PendingDataSchema,
    PendingFrameAxis,
    axis_role,
    column_kind,
    pending_column,
)
from marivo.analysis.model.layout_facts import (
    FrameFixedGroup,
    FrameGroup,
    FramePairStatisticContext,
    FrameShareContext,
)
from marivo.analysis.model.time_facts import merge_source_times
from marivo.analysis.model.value_names import validate_paths


def transport_logical_facts(
    physical: DataSchema, logical: DataSchema | PendingDataSchema | None
) -> DataSchema:
    """Close physical types while retaining snapshots captured at the public derive boundary."""

    if logical is None:
        return physical

    def fixed_group(group: FrameGroup) -> FrameGroup:
        if not isinstance(group, FrameFixedGroup):
            return group
        # Match within this exact output by the captured axis and origins. Key
        # scalars can acquire a different temporal unit as physical types close.
        matches = tuple(
            fixed
            for fixed in physical.fixed_axes
            if replace(fixed.axis, keys=group.fixed.axis.keys) == group.fixed.axis
            and tuple(key.slot_id for key in fixed.axis.keys)
            == tuple(key.slot_id for key in group.fixed.axis.keys)
            and fixed.origins == group.fixed.origins
        )
        assert len(matches) == 1, "a captured fixed group requires one completed selection"
        return replace(group, fixed=matches[0])

    # Completed coordinate domains can invalidate earlier comparison mappings.
    # Physical inference owns those bindings and completed fixed coordinates.
    # Retain operand snapshots because share has already expanded to division,
    # rebinding their fixed groups to the completed selections.
    contexts = dict(physical.value_contexts)
    for name, context in logical.value_contexts:
        if isinstance(context, FrameShareContext):
            contexts[name] = replace(
                context,
                groups=tuple(fixed_group(group) for group in context.groups),
                along=tuple(fixed_group(group) for group in context.along),
            )
        elif isinstance(context, FramePairStatisticContext):
            contexts[name] = replace(
                context, groups=tuple(fixed_group(group) for group in context.groups)
            )
    return replace(
        physical,
        value_contexts=tuple(contexts.items()),
        value_time_semantics=physical.value_time_semantics or logical.value_time_semantics,
        source_times=merge_source_times(physical.source_times, logical.source_times),
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
        return _decimal_column_type(left, right)
    reject(
        "compatible numeric types or matching exact Duration units",
        repr((left, right)),
        "Select compatible numeric values before compare.",
        "frame.compare",
    )


@overload
def _infer_frame_combine_schema(
    left: DataSchema, right: DataSchema, operation: AlignFrameValues
) -> DataSchema: ...
@overload
def _infer_frame_combine_schema(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    operation: AlignFrameValues,
) -> DataSchema | PendingDataSchema: ...


def _infer_frame_combine_schema(
    left: DataSchema | PendingDataSchema,
    right: DataSchema | PendingDataSchema,
    operation: AlignFrameValues,
) -> DataSchema | PendingDataSchema:
    """Derive one complete binary layout from captured current structures only."""
    aligned = align_axes(left, right, operation)
    left_axes, axis_columns = aligned.axes, aligned.columns
    matched, partners = aligned.matched, aligned.partners
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
                pending_axes.append(paired_axis(left, right, i, partners[i]))
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
                        tuple(pending_column(c) for c in axis_columns[i]),
                        axis.at,
                    )
                )
        return PendingDataSchema(
            tuple(pending_axes),
            tuple(pending_column(c) for c in values),
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
            concrete_axes.append(paired_axis(left, right, i, partners[i]))
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


def _comparison_value_types(
    current: DataSchema | PendingDataSchema, baseline: DataSchema | PendingDataSchema
) -> tuple[DataType | None, ...]:
    current_values: tuple[DataColumn | PendingDataColumn, ...] = current.values
    baseline_values: tuple[DataColumn | PendingDataColumn, ...] = baseline.values
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
    return tuple(types)


@overload
def _infer_frame_compare_schema(
    inputs: tuple[DataSchema, ...], operation: CompareFrameValues
) -> DataSchema: ...
@overload
def _infer_frame_compare_schema(
    inputs: tuple[DataSchema | PendingDataSchema, ...], operation: CompareFrameValues
) -> DataSchema | PendingDataSchema: ...


def _infer_frame_compare_schema(
    inputs: tuple[DataSchema | PendingDataSchema, ...], operation: CompareFrameValues
) -> DataSchema | PendingDataSchema:
    """Infer one current layout and all named baseline value groups together."""
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
        binding = baseline_binding(current, baseline, item)
        types = _comparison_value_types(current, baseline)
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
    return _comparison_schema(
        axes, values, paths, presence_paths, facts, key_nullability, pending=pending
    )


def _comparison_schema(
    axes: tuple[DataAxis | PendingDataAxis, ...],
    values: list[DataColumn | PendingDataColumn],
    paths: tuple[tuple[str, ...], ...],
    presence_paths: tuple[tuple[str, ...], ...],
    facts: list[DataComparison],
    key_nullability: dict[str, bool | None],
    *,
    pending: bool,
) -> DataSchema | PendingDataSchema:
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
                            replace(pending_column(c), nullable=key_nullability[c.slot_id])
                            for c in axis.columns
                        ),
                        axis.at,
                    )
                )
        return PendingDataSchema(
            tuple(pending_axes),
            tuple(pending_column(c) for c in values),
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

    context_tokens: dict[str, tuple[str, Literal["self", "left", "right"]]] = {
        schema.axes[a].token: (output_axes[b].token, "self") for a, b in axis_pairs
    }
    context_ids = {
        comparison_id(f): ".".join((namespace, comparison_id(f))) for f in schema.comparisons
    }
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
                endpoint(fact.current_presence) if fact.current_presence is not None else None,
                endpoint(fact.baseline_presence) if fact.baseline_presence is not None else None,
                tuple(
                    dict.fromkeys(
                        (*tuple(endpoint(record) for record in fact.enclosing_presence), wrapper)
                    )
                ),
                scoped,
                tuple(
                    (
                        path,
                        remap_context(a, context_tokens, context_ids),
                        remap_context(b, context_tokens, context_ids),
                    )
                    for path, a, b in fact.contexts
                ),
                fact.current_fixed_axes,
                fact.baseline_fixed_axes,
                fact.named,
            )
        )
    return tuple(result)


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

    return align_facts(left, right, _infer_frame_combine_schema(left, right, operation), operation)


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

    return comparison_facts(inputs, _infer_frame_compare_schema(inputs, operation), operation)
