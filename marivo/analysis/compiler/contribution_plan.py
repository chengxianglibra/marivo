"""Plan the physical keys, value slots and checks of ordinary contribution reads."""

from __future__ import annotations

from dataclasses import replace

import ibis
import ibis.expr.types as ir

from marivo._definition.expressions import (
    EntityCount,
    SourceAggregate,
)
from marivo._definition.observations import CalendarGrouping, ContributionBinding
from marivo._definition.types import (
    CalendarCoordinate,
    IntervalCoordinate,
    coordinate_read_keys,
)
from marivo._kernel.time_types import grain_token
from marivo.analysis.binding.calendar_keys import time_key_type
from marivo.analysis.compiler.observation import (
    duration_type,
    lower_observation,
    reduction_input,
)
from marivo.analysis.compiler.query_stages import (
    ReductionRead,
    SourceReadStage,
    SourceSchema,
)
from marivo.analysis.compiler.reduction_input import ReductionInput
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.analysis.model.data_types import DataColumn, DurationDataType, ScalarDataType
from marivo.analysis.model.errors import reject
from marivo.analysis.model.programs import FormulaInput
from marivo.analysis.model.unique_keys import UniqueKeys
from marivo.semantic.spi import NumericCheck


def _time_name(axis: CalendarGrouping) -> str:
    return (
        "interval"
        if isinstance(axis, IntervalCoordinate)
        else f"calendar:{grain_token(axis.grain_spec)}"
    )


def plan_contributions(
    source_stages: tuple[SourceReadStage, ...],
    contributions: tuple[ContributionBinding, ...],
    schemas: tuple[SourceSchema, ...],
    calendar: CalendarCoordinate | IntervalCoordinate | None,
    has_output_population: bool,
) -> tuple[list[SourceReadStage], list[FormulaInput], tuple[DataColumn, ...], str]:
    reads: list[SourceReadStage] = []
    values: list[FormulaInput] = []
    keys: tuple[DataColumn, ...] = ()
    calendar_key = "key.0"
    if not source_stages and calendar is not None:
        keys = (DataColumn(calendar_key, _time_name(calendar), time_key_type(calendar), False),)
    for stage, contribution in zip(source_stages, contributions, strict=True):
        reduction, expression, checks, stage_keys = _lower_contribution(
            stage, contribution, schemas, calendar, has_output_population
        )
        keys, calendar_key = _merge_output_keys(
            contribution, reduction, stage_keys, keys, calendar, calendar_key
        )
        read, value = _physical_read(stage, contribution, reduction, expression, checks, stage_keys)
        reads.append(read)
        values.append(value)
    return reads, values, keys, calendar_key


def _lower_contribution(
    stage: SourceReadStage,
    contribution: ContributionBinding,
    schemas: tuple[SourceSchema, ...],
    calendar: CalendarCoordinate | IntervalCoordinate | None,
    has_output_population: bool,
) -> tuple[ReductionInput, ir.Table, tuple[tuple[str, NumericCheck], ...], tuple[DataColumn, ...]]:
    reduction = reduction_input(contribution, f"{stage.stage_id}/value", calendar)
    schema = next(
        item
        for item in schemas
        if item.source_id == stage.source_id
        and (item.occurrence is None or item.occurrence == stage.occurrence)
    )
    reduction = replace(
        reduction,
        provider=schema.provider,
        preserve_sample_presence=(
            has_output_population
            and (bool(stage.predicates) or not (reduction.axes or reduction.calendars))
        ),
        duration=duration_type(
            contribution.definition, {item.field: item.dtype for item in schema.fields}
        ),
    )
    table = ibis.table({item.column: ibis_type(item.dtype) for item in schema.fields})
    expression, numeric_checks = lower_observation(
        reduction, table, dict(stage.field_columns), stage.predicates
    )
    calendar_slots = dict(reduction.calendars)
    stage_keys = tuple(
        DataColumn(
            slot,
            (_time_name(calendar_slots[slot]))
            if slot in calendar_slots
            else next(path for key, path in reduction.axes if key == slot),
            next(
                (
                    field.dtype
                    for field in schema.fields
                    if field.field
                    == next((path for key, path in reduction.axes if key == slot), "")
                    and isinstance(field.dtype, DurationDataType)
                ),
                data_type(expression[slot].type()),
            ),
            True,
        )
        for slot in expression.columns
        if slot.startswith("key.")
    )
    return reduction, expression, numeric_checks, stage_keys


def _merge_output_keys(
    contribution: ContributionBinding,
    reduction: ReductionInput,
    stage_keys: tuple[DataColumn, ...],
    keys: tuple[DataColumn, ...],
    calendar: CalendarCoordinate | IntervalCoordinate | None,
    calendar_key: str,
) -> tuple[tuple[DataColumn, ...], str]:
    if calendar is not None:
        calendar_key = next(
            (slot for slot, axis in reduction.calendars if axis == calendar), calendar_key
        )
    output_width = int(calendar is not None) + sum(
        len(coordinate_read_keys(read))
        for read in contribution.coordinates[: contribution.output_axis_count]
    )
    output_slots = {f"key.{i}" for i in range(output_width)}
    output_keys = tuple(c for c in stage_keys if c.slot_id in output_slots)
    merged_keys = {c.slot_id: c for c in keys}
    if calendar is not None:
        report_slot = next(
            (slot for slot, axis in reduction.calendars if axis == calendar),
            next(
                (
                    f"key.{i}"
                    for i in range(output_width)
                    if f"key.{i}" not in contribution.coordinate_slots
                ),
                calendar_key,
            ),
        )
        calendar_key = report_slot
        merged_keys[report_slot] = DataColumn(
            report_slot, _time_name(calendar), time_key_type(calendar), False
        )

    for key in output_keys:
        previous = merged_keys.get(key.slot_id)
        if previous is not None and previous.dtype != key.dtype:
            reject(
                "identical physical coordinate types across contributions",
                repr(key),
                "Choose fields with a common lossless coordinate type.",
                "analysis.query_plan",
            )
        merged_keys[key.slot_id] = key
    keys = tuple(merged_keys[k] for k in sorted(merged_keys, key=lambda k: int(k.split(".")[1])))
    return keys, calendar_key


def _physical_read(
    stage: SourceReadStage,
    contribution: ContributionBinding,
    reduction: ReductionInput,
    expression: ir.Table,
    numeric_checks: tuple[tuple[str, NumericCheck], ...],
    stage_keys: tuple[DataColumn, ...],
) -> tuple[SourceReadStage, FormulaInput]:
    column = DataColumn(
        reduction.value_slot,
        reduction.value_slot,
        reduction.duration or data_type(expression[reduction.value_slot].type()),
        True,
    )
    read_stage = replace(
        stage,
        columns=(
            *stage_keys,
            column,
            *(
                DataColumn(name, name, ScalarDataType("boolean"), False)
                for name, _ in numeric_checks
            ),
            *(
                (
                    DataColumn(
                        "__numeric_present",
                        "__numeric_present",
                        ScalarDataType("boolean"),
                        False,
                    ),
                )
                if "__numeric_present" in expression.columns
                else ()
            ),
            *(
                (
                    DataColumn(
                        "__sample_present",
                        "__sample_present",
                        ScalarDataType("boolean"),
                        False,
                    ),
                )
                if "__sample_present" in expression.columns
                else ()
            ),
            *(
                DataColumn(name, name, ScalarDataType("boolean"), False)
                for name in expression.columns
                if name.startswith("__sample_scope_")
            ),
        ),
        keys=tuple(c.slot_id for c in stage_keys),
        unique_keys=UniqueKeys(tuple(expression.columns), (tuple(c.slot_id for c in stage_keys),)),
        program=ReductionRead(reduction),
    )
    node = contribution.definition
    key_groups = []
    ordinary = iter(slot for slot, _ in reduction.axes)
    for read in contribution.coordinates:
        key_groups.append(tuple(next(ordinary) for _ in coordinate_read_keys(read)))
    key_groups.extend((slot,) for slot, _ in reduction.calendars)
    key_groups.sort(key=lambda group: int(group[0].split(".")[1]))
    value = FormulaInput(
        contribution.logical_occurrence or contribution.occurrence,
        stage.stage_id,
        column,
        isinstance(node, EntityCount)
        or (
            isinstance(node, SourceAggregate)
            and (node.reducer in ("sum", "count", "count_distinct", "approx_nunique"))
        ),
        tuple((name, check.expected, check.received) for name, check in numeric_checks),
        keys=tuple(c.slot_id for c in stage_keys),
        key_groups=tuple(key_groups),
    )
    return read_stage, value
