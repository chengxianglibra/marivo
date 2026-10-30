"""Current Query sample combination over completed source contributions."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, NoReturn

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.graph_plan import LocalCompute
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.core.calendar_keys import time_key_series
from marivo.analysis.core.data_types import DataColumn, DecimalDataType, ScalarDataType
from marivo.analysis.core.unique_keys import UniqueKeys

if TYPE_CHECKING:
    from marivo.analysis.core.attribute import AttributeFormulaContext


def _attribute_formula_failure(
    context: AttributeFormulaContext,
    metric: str,
    path: tuple[int, ...],
    coordinates: pd.DataFrame,
    expected: str,
    received: str,
) -> NoReturn:
    """Annotate the existing numeric failure from its actual check coordinates."""
    from marivo.analysis.core.attribute import (
        AttributeFailureContext,
        AttributeFineFiber,
        AttributeNumericWitness,
        AttributeParentFiber,
    )
    from marivo.analysis.core.frozen_calendar import frozen_key_rows
    from marivo.semantic._numeric_lowering import numeric_failure
    from marivo.semantic.errors import SemanticExpressionError

    source_parent = frozen_key_rows(coordinates, context.source_parent_columns)[0]
    parent = dict(context.parent_mapping)[source_parent]
    fiber: AttributeParentFiber | AttributeFineFiber = (
        AttributeFineFiber(
            parent,
            context.parent_columns,
            frozen_key_rows(coordinates, context.fine_columns)[0],
            context.fine_columns,
        )
        if context.fine_columns
        else AttributeParentFiber(parent, context.parent_columns)
    )
    captured = AttributeFailureContext(
        tuple(use for use in context.uses if use.metric == metric),
        fiber,
        AttributeNumericWitness(path, "original", ()),
    )
    try:
        numeric_failure(expected, received)
    except SemanticExpressionError as error:
        error.details["attribute_context"] = captured
        raise


def query_combine(
    stage: LocalCompute,
    inputs: tuple[pd.DataFrame, ...],
    *,
    attribute: AttributeFormulaContext | None = None,
    unique_keys: tuple[UniqueKeys, ...] = (),
) -> pd.DataFrame:
    """Combine completed Metric samples and check only demanded numeric values."""
    from marivo.semantic._numeric_lowering import numeric_failure

    program = stage.program
    assert isinstance(program, FormulaProgram)
    frame, checks = _combine_program(
        program,
        inputs,
        stage.keys,
        stage.columns[len(stage.keys) :],
        attribute=attribute,
        unique_keys=unique_keys,
    )
    for name, expected, received in checks:
        violation = frame[name].astype("boolean").fillna(False)
        if violation.any():
            coordinates = frame.loc[violation, list(stage.keys)].head(6).to_dict(orient="records")
            numeric_failure(expected, f"{received}; coordinates={coordinates}")
    return frame.loc[:, [column.slot_id for column in stage.columns]]


def _combine_program(
    program: FormulaProgram,
    inputs: tuple[pd.DataFrame, ...],
    key_slots: tuple[str, ...],
    outputs: tuple[DataColumn, ...],
    forced_domain: pd.DataFrame | None = None,
    attribute: AttributeFormulaContext | None = None,
    unique_keys: tuple[UniqueKeys, ...] = (),
) -> tuple[pd.DataFrame, tuple[tuple[str, str, str], ...]]:
    """Keep sample checks as data until the enclosing formula demands them."""

    import ibis

    from marivo._definition.correspondence import coordinate_position
    from marivo._definition.expressions import (
        DeclaredMetricRef,
        EntityCount,
        MemberCount,
        MetricNode,
        PerReduce,
        PerWeightedMean,
        SourceAggregate,
        SourceWeightedMean,
    )
    from marivo._definition.graph import children, walk
    from marivo._definition.relations import (
        PopulationDependency,
        PopulationProgram,
        sample_population,
    )
    from marivo._definition.types import AxisBinding, CalendarCoordinate, IntervalCoordinate
    from marivo._temporal import Grain
    from marivo.analysis.compiler.observation import (
        FormulaInput,
        MemberInput,
        duration_type,
        formula_columns,
    )
    from marivo.analysis.core.data_types import DurationDataType
    from marivo.analysis.core.data_validation import stage_arrow_type
    from marivo.analysis.core.value_types import data_type, ibis_type
    from marivo.analysis.methods._ibis_numeric import evaluate_numeric
    from marivo.analysis.methods._sample_numeric import reduce_samples
    from marivo.semantic._numeric_lowering import (
        NumericCheck,
        NumericValue,
        lower_metric_formula,
        numeric_failure,
    )

    keys = list(key_slots)
    population_frames = inputs[len(program.values) :]
    inputs = inputs[: len(program.values)]
    grouped: dict[tuple[int, ...], tuple[FormulaInput, list[pd.DataFrame]]] = {}
    grouped_keys: dict[tuple[int, ...], list[UniqueKeys]] = {}
    for index, (binding, frame) in enumerate(zip(program.values, inputs, strict=True)):
        grouped_keys.setdefault(binding.occurrence, []).append(
            unique_keys[index] if unique_keys else UniqueKeys()
        )
        if binding.presence is not None:
            frame = frame.copy()
            frame["__numeric_present"] = frame[binding.presence]
        if binding.occurrence in grouped:
            original, frames = grouped[binding.occurrence]
            frames.append(frame.rename(columns={binding.column.slot_id: original.column.slot_id}))
        else:
            grouped[binding.occurrence] = (binding, [frame])
    bindings = [item[0] for item in grouped.values()]
    frames = [
        pd.concat(item[1], ignore_index=True) if len(item[1]) > 1 else item[1][0]
        for item in grouped.values()
    ]
    frame_keys = [facts[0] if len(facts) == 1 else UniqueKeys() for facts in grouped_keys.values()]
    program = replace(program, values=tuple(bindings))
    inputs = tuple(frames)
    if keys:
        domain = (
            pd.concat(
                [
                    frame.loc[
                        frame[
                            f"__sample_scope_{len(keys)}"
                            if f"__sample_scope_{len(keys)}" in frame.columns
                            else "__sample_present"
                            if "__sample_present" in frame.columns
                            else "__numeric_present"
                        ]
                        .astype("boolean")
                        .fillna(False),
                        keys,
                    ]
                    if "__numeric_present" in frame.columns
                    or "__sample_present" in frame.columns
                    or f"__sample_scope_{len(keys)}" in frame.columns
                    else frame.loc[:, keys]
                    for frame in inputs
                ],
                ignore_index=True,
            ).drop_duplicates()
            if inputs
            else pd.DataFrame(columns=keys)
        )
        if (
            program.calendar is not None
            and program.output_population is None
            and program.complete_calendar
        ):
            buckets = pd.DataFrame({program.calendar_key: time_key_series(program.calendar)})
            field_keys = [key for key in keys if key != program.calendar_key]
            domain = (
                domain.loc[:, field_keys].drop_duplicates().merge(buckets, how="cross")
                if field_keys
                else buckets
            )
            domain = domain.loc[:, keys]
        domain = domain.reset_index(drop=True)
    else:
        domain = pd.DataFrame(index=pd.RangeIndex(1))
        if program.output_population is not None and inputs:
            has_required_coordinate = any(
                bool(frame["__sample_scope_0"].astype("boolean").fillna(False).any())
                if "__sample_scope_0" in frame.columns
                else bool(frame["__sample_present"].astype("boolean").fillna(False).any())
                if "__sample_present" in frame.columns
                else bool(len(frame))
                for frame in inputs
            )
            if not has_required_coordinate:
                domain = domain.iloc[:0].copy()
    if forced_domain is not None:
        domain = (
            forced_domain.loc[:, keys].drop_duplicates().reset_index(drop=True)
            if keys
            else pd.DataFrame(index=pd.RangeIndex(len(forced_domain)))
        )

    def population_domain(definition: PopulationDependency, slots: tuple[str, ...]) -> pd.DataFrame:
        from marivo.analysis.dsl.population import population_keys

        if isinstance(definition, PopulationProgram):
            found = next(
                (
                    (binding, frame)
                    for binding, frame in zip(program.populations, population_frames, strict=True)
                    if binding.definition == definition
                ),
                None,
            )
            if found is not None:
                binding, frame = found
                selected = frame.loc[:, list(binding.keys)].copy()
                selected.columns = list(slots)
                return selected
        finite = population_keys(definition)
        if finite is None:
            numeric_failure(
                "a prepared independent Population domain", "missing source domain input"
            )
        records = [
            tuple(
                component
                for axis, value in zip(definition.axes, row, strict=True)
                for component in (
                    value
                    if isinstance(axis, AxisBinding)
                    and axis.role == "identity"
                    and isinstance(value, tuple)
                    else (value,)
                )
            )
            for row in finite
        ]
        return pd.DataFrame(records, columns=slots, dtype=object)

    def parent_scoped(rows: pd.DataFrame) -> pd.DataFrame:
        if program.parent_scope is None:
            return rows
        allowed = population_domain(program.parent_scope, program.parent_slots)
        if not program.parent_slots:
            return rows if len(allowed) else rows.iloc[:0].copy()
        return rows.merge(allowed.drop_duplicates(), on=list(program.parent_slots), how="inner")

    def count_coordinates(count: MemberInput, members: pd.DataFrame) -> pd.DataFrame:
        classified = dict(count.axis_mapping)
        mapping: list[tuple[str, str]] = []
        time_domains: list[tuple[str, CalendarCoordinate | IntervalCoordinate]] = []
        offset = 0
        for axis in program.axes:
            if (
                isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
                and axis not in classified
            ):
                time_domains.append(
                    (
                        key_slots[offset],
                        axis,
                    )
                )
                offset += 1
            else:
                source_keys = count.axis_mapping[coordinate_position(count.population.axes, axis)][
                    1
                ]
                mapping.extend(
                    zip(source_keys, key_slots[offset : offset + len(source_keys)], strict=True)
                )
                offset += len(source_keys)
        selected = members.loc[:, [source for source, _ in mapping]].copy()
        selected.columns = [target for _, target in mapping]
        for slot, time_axis in time_domains:
            selected = selected.merge(pd.DataFrame({slot: time_key_series(time_axis)}), how="cross")
        return selected

    count_data = {}
    for count in program.counts:
        member_data = population_domain(count.population, count.keys)
        count_data[count.definition] = (count, member_data)
        member_coordinates = count_coordinates(count, member_data)
        if keys and forced_domain is None:
            member_coordinates = member_coordinates.loc[:, keys].drop_duplicates()
            if not member_coordinates.empty:
                if domain.empty:
                    domain = member_coordinates.reset_index(drop=True)
                else:
                    domain = (
                        pd.concat([domain, member_coordinates], ignore_index=True)
                        .drop_duplicates()
                        .reset_index(drop=True)
                    )

    domain = parent_scoped(domain)
    if program.output_population is not None:
        complete_domain = parent_scoped(population_domain(program.output_population, key_slots))
        if program.values and not program.population_checked:
            if keys:
                missing = domain.merge(
                    complete_domain.drop_duplicates(), on=keys, how="left", indicator=True
                )
                missing = missing.loc[missing["_merge"] == "left_only", keys]
            else:
                missing = domain if not len(complete_domain) else domain.iloc[:0]
            if len(missing):
                from marivo.analysis.materialization.errors import MaterializationError

                raise MaterializationError(
                    expected="an output Population covering every required complete coordinate",
                    received=f"missing output coordinates={missing.head(6).to_dict(orient='records') if keys else [()]}",
                    repair="Expand by=Population to include the missing complete keys, or explicitly restrict fact contributions before choosing the output domain.",
                    stage="population_coverage",
                    help_target="session.observe",
                )
        domain = complete_domain.drop_duplicates().reset_index(drop=True)
    declarations = dict(program.declared)
    type_values = {
        binding.occurrence: NumericValue(ibis.literal(None).cast(ibis_type(binding.column.dtype)))
        for binding in bindings
    }

    def materialize_samples(node: MetricNode, occurrence: tuple[int, ...]) -> None:
        nonlocal domain
        if isinstance(node, MemberCount):
            if any(binding.occurrence == occurrence for binding in bindings):
                return
            count, member_data = count_data[node.population]
            selected = count_coordinates(count, member_data)
            selected = parent_scoped(selected)
            slot = "members:" + ".".join(str(part) for part in occurrence)
            counted = (
                selected.groupby(keys, dropna=False, sort=True).size().rename(slot).reset_index()
                if keys
                else pd.DataFrame({slot: [len(selected)]})
            )
            bindings.append(
                FormulaInput(
                    occurrence,
                    slot,
                    DataColumn(slot, slot, ScalarDataType("int64"), False),
                    True,
                    keys=key_slots,
                )
            )
            frames.append(counted)
            frame_keys.append(UniqueKeys(tuple(str(c) for c in counted.columns), (key_slots,)))
            return
        if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
            return
        if isinstance(node, DeclaredMetricRef):
            materialize_samples(declarations[node.ref], (*occurrence, 0))
            return
        if isinstance(node, (PerReduce, PerWeightedMean)):
            if any(binding.occurrence == occurrence for binding in bindings):
                return
            calendar_sample = dict(program.calendar_samples).get(occurrence)
            sample_axes: tuple[AxisBinding | CalendarCoordinate | IntervalCoordinate, ...]
            if isinstance(node.samples, Grain):
                assert calendar_sample is not None
                sample_axes = (calendar_sample,)
            else:
                sample_axes = node.samples if isinstance(node.samples, tuple) else node.samples.axes
            source_groups = next(
                (
                    binding.key_groups
                    for binding in bindings
                    if binding.occurrence[: len(occurrence)] == occurrence and binding.key_groups
                ),
                (),
            )
            remaining_groups = tuple(
                group for group in source_groups if not all(key in key_slots for key in group)
            )
            sample_widths = {
                axis: len(group) for count in program.counts for axis, group in count.axis_mapping
            }
            next_sample_slot = len(keys)
            fallback_groups = []
            for axis in sample_axes:
                width = sample_widths.get(axis, 1)
                fallback_groups.append(
                    tuple(
                        f"key.{index}"
                        for index in range(next_sample_slot, next_sample_slot + width)
                    )
                )
                next_sample_slot += width
            sample_groups = (
                remaining_groups[: len(sample_axes)] if remaining_groups else tuple(fallback_groups)
            )
            sample_keys = tuple(key for group in sample_groups for key in group)
            complete_keys = (*key_slots, *sample_keys)
            operands = (node.operand,) if isinstance(node, PerReduce) else (node.value, node.weight)
            selected_bindings: list[FormulaInput] = []
            selected_frames: list[pd.DataFrame] = []
            selected_keys: list[UniqueKeys] = []
            consumed: list[int] = []
            for operand_index, _ in enumerate(operands):
                prefix = (*occurrence, operand_index)
                for index, (binding, frame) in enumerate(zip(bindings, frames, strict=True)):
                    if binding.occurrence[: len(prefix)] == prefix:
                        selected_bindings.append(
                            replace(
                                binding,
                                occurrence=(operand_index, *binding.occurrence[len(prefix) :]),
                            )
                        )
                        selected_frames.append(frame)
                        selected_keys.append(frame_keys[index])
                        consumed.append(index)
            relevant_counts = {
                child.population
                for child in walk(operands, resolve=declarations.get).order
                if isinstance(child, MemberCount)
            }
            child_program = FormulaProgram(
                tuple((f"sample_{i}", operand) for i, operand in enumerate(operands)),
                program.declared,
                tuple(selected_bindings),
                program.calendar,
                program.populations,
                calendar_key=program.calendar_key,
                counts=tuple(
                    count for count in program.counts if count.definition in relevant_counts
                ),
                axes=(*program.axes, *sample_axes),
                parent_scope=program.parent_scope,
                parent_slots=program.parent_slots,
                # Output buckets are complete; internal samples keep their joint keys.
                complete_calendar=False,
                calendar_samples=tuple(
                    ((index, *path[len(prefix) :]), coordinate)
                    for index in range(len(operands))
                    for prefix in ((*occurrence, index),)
                    for path, coordinate in program.calendar_samples
                    if path[: len(prefix)] == prefix
                ),
            )
            complete_domain = None
            if calendar_sample is not None:
                parent_slot, parent_axis = next(
                    (group[0], axis)
                    for group, axis in reversed(
                        tuple(zip(source_groups, program.axes, strict=False))
                    )
                    if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
                )
                parent_cells = (
                    parent_axis.cells
                    if isinstance(parent_axis, CalendarCoordinate)
                    else ((parent_axis.start, parent_axis.end, parent_axis.start, parent_axis.end),)
                )
                pairs = pd.DataFrame(
                    [
                        (cell[0] if isinstance(parent_axis, CalendarCoordinate) else 0, sample[0])
                        for cell in parent_cells
                        for sample in calendar_sample.cells
                        if cell[2] <= sample[2] and sample[3] <= cell[3]
                    ],
                    columns=[parent_slot, sample_keys[0]],
                )
                pairs[parent_slot] = pd.Series(
                    pairs[parent_slot],
                    dtype="timestamp[us, tz=UTC][pyarrow]"
                    if isinstance(parent_axis, CalendarCoordinate)
                    else "int64[pyarrow]",
                )
                pairs[sample_keys[0]] = pd.Series(
                    pairs[sample_keys[0]], dtype="timestamp[us, tz=UTC][pyarrow]"
                )
                complete_domain = domain.merge(pairs, on=parent_slot, how="inner")
            sample_population_definition = sample_population(node.samples)
            if sample_population_definition is not None:
                sample_domain = population_domain(sample_population_definition, sample_keys)
                complete_domain = (
                    domain.merge(sample_domain, how="cross") if keys else sample_domain
                )
            child_outputs = formula_columns(child_program)
            child_frame, child_checks = _combine_program(
                child_program,
                (*selected_frames, *population_frames),
                complete_keys,
                child_outputs,
                complete_domain,
                unique_keys=tuple(selected_keys),
            )
            for child_column in child_outputs:
                if isinstance(child_column.dtype, DurationDataType):
                    child_frame[child_column.slot_id] = pd.Series(
                        pd.arrays.ArrowExtensionArray(
                            pa.array(child_frame[child_column.slot_id]).cast(pa.int64())
                        )
                    )
            order_key = None
            if isinstance(node, PerReduce) and node.order_by is not None:
                order_key = next(
                    key
                    for group, axis in zip(sample_groups, sample_axes, strict=True)
                    for key in group
                    if isinstance(axis, AxisBinding) and axis.field == node.order_by
                )
            if (
                isinstance(node, PerReduce)
                and node.reducer in ("first", "last")
                and order_key is None
            ):
                calendar_keys = [
                    key
                    for group, axis in zip(sample_groups, sample_axes, strict=True)
                    if isinstance(axis, CalendarCoordinate)
                    for key in group
                ]
                if len(calendar_keys) == 1:
                    order_key = calendar_keys[0]
            dtype = duration_type(
                node,
                {},
                values={binding.occurrence: binding.column.dtype for binding in bindings},
                declared=declarations,
                occurrence=occurrence,
            ) or data_type(
                lower_metric_formula(node, occurrence, type_values, declarations).value.type()
            )
            reduced, reduced_checks = reduce_samples(
                node,
                child_frame,
                key_slots,
                domain,
                tuple(column.slot_id for column in child_outputs),
                child_checks,
                order_key=order_key,
                exact_duration=isinstance(dtype, DurationDataType),
                output_type=dtype,
                unique_keys=UniqueKeys(
                    tuple(str(c) for c in child_frame.columns), (complete_keys,)
                ),
            )
            for index in sorted(set(consumed), reverse=True):
                bindings.pop(index)
                frames.pop(index)
                frame_keys.pop(index)
            slot = "sample:" + ".".join(str(part) for part in occurrence)
            reduced = reduced.rename(columns={"value": slot})
            bindings.append(
                FormulaInput(
                    occurrence,
                    slot,
                    DataColumn(slot, slot, dtype, True),
                    False,
                    reduced_checks,
                    key_slots,
                )
            )
            frames.append(reduced)
            frame_keys.append(UniqueKeys(tuple(str(c) for c in reduced.columns), (key_slots,)))
            return
        for edge in children(node):
            if isinstance(edge.child, MetricNode):
                materialize_samples(edge.child, (*occurrence, edge.index))

    for i, (_, node) in enumerate(program.outputs):
        materialize_samples(node, (i,))
    program = replace(program, values=tuple(bindings))
    inputs = tuple(frames)
    values: dict[tuple[int, ...], NumericValue] = {}
    physical: dict[str, pd.Series] = {}
    for input_index, (binding, frame) in enumerate(zip(program.values, inputs, strict=True)):
        slot = binding.column.slot_id
        input_dtype = frame[slot].dtype
        if (
            isinstance(binding.column.dtype, DurationDataType)
            and isinstance(input_dtype, pd.ArrowDtype)
            and pa.types.is_duration(input_dtype.pyarrow_dtype)
        ):
            frame = frame.copy()
            frame[slot] = pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array(frame[slot]).cast(pa.int64()))
            )
        if keys:
            marked = frame.assign(**{"__present": True})
            merged = domain.merge(
                marked,
                on=keys,
                how="left",
                validate=None if frame_keys[input_index].proves(key_slots) else "many_to_one",
                sort=False,
            )
            value = merged[slot]
            if binding.zero_when_absent:
                value = value.mask(
                    merged["__numeric_present"].astype("boolean").fillna(False).eq(False)
                    if "__numeric_present" in merged.columns
                    else merged["__present"].isna(),
                    pd.Series(
                        [Decimal(0) if isinstance(binding.column.dtype, DecimalDataType) else 0]
                        * len(value),
                        index=value.index,
                        dtype=value.dtype,
                    ),
                )
        else:
            value = frame[slot].reset_index(drop=True).iloc[: len(domain)]
        if not isinstance(value.dtype, pd.ArrowDtype):
            # Untyped internal/test inputs still need the captured NULL/type
            # binding. Already typed stages keep their native carrier throughout.
            value = value.astype(object)
        name = f"input_{input_index}"
        check_names = tuple(f"check_{input_index}_{i}" for i in range(len(binding.checks)))
        table = ibis.table(
            {
                name: ibis_type(binding.column.dtype),
                **dict.fromkeys(check_names, "boolean"),
            },
            name=f"_completed_{input_index}",
        )
        physical[name] = value
        checks = []
        aligned = merged if keys else frame.reset_index(drop=True).iloc[: len(domain)]
        for key, (column_name, expected, received) in zip(check_names, binding.checks, strict=True):
            physical[key] = aligned[column_name].astype("boolean").fillna(False).astype(object)
            checks.append(NumericCheck(table[key], expected, received))
        values[binding.occurrence] = NumericValue(table[name], tuple(checks))
    declarations = dict(program.declared)

    result = domain.copy()
    retained: list[tuple[str, str, str]] = []
    for i, (metric_name, node) in enumerate(program.outputs):
        column = outputs[i]
        expression = lower_metric_formula(node, (i,), values, declarations)
        for check in expression.checks:
            violation = (
                evaluate_numeric(
                    check.violation.op(), physical, domain.index, output_name=metric_name
                )
                .astype("boolean")
                .fillna(False)
                .astype(bool)
            )
            check_name = f"formula.check.{len(retained)}"
            result[check_name] = violation
            retained.append((check_name, check.expected, check.received))
            if attribute is not None and violation.any():
                coordinates = domain.loc[violation, keys].head(6)
                _attribute_formula_failure(
                    attribute,
                    metric_name,
                    (i,),
                    coordinates,
                    check.expected,
                    f"{check.received}; coordinates={coordinates.to_dict(orient='records')}",
                )
        series = evaluate_numeric(
            expression.value.op(),
            physical,
            domain.index,
            exact_division=isinstance(column.dtype, DurationDataType),
            output_name=metric_name,
        )
        # Match native Ibis's numeric result type, including integer true division.
        if isinstance(column.dtype, DurationDataType):
            series = pd.Series(
                [round(value) if pd.notna(value) else None for value in series], dtype=object
            )
        if (
            isinstance(column.dtype, ScalarDataType)
            and column.dtype.name == "float64"
            and series.dtype != pd.ArrowDtype(pa.float64())
        ):
            series = pd.Series(
                [None if value is None or value is pd.NA else float(value) for value in series],
                index=series.index,
                dtype=object,
            )
        try:
            result[column.slot_id] = pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(
                        series,
                        type=stage_arrow_type(column.dtype),
                        from_pandas=column.dtype != ScalarDataType("float64"),
                    )
                )
            )
        except (pa.ArrowException, OverflowError, ValueError, TypeError) as error:
            if attribute is not None:
                # Reuse the same exact output conversion to identify its failing
                # coordinate; no numeric expression or source is re-evaluated.
                for position, value in enumerate(series):
                    try:
                        pa.array([value], type=stage_arrow_type(column.dtype), from_pandas=True)
                    except (pa.ArrowException, OverflowError, ValueError, TypeError):
                        _attribute_formula_failure(
                            attribute,
                            metric_name,
                            (i,),
                            domain.iloc[[position]],
                            f"{column.name}: {column.dtype}",
                            f"numeric output conversion: {error}",
                        )
            numeric_failure(f"{column.name}: {column.dtype}", f"numeric output conversion: {error}")
    return result, tuple(retained)
