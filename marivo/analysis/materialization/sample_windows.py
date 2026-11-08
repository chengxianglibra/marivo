"""Bind calendar-sample windows from their frozen, actual joint sample domains."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime

import ibis
import pandas as pd

from marivo._definition.correspondence import coordinate_matches
from marivo._definition.expressions import (
    BuiltinSampleCoordinate,
    DeclaredMetricRef,
    EntityCount,
    FieldCalendarSample,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
)
from marivo._definition.graph import children
from marivo._definition.observations import ContributionBinding, ObservationPeriod, SampleWindow
from marivo._definition.relations import (
    ObserveQuery,
    PopulationDependency,
    population_keys,
    sample_population,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    IntervalCoordinate,
    coordinate_read_keys,
    field_read_key,
)
from marivo._temporal import Grain, anchor_from_datetime
from marivo.analysis.binding.windows import bind_windows
from marivo.analysis.compiler.calendar import bind_calendar
from marivo.analysis.compiler.graph_lowering import LoweredQuery, LoweredRead, lower_query
from marivo.analysis.compiler.graph_plan import (
    FrameKeysInput,
    QueryStage,
    SampleWindowRead,
    SourceRead,
    plan_query,
    read_reduction,
)
from marivo.analysis.compiler.observation import reduction_input
from marivo.analysis.core.calendar_keys import time_key_type
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DataColumn
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import reject
from marivo.analysis.core.value_types import ibis_type
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    execute_numeric_query,
    prepare_query_sources,
    qualify_query_sources,
)
from marivo.analysis.materialization.native_calendar import _actual_positions, _point
from marivo.analysis.materialization.query_parameters import instantiate, parameter_data
from marivo.semantic._compiled_state import CompiledSemanticState


def has_sample_windows(graph: BoundGraph) -> bool:
    observation = graph.signature.observation
    return observation is not None and any(
        item.sample_window is not None for item in observation.contributions
    )


def _consumers(stages: tuple[QueryStage, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (
            stage.stage_id,
            tuple(other.stage_id for other in stages if stage.stage_id in other.inputs),
        )
        for stage in stages
    )


def _sample_slot(graph: BoundGraph, item: ContributionBinding, context: SampleWindow) -> str:
    width = sum(
        len(coordinate_read_keys(read)) for read in item.coordinates[: item.output_axis_count]
    )
    width += sum(
        isinstance(axis, (CalendarCoordinate, IntervalCoordinate)) for axis in graph.signature.axes
    )
    ordinary = iter(item.coordinates[item.output_axis_count :])
    for axis in item.sample_axes[: context.depth]:
        width += (
            1
            if isinstance(axis, (BuiltinSampleCoordinate, CalendarCoordinate, IntervalCoordinate))
            else len(coordinate_read_keys(next(ordinary)))
        )
    return f"key.{width - 1}"


def prepare_sample_window_plan(
    graph: BoundGraph,
    prepared: PreparedSources,
    state: CompiledSemanticState,
    timezone: str,
    frame_schemas: Mapping[str, DataSchema],
) -> tuple[LoweredQuery, PreparedSources]:
    """Discover sample keys with source conditions before evaluating their windows."""
    assert isinstance(graph.root, ObserveQuery) and graph.signature.observation is not None
    observation = graph.signature.observation
    originals = observation.contributions
    contributions = tuple(
        replace(
            item,
            definition=EntityCount(item.root, item.time),
            window_rule=None,
            sample_window=None,
            window_coordinates=(),
        )
        for item in originals
    )
    candidate = replace(
        graph,
        signature=replace(
            graph.signature, observation=replace(observation, contributions=contributions)
        ),
    )
    sources = prepare_query_sources(
        candidate,
        state,
        prepared.source,
        sources={source.datasource.semantic_id: source for source in prepared.sources.values()},
        consumption_timezone=timezone,
        frame_schemas=frame_schemas,
    )
    plan = plan_query(candidate, sources.schemas)
    candidates = tuple(
        next(
            stage
            for stage in plan.stages
            if isinstance(stage, SourceRead)
            and stage.occurrence == item.occurrence
            and stage.source_id == item.root.ref.path
            and read_reduction(stage) is not None
        )
        for item in originals
    )
    shape = plan_query(graph, prepared.schemas)
    endpoint = next(stage for stage in shape.stages if stage.stage_id == shape.publish)
    keys_by_id = {
        stage.stage_id: stage
        for stage in (*plan.stages, *shape.stages)
        if isinstance(stage, FrameKeysInput)
    }
    keys = tuple(keys_by_id.values())
    assert plan.original is not None
    populations = plan.original.populations
    finish = SourceRead(
        "sample-windows",
        prepared.source.datasource.semantic_id,
        (),
        endpoint.columns,
        endpoint.keys,
        SampleWindowRead(
            graph,
            state,
            timezone,
            tuple(frame_schemas.items()),
            tuple(stage.stage_id for stage in keys),
            originals,
            populations,
            plan.original.counts,
        ),
        (),
        (),
        None,
        inputs=(
            *tuple(stage.stage_id for stage in candidates),
            *tuple(stage.stage_id for stage in keys),
            *tuple(population.stage_id for population in populations),
        ),
        unique_keys=endpoint.unique_keys,
    )
    stages = (
        *(stage for stage in keys if stage.stage_id not in {item.stage_id for item in plan.stages}),
        *tuple(
            stage for stage in plan.stages if stage.stage_id != plan.publish or stage in candidates
        ),
        finish,
    )
    plan = replace(
        plan,
        stages=stages,
        consumers=_consumers(stages),
        publish=finish.stage_id,
        original=shape.original,
        required_slots=(),
        output_groups=shape.output_groups,
    )
    return lower_query(plan, sources.relations), replace(
        sources, source_times=prepared.source_times, sources={**prepared.sources, **sources.sources}
    )


def _enclosing_populations(
    graph: BoundGraph,
) -> dict[tuple[int, ...], tuple[tuple[int, PopulationDependency], ...]]:
    """Retain explicit parent sample selections before discovering inner endpoints."""
    assert isinstance(graph.root, ObserveQuery)
    declared = dict(graph.signature.declared_metrics)
    result: dict[tuple[int, ...], tuple[tuple[int, PopulationDependency], ...]] = {}

    def visit(
        node: MetricNode,
        path: tuple[int, ...],
        depth: int,
        selected: tuple[tuple[int, PopulationDependency], ...],
    ) -> None:
        result[path] = selected
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref], (*path, 0), depth, selected)
            return
        if isinstance(node, (PerReduce, PerWeightedMean)):
            population = sample_population(node.samples)
            if population is not None:
                selected = (*selected, (depth, population))
            samples = node.samples
            depth += (
                1
                if isinstance(samples, (Grain, FieldCalendarSample))
                else len(samples if isinstance(samples, tuple) else samples.axes)
            )
        for edge in children(node):
            if isinstance(edge.child, MetricNode):
                visit(edge.child, (*path, edge.index), depth, selected)

    for index, (_, metric) in enumerate(graph.root.metrics):
        visit(metric, (index,), 0, ())
    return result


def _population_domain(
    population: PopulationDependency,
    slots: tuple[str, ...],
    populations: Mapping[PopulationDependency, pd.DataFrame],
) -> pd.DataFrame:
    """Read captured finite or prepared population keys with their physical slots."""
    finite = population_keys(population)
    if finite is None:
        keys = populations[population].copy()
        keys.columns = list(slots)
    else:
        rows = [
            tuple(
                component
                for axis, value in zip(population.axes, row, strict=True)
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
        keys = pd.DataFrame(rows, columns=slots)
    return keys


def _select_parent_samples(
    graph: BoundGraph,
    item: ContributionBinding,
    domain: pd.DataFrame,
    selections: tuple[tuple[int, PopulationDependency], ...],
    populations: Mapping[PopulationDependency, pd.DataFrame],
) -> pd.DataFrame:
    for depth, population in selections:
        slots: list[str] = []
        for axis in population.axes:
            offset = next(
                index
                for index, sample in enumerate(item.sample_axes[depth:])
                if not isinstance(sample, BuiltinSampleCoordinate)
                and coordinate_matches(sample, axis)
            )
            start = int(_sample_slot(graph, item, SampleWindow((), depth + offset))[4:]) + 1
            end = int(_sample_slot(graph, item, SampleWindow((), depth + offset + 1))[4:]) + 1
            slots.extend(f"key.{index}" for index in range(start, end))
        keys = _population_domain(population, tuple(slots), populations)
        domain = (
            domain.merge(keys.drop_duplicates(), on=slots, how="inner")
            if slots
            else domain
            if len(keys)
            else domain.iloc[:0]
        )
    return domain


def _domains(
    program: SampleWindowRead,
    inputs: tuple[StageOutput, ...],
    population_outputs: tuple[StageOutput, ...],
) -> tuple[dict[tuple[int, ...], pd.DataFrame], dict[tuple[int, ...], dict[str, DataColumn]]]:
    observation = program.graph.signature.observation
    assert observation is not None
    contexts = tuple(
        dict.fromkeys(
            item.sample_window
            for item in observation.contributions
            if item.sample_window is not None
        )
    )
    selections = _enclosing_populations(program.graph)
    populations = {
        binding.definition: output.table.loc[:, list(binding.keys)]
        for binding, output in zip(program.populations, population_outputs, strict=True)
    }
    domains: dict[tuple[int, ...], pd.DataFrame] = {}
    columns: dict[tuple[int, ...], dict[str, DataColumn]] = {}
    for context in contexts:
        context_columns: dict[str, DataColumn] = {}
        parts = []
        for item, output in zip(program.candidates, inputs, strict=True):
            if item.occurrence[: len(context.occurrence)] != context.occurrence:
                continue
            slot = _sample_slot(program.graph, item, context)
            keys = tuple(f"key.{i}" for i in range(int(slot[4:]) + 1))
            selected = output.table
            missing = tuple(key for key in keys if key not in output.keys)
            report_axis = next(
                (
                    axis
                    for axis in program.graph.signature.axes
                    if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
                ),
                None,
            )
            if missing and item.time is None and report_axis is not None:
                sample_keys = {key for key, _ in item.calendars}
                output_width = 1 + sum(
                    len(coordinate_read_keys(read))
                    for read in item.coordinates[: item.output_axis_count]
                )
                report_keys = tuple(
                    key for key in missing if int(key[4:]) < output_width and key not in sample_keys
                )
                if len(report_keys) == 1:
                    report_key = report_keys[0]
                    outer_keys = tuple(f"key.{i}" for i in range(output_width))
                    output_population = observation.coordinates.population
                    if output_population is not None:
                        outer = _population_domain(output_population, outer_keys, populations)
                    else:
                        outer = _required_report_domain(outer_keys, inputs)
                        count_domains = []
                        for count in program.counts:
                            groups = [
                                group
                                for axis in program.graph.signature.axes
                                for candidate, group in count.axis_mapping
                                if coordinate_matches(axis, candidate)
                            ]
                            if len(groups) != len(program.graph.signature.axes):
                                continue
                            members = _population_domain(count.population, count.keys, populations)
                            count_keys = members.loc[
                                :, [key for group in groups for key in group]
                            ].copy()
                            count_keys.columns = list(outer_keys)
                            count_domains.append(count_keys)
                        if count_domains:
                            represented = [
                                part for part in (outer, *count_domains) if not part.empty
                            ]
                            if represented:
                                outer = pd.concat(represented, ignore_index=True).drop_duplicates()
                    shared = [key for key in outer_keys if key in selected]
                    selected = (
                        outer.merge(selected, on=shared, how="inner")
                        if shared
                        else outer.merge(selected, how="cross")
                    )
                    context_columns[report_key] = DataColumn(
                        report_key, "report", time_key_type(report_axis), False
                    )
            if not all(key in selected for key in keys):
                continue  # Independent leaves do not invent contribution-clock samples.
            presence = next(
                (
                    name
                    for name in (
                        f"__sample_scope_{len(keys)}",
                        "__sample_present",
                        "__numeric_present",
                    )
                    if name in selected
                ),
                None,
            )
            if presence is not None:
                selected = selected.loc[selected[presence].fillna(False)]
            selected = _select_parent_samples(
                program.graph, item, selected, selections[context.occurrence], populations
            )
            if selected[slot].isna().any():
                reject(
                    "non-NULL internal calendar sample time",
                    "NULL sample time",
                    "Filter or repair missing sample times before calendar sampling.",
                    "Metric.per",
                )
            parts.append(selected.loc[:, list(keys)])
            context_columns.update(
                {column.slot_id: column for column in output.columns if column.slot_id in keys}
            )
        representative = next(
            item for item in observation.contributions if item.sample_window == context
        )
        slot = _sample_slot(program.graph, representative, context)
        keys = tuple(f"key.{i}" for i in range(int(slot[4:]) + 1))
        domains[context.occurrence] = (
            pd.concat(parts, ignore_index=True).drop_duplicates().reset_index(drop=True)
            if parts
            else pd.DataFrame(columns=keys)
        )
        columns[context.occurrence] = context_columns
    return domains, columns


def _restrict(
    prepared: PreparedSources,
    item: ContributionBinding,
    allowed: pd.DataFrame,
    columns: Mapping[str, DataColumn],
    width: int,
) -> None:
    """Retain ordinary joint keys and explicit sample cohorts on other clocks."""
    identifier = "read:" + ".".join(str(index) for index in item.occurrence)
    schema = next(
        value
        for value in prepared.schemas
        if value.source_id == item.root.ref.path and value.occurrence == item.occurrence
    )
    reduction = reduction_input(item, "", None)
    table = prepared.relations[identifier]
    fields = {
        slot: table[schema.field(path).column]
        for slot, path in reduction.axes
        if int(slot[4:]) < width
    }
    for slot, read in item.sample_calendar_reads:
        if int(slot[4:]) >= width or (item.time is not None and read.field == item.time.field):
            continue
        calendar = dict(item.calendars)[slot]
        name = "__sample_cohort_" + slot.replace(".", "_")
        table = bind_calendar(
            table, table[schema.field(field_read_key(read)).column], calendar, name
        )
        fields[slot] = table[name]
    if not fields:
        return
    names = tuple(fields)
    key_columns = tuple(columns[name] for name in names)
    output = StageOutput.from_frame(
        allowed.loc[:, list(names)].drop_duplicates(), key_columns, names
    )
    name = "__marivo_sample_window_keys"
    keys = ibis.table(
        {column.slot_id: ibis_type(column.dtype) for column in key_columns}, name=name
    )
    matched = table.semi_join(keys, [fields[slot].identical_to(keys[slot]) for slot in names])
    owner = prepared.sources[item.root.ref.path]
    relation = (
        owner._bind_key_relation(parameter_data(key_columns, output))
        if owner.provider.name == "duckdb"
        else None
    )
    prepared.relations[identifier] = instantiate(
        matched,
        name,
        key_columns,
        output,
        relation=relation,
        provider=owner.provider.name,
        source=owner,
    )


def _required_report_domain(
    keys: tuple[str, ...], candidates: tuple[StageOutput, ...]
) -> pd.DataFrame:
    """Keep actual outer support independent of internal sample selection."""
    parts = []
    for output in candidates:
        selected = output.table
        presence = next(
            (
                name
                for name in (
                    f"__sample_scope_{len(keys)}",
                    "__sample_present",
                    "__numeric_present",
                )
                if name in selected
            ),
            None,
        )
        if presence is not None:
            selected = selected.loc[selected[presence].fillna(False)]
        if all(key in selected for key in keys):
            parts.append(selected.loc[:, list(keys)])
    if not keys:
        return pd.DataFrame(index=pd.RangeIndex(int(any(len(part) for part in parts))))
    return (
        pd.concat(parts, ignore_index=True).drop_duplicates().reset_index(drop=True)
        if parts
        else pd.DataFrame(columns=keys)
    )


def run_sample_windows(
    prepared: PreparedSources,
    stage: SourceRead,
    program: SampleWindowRead,
    inputs: tuple[StageOutput, ...],
    *,
    observe_read: Callable[[SourceRead, LoweredRead, StageOutput], None] | None,
) -> StageOutput:
    """Evaluate each window at its sample endpoint, then fold the captured domain."""
    candidates = inputs[: len(program.candidates)]
    key_end = len(program.candidates) + len(program.key_inputs)
    domains, columns = _domains(program, candidates, inputs[key_end:])
    graph = program.graph
    observation = graph.signature.observation
    assert observation is not None
    contributions: list[ContributionBinding] = []
    restrictions: list[tuple[ContributionBinding, pd.DataFrame, Mapping[str, DataColumn], int]] = []
    native_bounds: dict[tuple[tuple[int, ...], datetime], tuple[datetime, datetime]] = {}
    for item in observation.contributions:
        context = item.sample_window
        if context is None:
            contributions.append(replace(item, sample_window=None))
            continue
        domain = domains[context.occurrence]
        slot = _sample_slot(graph, item, context)
        width = int(slot[4:]) + 1
        sample = item.sample_axes[context.depth - 1]
        assert isinstance(sample, BuiltinSampleCoordinate) and item.window_rule is not None
        rule = item.window_rule
        if domain.empty:
            contributions.append(replace(item, sample_window=None))
            continue
        owner = prepared.sources[item.root.ref.path]
        coordinate = CalendarCoordinate(
            "sample:" + ".".join(map(str, context.occurrence)),
            sample.grain_spec,
            sample.timezone,
            (),
            civil_bounds=(),
            actual_only=True,
        )
        calendar, starts = _actual_positions(
            coordinate, owner, tuple(sorted({_point(value) for value in domain[slot]})), (rule,)
        )
        for cell in calendar.cells:
            identity = (context.occurrence, cell[0])
            bounds = (cell[0], cell[1])
            if identity in native_bounds and native_bounds[identity] != bounds:
                reject(
                    "consistent native sample endpoints across contributing executors",
                    repr(identity),
                    "Observe the executor-specific sample domains separately.",
                    "Metric.window",
                )
            native_bounds[identity] = bounds
        cells = {
            cell[0]: (cell, civil)
            for cell, civil in zip(calendar.cells, calendar.civil_bounds, strict=True)
        }
        temporal = tuple(key for key, _ in item.calendars if int(key[4:]) < width)
        positions = domain.loc[:, list(temporal)].drop_duplicates()
        for index, row in enumerate(positions.itertuples(index=False, name=None)):
            assigned = {
                key: int(value)
                if isinstance(dict(item.calendars)[key], IntervalCoordinate)
                else _point(value)
                for key, value in zip(temporal, row, strict=True)
            }
            point = anchor_from_datetime(_point(assigned[slot]))
            cell, civil = cells[point]
            same_clock = not isinstance(sample.sample, FieldCalendarSample) or (
                item.time is not None and sample.sample.field.field == item.time.field
            )
            if same_clock and item.period is not None:
                cell = (
                    cell[0],
                    cell[1],
                    max(cell[2], item.period.start) if item.period.start is not None else cell[2],
                    min(cell[3], item.period.end),
                )
            endpoint = replace(
                calendar,
                cells=(cell,),
                civil_bounds=(civil,),
                actual_only=False,
                ranges=((cell[2], cell[3]),),
            )
            fake = replace(
                item,
                occurrence=(0, 0),
                logical_occurrence=None,
                sample_window=None,
                calendars=(("key.0", endpoint),),
                window_coordinates=(),
            )
            bound = bind_windows(
                (fake,),
                (("window", MetricWindow(item.definition, rule)),),
                {},
                ObservationPeriod(cell[2], cell[3], sample.timezone),
                endpoint,
                native_starts=starts,
            )[0]
            if item.time is None:
                continue
            expanded = replace(
                item,
                occurrence=(*item.occurrence, index),
                logical_occurrence=item.occurrence,
                sample_window=None,
                window_key=None,
                window_coordinates=tuple(assigned.items()),
                period=bound.period,
            )
            selected = domain
            for key, value in assigned.items():
                selected = selected.loc[selected[key] == value]
            contributions.append(expanded)
            restrictions.append((expanded, selected, columns[context.occurrence], width))
        if item.time is None:
            contributions.append(replace(item, sample_window=None))
    rebound = replace(
        graph,
        signature=replace(
            graph.signature, observation=replace(observation, contributions=tuple(contributions))
        ),
    )
    sources = prepare_query_sources(
        rebound,
        program.state,
        prepared.source,
        sources={source.datasource.semantic_id: source for source in prepared.sources.values()},
        consumption_timezone=program.timezone,
        frame_schemas=dict(program.frame_schemas),
    )
    for item, selected, selected_columns, width in restrictions:
        _restrict(sources, item, selected, selected_columns, width)
    # Source reductions remain native; the existing local sample kernel consumes
    # the frozen domains, including samples whose windows have no contributions.
    plan = plan_query(rebound, sources.schemas)
    lowered = lower_query(plan, sources.relations)
    qualify_query_sources(sources, lowered)
    output = execute_numeric_query(
        sources,
        lowered,
        observe_read=observe_read,
        report_domain=_required_report_domain(stage.keys, candidates)
        if observation.coordinates.population is not None
        else None,
        sample_domains=domains,
        frame_key_inputs=dict(
            zip(program.key_inputs, inputs[len(program.candidates) : key_end], strict=True)
        ),
    )
    return replace(
        output,
        receipts=(
            *tuple(receipt for candidate in candidates for receipt in candidate.receipts),
            *output.receipts,
        ),
    )
