"""Resolve Population key layouts and materialize finite typed relations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo._definition.graph import computation_fingerprint, walk
from marivo._definition.relations import (
    AttributeKeys,
    FrameKeys,
    PopulationDependency,
    PopulationProgram,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    CoordinateValue,
    FieldRead,
    IntervalCoordinate,
    LiteralValue,
)
from marivo._kernel.numeric_types import exact_unsigned
from marivo._kernel.refs import ref
from marivo.analysis.compiler.populations import frame_key_groups
from marivo.analysis.core.value_types import data_type
from marivo.analysis.materialization.population_domains import (
    PreparedPopulation,
)
from marivo.analysis.model.errors import reject
from marivo.semantic.spi import (
    CompiledSemanticState,
    evaluate_expression_body,
)


@dataclass(frozen=True, slots=True)
class PopulationLayout:
    key_groups: tuple[tuple[str, ...], ...]
    columns: tuple[str, ...]
    types: dict[str, dt.DataType]


def population_field(read: FieldRead, table: ir.Table, state: CompiledSemanticState) -> ir.Value:
    body = state.sidecar.bodies.get(read.field.ref)
    if body is None:
        reject(
            "a captured field body",
            read.field.ref.key,
            "Load the field's owning model.",
            "population.prepare",
        )
    return exact_unsigned(
        evaluate_expression_body(
            catalog_definition_fingerprint=state.definition_fingerprint,
            expression_sidecar=state.sidecar,
            owning_ref=read.field.ref,
            body=body,
            entity_refs=(ref.entity(read.entity.ref.path),),
            aliases=(table,),
        )
    )


def _frame_axis_types(
    definition: PopulationDependency, entities: Mapping[str, ir.Table]
) -> dict[AxisBinding, tuple[dt.DataType, ...]]:
    frame_types: dict[AxisBinding, tuple[dt.DataType, ...]] = {}
    for captured_domain in walk((definition,), resolve=lambda _: None).order:
        if isinstance(captured_domain, PopulationProgram) and isinstance(
            captured_domain.root, (FrameKeys, AttributeKeys)
        ):
            relation = entities.get(
                "population-parameter:" + computation_fingerprint(captured_domain)
            )
            if relation is None:
                continue

            for binding, names in zip(
                captured_domain.root.bindings,
                frame_key_groups(captured_domain.root)[: len(captured_domain.root.bindings)],
                strict=True,
            ):
                captured_types = tuple(relation[name].type() for name in names)
                previous = frame_types.get(binding.target)
                if previous is not None and tuple(data_type(dtype) for dtype in previous) != tuple(
                    data_type(dtype) for dtype in captured_types
                ):
                    reject(
                        "compatible complete types for a shared Population target axis",
                        repr((previous, captured_types)),
                        "Map compatible complete Frame key types before combining their domains.",
                        "frame.population",
                    )
                frame_types[binding.target] = captured_types
    return frame_types


def population_layout(
    definition: PopulationDependency, entities: Mapping[str, ir.Table], state: CompiledSemanticState
) -> PopulationLayout:
    groups: list[tuple[str, ...]] = []
    types: dict[str, dt.DataType] = {}
    frame_types = _frame_axis_types(definition, entities)
    group: tuple[str, ...]
    for index, axis in enumerate(definition.axes):
        if isinstance(axis, (CalendarCoordinate, IntervalCoordinate)):
            group = (f"__population_{index}_0",)
            types[group[0]] = (
                dt.Timestamp(timezone="UTC", scale=6)
                if isinstance(axis, CalendarCoordinate)
                else dt.int64
            )
            groups.append(group)
            continue
        if not isinstance(axis, AxisBinding):
            reject(
                "source field or identity axes",
                "a correspondence axis",
                "Select a complete original endpoint projection before reading a source.",
                "population.prepare",
            )
        axis_table = entities.get(axis.entity.ref.path)
        captured = frame_types.get(axis)
        if axis_table is None and captured is not None:
            group = tuple(f"__population_{index}_{component}" for component in range(len(captured)))
            types.update(zip(group, captured, strict=True))
            groups.append(group)
            continue
        if axis_table is None:
            reject(
                "a prepared explicit source or Frame-key schema",
                axis.entity.ref.key,
                "Prepare the required source leaf or retain the Frame input.",
                "frame.population",
            )
        if axis.field is None:
            keys = state.registry.entities[axis.entity.ref.path].primary_key
            group = tuple(f"__population_{index}_{component}" for component in range(len(keys)))
            for name, key in zip(group, keys, strict=True):
                types[name] = axis_table[key].type()
        else:
            group = (f"__population_{index}_0",)
            types[group[0]] = population_field(
                FieldRead(axis.field, axis.entity, axis.path, axis.at), axis_table, state
            ).type()
        groups.append(group)
    key_groups = tuple(groups)
    columns = tuple(column for group in groups for column in group)
    return PopulationLayout(key_groups, columns, types)


def finite_population(
    definition: PopulationDependency,
    finite: tuple[tuple[CoordinateValue, ...], ...],
    layout: PopulationLayout,
) -> PreparedPopulation:
    key_groups, columns, types = layout.key_groups, layout.columns, layout.types
    groups = key_groups
    data: dict[str, list[LiteralValue]] = {column: [] for column in columns}
    for row in finite:
        for axis, group, value in zip(definition.axes, groups, row, strict=True):
            values: tuple[CoordinateValue, ...] = (
                value
                if isinstance(axis, AxisBinding)
                and axis.role == "identity"
                and isinstance(value, tuple)
                else (value,)
            )
            if len(values) != len(group):
                reject(
                    "a complete primary-key tuple",
                    str(value),
                    "Supply every declared primary-key component in order.",
                    "population.prepare",
                )
            for column, item in zip(group, values, strict=True):
                if isinstance(item, tuple):
                    reject(
                        "scalar coordinate components",
                        str(item),
                        "Remove nested key tuples.",
                        "population.prepare",
                    )
                if isinstance(item, bool) and types[column].is_integer():
                    reject(
                        "an integer coordinate",
                        "bool",
                        "Supply a value with the field's real type.",
                        "population.prepare",
                    )
                data[column].append(item)
    unit = ibis.literal(True).name("__population_unit").as_table()
    if not columns:
        return PreparedPopulation(
            unit if finite else unit.filter(ibis.literal(False)),
            key_groups,
            presence="__population_unit",
        )
    # Literal-only scalar relations are governed inline constants, not a
    # second physical table or a registered in-memory backend resource.
    rows = tuple(
        unit.select(
            **{column: ibis.literal(data[column][index], type=types[column]) for column in columns}
        )
        for index in range(len(finite))
    )
    if not rows:
        return PreparedPopulation(
            unit.select(
                **{
                    column: ibis.literal(None, type=types[column].copy(nullable=True))
                    for column in columns
                }
            ).filter(ibis.literal(False)),
            key_groups,
        )
    table = ibis.union(rows[0], *rows[1:])
    return PreparedPopulation(table, key_groups)
