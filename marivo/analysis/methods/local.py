"""Current Query sample combination over completed source contributions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, NoReturn

import ibis
import pandas as pd
import pyarrow as pa

from marivo._definition.expressions import (
    BuiltinSampleCoordinate,
    DeclaredMetricRef,
    EntityCount,
    FieldCalendarSample,
    MemberCount,
    MetricNode,
    PerReduce,
    PerWeightedMean,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import children, walk
from marivo._definition.observations import SampleAxis, sample_key_matches
from marivo._definition.relations import (
    PopulationDependency,
    sample_population,
)
from marivo._definition.types import AxisBinding, CalendarCoordinate
from marivo._kernel.temporal import Grain
from marivo.analysis.binding.programs import FormulaProgram, MemberInput
from marivo.analysis.compiler.observation import duration_type, formula_columns
from marivo.analysis.compiler.query_stages import LocalCompute
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.frozen_calendar import frozen_key_rows
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.analysis.methods import _sample_numeric
from marivo.analysis.methods._ibis_numeric import evaluate_numeric
from marivo.analysis.methods.local_formula_domains import FormulaDomains, initial_formula_domain
from marivo.analysis.model.attribute_failures import (
    AttributeFailureContext,
    AttributeFineFiber,
    AttributeNumericWitness,
    AttributeParentFiber,
)
from marivo.analysis.model.data_types import (
    DataColumn,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.model.programs import FormulaInput
from marivo.analysis.model.unique_keys import UniqueKeys
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.spi import NumericCheck, NumericValue, lower_metric_formula, numeric_failure

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
    report_domain: pd.DataFrame | None = None,
    sample_domains: Mapping[tuple[int, ...], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Combine completed Metric samples and check only demanded numeric values."""

    program = stage.program
    assert isinstance(program, FormulaProgram)
    frame, checks = _combine_program(
        program,
        inputs,
        stage.keys,
        stage.columns[len(stage.keys) :],
        forced_domain=report_domain
        if sample_domains is None or program.output_population is not None
        else None,
        sample_domains=sample_domains,
        attribute=attribute,
        unique_keys=unique_keys,
        report_candidates=report_domain
        if sample_domains is not None and program.output_population is None
        else None,
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
    sample_domains: Mapping[tuple[int, ...], pd.DataFrame] | None = None,
    report_candidates: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, tuple[tuple[str, str, str], ...]]:
    """Keep sample checks as data until the enclosing formula demands them."""

    formula_inputs = _prepare_formula_inputs(program, inputs, unique_keys)
    program = replace(program, values=tuple(formula_inputs.bindings))
    domains = FormulaDomains(
        program,
        key_slots,
        formula_inputs.population_frames,
        initial_formula_domain(
            program, formula_inputs.frames, key_slots, forced_domain, report_candidates
        ),
    )
    count_data = domains.prepare_report(forced_domain, sample_domains)
    samples = _SampleEvaluation(
        program,
        formula_inputs,
        domains,
        count_data,
        sample_domains,
        {
            binding.occurrence: NumericValue(
                ibis.literal(None).cast(ibis_type(binding.column.dtype))
            )
            for binding in formula_inputs.bindings
        },
    )
    for index, (_, node) in enumerate(program.outputs):
        samples.visit(node, (index,))
    values, physical = _align_formula_inputs(formula_inputs, domains)
    return _evaluate_formula_outputs(program, outputs, values, physical, domains, attribute)


@dataclass(slots=True)
class _FormulaInputs:
    bindings: list[FormulaInput]
    frames: list[pd.DataFrame]
    frame_keys: list[UniqueKeys]
    population_frames: tuple[pd.DataFrame, ...]


def _prepare_formula_inputs(
    program: FormulaProgram,
    inputs: tuple[pd.DataFrame, ...],
    unique_keys: tuple[UniqueKeys, ...],
) -> _FormulaInputs:
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
    return _FormulaInputs(bindings, frames, frame_keys, population_frames)


def _sample_layout(
    program: FormulaProgram,
    bindings: list[FormulaInput],
    key_slots: tuple[str, ...],
    node: PerReduce | PerWeightedMean,
    occurrence: tuple[int, ...],
) -> tuple[tuple[SampleAxis, ...], tuple[tuple[str, ...], ...], tuple[str, ...]]:
    keys = list(key_slots)
    calendar_sample = dict(program.calendar_samples).get(occurrence)
    sample_axes: tuple[SampleAxis, ...]
    if isinstance(node.samples, (Grain, FieldCalendarSample)):
        assert calendar_sample is not None
        sample_axes = (calendar_sample,)
    else:
        sample_axes = node.samples if isinstance(node.samples, tuple) else node.samples.axes
    source_groups = tuple(
        sorted(
            {
                group
                for binding in bindings
                if binding.occurrence[: len(occurrence)] == occurrence
                for group in binding.key_groups
            },
            key=lambda group: int(group[0].split(".")[1]),
        )
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
        width = 1 if isinstance(axis, BuiltinSampleCoordinate) else sample_widths.get(axis, 1)
        fallback_groups.append(
            tuple(f"key.{index}" for index in range(next_sample_slot, next_sample_slot + width))
        )
        next_sample_slot += width
    sample_groups = (
        remaining_groups[: len(sample_axes)] if remaining_groups else tuple(fallback_groups)
    )
    sample_keys = tuple(key for group in sample_groups for key in group)

    return sample_axes, sample_groups, sample_keys


@dataclass(frozen=True, slots=True)
class _SampleOperands:
    program: FormulaProgram
    frames: tuple[pd.DataFrame, ...]
    unique_keys: tuple[UniqueKeys, ...]
    consumed: tuple[int, ...]


def _sample_operands(
    program: FormulaProgram,
    inputs: _FormulaInputs,
    operands: tuple[MetricNode, ...],
    occurrence: tuple[int, ...],
    sample_axes: tuple[SampleAxis, ...],
    sample_groups: tuple[tuple[str, ...], ...],
) -> _SampleOperands:
    declarations = dict(program.declared)
    bindings, frames, frame_keys = inputs.bindings, inputs.frames, inputs.frame_keys
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
        counts=tuple(count for count in program.counts if count.definition in relevant_counts),
        axes=(*program.axes, *sample_axes),
        parent_scope=program.parent_scope,
        parent_slots=program.parent_slots,
        calendar_samples=tuple(
            ((index, *path[len(prefix) :]), coordinate)
            for index in range(len(operands))
            for prefix in ((*occurrence, index),)
            for path, coordinate in program.calendar_samples
            if path[: len(prefix)] == prefix
        ),
        sample_matches=tuple(
            ((index, *path[len(prefix) :]), matches)
            for index in range(len(operands))
            for prefix in ((*occurrence, index),)
            for path, matches in program.sample_matches
            if path[: len(prefix)] == prefix
        ),
        key_groups=(*program.key_groups, *sample_groups),
    )
    return _SampleOperands(
        child_program, tuple(selected_frames), tuple(selected_keys), tuple(consumed)
    )


def _complete_sample_domain(
    program: FormulaProgram,
    domains: FormulaDomains,
    child_program: FormulaProgram,
    node: PerReduce | PerWeightedMean,
    occurrence: tuple[int, ...],
    sample_keys: tuple[str, ...],
    sample_domains: Mapping[tuple[int, ...], pd.DataFrame] | None,
) -> pd.DataFrame | None:
    domain, keys = domains.domain, domains.key_slots
    complete_keys = (*keys, *sample_keys)
    complete_domain = None
    sample_population_definition = sample_population(node.samples)
    if sample_population_definition is not None:
        sample_domain = domains.population_domain(sample_population_definition, sample_keys)
        matches = sample_key_matches(
            child_program.key_groups, dict(program.sample_matches).get(occurrence, ())
        )
        if matches:
            outer_slots = [match.outer for match in matches]
            inner_slots = []
            for position, match in enumerate(matches):
                if match.interval_keys is None:
                    inner_slots.append(match.inner)
                    continue
                mapped_slot = f"__sample_interval_{position}"
                sample_domain[mapped_slot] = sample_domain[match.inner].map(
                    {inner: outer for outer, inner in match.interval_keys}
                )
                inner_slots.append(mapped_slot)
            complete_domain = domain.merge(
                sample_domain, left_on=outer_slots, right_on=inner_slots
            ).loc[:, list(complete_keys)]
        else:
            complete_domain = domain.merge(sample_domain, how="cross") if keys else sample_domain
    if sample_domains is not None and occurrence in sample_domains:
        complete_domain = sample_domains[occurrence].loc[:, list(complete_keys)]
    return complete_domain


@dataclass(slots=True)
class _SampleEvaluation:
    program: FormulaProgram
    inputs: _FormulaInputs
    domains: FormulaDomains
    count_data: dict[PopulationDependency, tuple[MemberInput, pd.DataFrame]]
    sample_domains: Mapping[tuple[int, ...], pd.DataFrame] | None
    type_values: dict[tuple[int, ...], NumericValue]

    def visit(self, node: MetricNode, occurrence: tuple[int, ...]) -> None:
        if isinstance(node, (MemberCount, PerReduce, PerWeightedMean)):
            if any(binding.occurrence == occurrence for binding in self.inputs.bindings):
                return
            if isinstance(node, MemberCount):
                self._count(node, occurrence)
            else:
                self._reduce(node, occurrence)
            return
        if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
            return
        if isinstance(node, DeclaredMetricRef):
            self.visit(dict(self.program.declared)[node.ref], (*occurrence, 0))
            return
        for edge in children(node):
            if isinstance(edge.child, MetricNode):
                self.visit(edge.child, (*occurrence, edge.index))

    def _count(self, node: MemberCount, occurrence: tuple[int, ...]) -> None:
        key_slots = self.domains.key_slots
        keys = list(key_slots)
        bindings, frames, frame_keys = (
            self.inputs.bindings,
            self.inputs.frames,
            self.inputs.frame_keys,
        )
        count, member_data = self.count_data[node.population]
        selected = self.domains.count_coordinates(count, member_data)
        selected = self.domains.parent_scoped(selected)
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

    def _reduce(self, node: PerReduce | PerWeightedMean, occurrence: tuple[int, ...]) -> None:
        program = self.program
        bindings, frames, frame_keys = (
            self.inputs.bindings,
            self.inputs.frames,
            self.inputs.frame_keys,
        )
        key_slots, domain = self.domains.key_slots, self.domains.domain
        declarations = dict(program.declared)
        sample_domains = self.sample_domains
        sample_axes, sample_groups, sample_keys = _sample_layout(
            program, bindings, key_slots, node, occurrence
        )
        complete_keys = (*key_slots, *sample_keys)
        operands = (node.operand,) if isinstance(node, PerReduce) else (node.value, node.weight)
        selected = _sample_operands(
            program, self.inputs, operands, occurrence, sample_axes, sample_groups
        )
        child_program = selected.program
        complete_domain = _complete_sample_domain(
            program, self.domains, child_program, node, occurrence, sample_keys, sample_domains
        )
        child_domains = {
            (index, *path[len(prefix) :]): selected
            for index in range(len(operands))
            for prefix in ((*occurrence, index),)
            for path, selected in (sample_domains or {}).items()
            if path[: len(prefix)] == prefix
        }
        child_outputs = formula_columns(child_program)
        child_frame, child_checks = _combine_program(
            child_program,
            (*selected.frames, *self.domains.population_frames),
            complete_keys,
            child_outputs,
            complete_domain,
            unique_keys=selected.unique_keys,
            sample_domains=child_domains,
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
        if isinstance(node, PerReduce) and node.reducer in ("first", "last") and order_key is None:
            calendar_keys = [
                key
                for group, axis in zip(sample_groups, sample_axes, strict=True)
                if isinstance(axis, (CalendarCoordinate, BuiltinSampleCoordinate))
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
            lower_metric_formula(node, occurrence, self.type_values, declarations).value.type()
        )
        reduced, reduced_checks = _sample_numeric.reduce_samples(
            node,
            child_frame,
            key_slots,
            domain,
            tuple(column.slot_id for column in child_outputs),
            child_checks,
            order_key=order_key,
            exact_duration=isinstance(dtype, DurationDataType),
            output_type=dtype,
            unique_keys=UniqueKeys(tuple(str(c) for c in child_frame.columns), (complete_keys,)),
        )
        for index in sorted(set(selected.consumed), reverse=True):
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


def _align_formula_inputs(
    inputs: _FormulaInputs,
    domains: FormulaDomains,
) -> tuple[dict[tuple[int, ...], NumericValue], dict[str, pd.Series]]:
    domain, keys = domains.domain, list(domains.key_slots)
    frame_keys = inputs.frame_keys
    values: dict[tuple[int, ...], NumericValue] = {}
    physical: dict[str, pd.Series] = {}
    for input_index, (binding, frame) in enumerate(
        zip(inputs.bindings, inputs.frames, strict=True)
    ):
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
            match_keys = [key for key in keys if key in frame]
            aligned = (
                domain.merge(
                    marked,
                    on=match_keys,
                    how="left",
                    validate=None
                    if frame_keys[input_index].proves(tuple(match_keys))
                    else "many_to_one",
                    sort=False,
                )
                if match_keys
                else domain.merge(marked, how="cross")
            )
            value = aligned[slot]
            if binding.zero_when_absent:
                value = value.mask(
                    aligned["__numeric_present"].astype("boolean").fillna(False).eq(False)
                    if "__numeric_present" in aligned.columns
                    else aligned["__present"].isna(),
                    pd.Series(
                        [Decimal(0) if isinstance(binding.column.dtype, DecimalDataType) else 0]
                        * len(value),
                        index=value.index,
                        dtype=value.dtype,
                    ),
                )
        else:
            aligned = frame.reset_index(drop=True).iloc[: len(domain)]
            value = aligned[slot]
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
        for key, (column_name, expected, received) in zip(check_names, binding.checks, strict=True):
            physical[key] = aligned[column_name].astype("boolean").fillna(False).astype(object)
            checks.append(NumericCheck(table[key], expected, received))
        values[binding.occurrence] = NumericValue(table[name], tuple(checks))
    return values, physical


def _evaluate_formula_outputs(
    program: FormulaProgram,
    outputs: tuple[DataColumn, ...],
    values: dict[tuple[int, ...], NumericValue],
    physical: dict[str, pd.Series],
    domains: FormulaDomains,
    attribute: AttributeFormulaContext | None,
) -> tuple[pd.DataFrame, tuple[tuple[str, str, str], ...]]:
    domain, keys = domains.domain, list(domains.key_slots)
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
