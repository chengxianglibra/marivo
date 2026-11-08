"""Execute Grain-only windows after obtaining the exact contribution key image."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

import ibis
import pandas as pd

from marivo._definition.expressions import EntityCount
from marivo._definition.observations import ObservationPeriod
from marivo._definition.relations import ObserveQuery
from marivo._definition.types import CalendarCoordinate
from marivo.analysis.binding.windows import actual_window_axis, bind_windows
from marivo.analysis.compiler.graph_lowering import LoweredQuery, LoweredRead, lower_query
from marivo.analysis.compiler.graph_plan import (
    ActualWindowRead,
    FrameKeysInput,
    SourceRead,
    plan_query,
)
from marivo.analysis.compiler.observation import calendar_field, reduction_input
from marivo.analysis.compiler.optimization import optimize_query
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import BoundQuantity
from marivo.analysis.core.value_types import ibis_type
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    execute_numeric_query,
    prepare_query_sources,
    qualify_query_sources,
)
from marivo.analysis.materialization.native_calendar import (
    _actual_positions,
    _point,
    _replace_calendar,
)
from marivo.analysis.materialization.query_parameters import instantiate, parameter_data
from marivo.semantic._compiled_state import CompiledSemanticState


def prepare_actual_window_plan(
    graph: BoundGraph,
    prepared: PreparedSources,
    state: CompiledSemanticState,
    axis: CalendarCoordinate,
    timezone: str,
    frame_schemas: Mapping[str, DataSchema],
) -> tuple[LoweredQuery, PreparedSources]:
    """Plan candidate key discovery with the usual source predicates and projections."""
    assert isinstance(graph.root, ObserveQuery) and graph.signature.observation is not None
    observation = graph.signature.observation
    contributions = tuple(
        replace(
            item,
            occurrence=(index,),
            definition=EntityCount(item.root, item.time),
            metric=f"candidate_{index}",
            coordinates=item.coordinates[: item.output_axis_count],
            calendars=tuple(
                (slot, calendar) for slot, calendar in item.calendars if calendar == axis
            ),
            sample_axes=(),
            sample_calendar_reads=(),
            memberships=observation.coordinates.memberships,
            window_rule=None,
        )
        for index, item in enumerate(observation.contributions)
    )
    metrics = tuple((item.metric, item.definition) for item in contributions)
    candidate = BoundGraph(
        replace(graph.root, metrics=metrics),
        replace(
            graph.signature,
            quantities=tuple(BoundQuantity(name, node, None) for name, node in metrics),
            declared_metrics=(),
            observation=replace(observation, contributions=contributions, calendar_samples=()),
        ),
    )
    candidate_sources = prepare_query_sources(
        candidate,
        state,
        prepared.source,
        sources={source.datasource.semantic_id: source for source in prepared.sources.values()},
        consumption_timezone=timezone,
        frame_schemas=frame_schemas,
    )
    candidate_plan = optimize_query(plan_query(candidate, candidate_sources.schemas))
    finish_shape = plan_query(graph, prepared.schemas)
    endpoint = next(
        stage for stage in finish_shape.stages if stage.stage_id == finish_shape.publish
    )
    key_inputs = tuple(stage for stage in finish_shape.stages if isinstance(stage, FrameKeysInput))
    candidate_ids = {stage.stage_id for stage in candidate_plan.stages}
    finish = SourceRead(
        "actual-windows",
        prepared.source.datasource.semantic_id,
        (),
        endpoint.columns,
        endpoint.keys,
        ActualWindowRead(
            graph,
            state,
            timezone,
            tuple(frame_schemas.items()),
            tuple(stage.stage_id for stage in key_inputs),
        ),
        (),
        (),
        None,
        inputs=(candidate_plan.publish, *(stage.stage_id for stage in key_inputs)),
        unique_keys=endpoint.unique_keys,
    )
    stages = (
        *(stage for stage in key_inputs if stage.stage_id not in candidate_ids),
        *candidate_plan.stages,
        finish,
    )
    consumers: dict[str, list[str]] = {stage.stage_id: [] for stage in stages}
    for stage in stages:
        for dependency in stage.inputs:
            consumers[dependency].append(stage.stage_id)
    plan = replace(
        candidate_plan,
        stages=stages,
        consumers=tuple((name, tuple(users)) for name, users in consumers.items()),
        publish=finish.stage_id,
        required_slots=(),
        original=finish_shape.original,
        output_groups=finish_shape.output_groups,
    )
    return lower_query(plan, candidate_sources.relations), replace(
        candidate_sources, source_times=prepared.source_times
    )


def _bind_actual_windows(
    program: ActualWindowRead,
    prepared: PreparedSources,
    axis: CalendarCoordinate,
    points: tuple[object, ...],
) -> BoundGraph:
    graph = program.graph
    observation = graph.signature.observation
    assert isinstance(graph.root, ObserveQuery) and observation is not None
    rules = tuple(
        dict.fromkeys(
            item.window_rule for item in observation.contributions if item.window_rule is not None
        )
    )
    owners = tuple(
        dict.fromkeys(prepared.sources[item.root.ref.path] for item in observation.contributions)
    )
    exact = tuple(sorted({_point(key) for key in points}))
    native = {owner: _actual_positions(axis, owner, exact, rules) for owner in owners}
    display = native[owners[0]][0]
    rebound = _replace_calendar(graph, axis, display)
    assert isinstance(rebound, BoundGraph) and rebound.signature.observation is not None
    contributions = []
    for item in rebound.signature.observation.contributions:
        if item.window_rule is None:
            contributions.append(item)
            continue
        owner = prepared.sources[item.root.ref.path]
        calendar, starts = native[owner]
        period = ObservationPeriod(None, max(cell[3] for cell in calendar.cells), calendar.timezone)
        contributions.extend(
            bind_windows(
                (item,),
                graph.root.metrics,
                dict(graph.signature.declared_metrics),
                period,
                calendar,
                native_starts=starts,
            )
        )
    return replace(
        rebound,
        signature=replace(
            rebound.signature,
            observation=replace(rebound.signature.observation, contributions=tuple(contributions)),
        ),
    )


def _restrict_window_reads(
    prepared: PreparedSources, graph: BoundGraph, candidates: StageOutput
) -> PreparedSources:
    """Join already classified rows to the exact authorized key tuples for each window."""
    observation = graph.signature.observation
    assert observation is not None
    relations = dict(prepared.relations)
    schema_by_path = {(item.source_id, item.occurrence): item for item in prepared.schemas}
    for contribution in observation.contributions:
        root = contribution.root.ref.path
        read_id = "read:" + ".".join(str(index) for index in contribution.occurrence)
        schema = schema_by_path[(root, contribution.occurrence)]
        reduction = reduction_input(contribution, "", None)
        calendar_key = next(
            slot
            for slot, calendar in contribution.calendars
            if isinstance(calendar, CalendarCoordinate) and calendar.actual_only
        )
        allowed = candidates.table
        fields = {slot: path for slot, path in reduction.axes if slot in candidates.keys}
        if contribution.window_key is not None:
            allowed = allowed.loc[allowed[calendar_key] == contribution.window_key]
        else:
            fields[calendar_key] = calendar_field(calendar_key)
        if not fields:
            continue
        names = tuple(slot for slot in candidates.keys if slot in fields)
        key_columns = tuple(column for column in candidates.columns if column.slot_id in fields)
        output = StageOutput.from_frame(allowed.loc[:, list(names)], key_columns, names)
        name = "__marivo_actual_window_keys"
        keys = ibis.table(
            {column.slot_id: ibis_type(column.dtype) for column in key_columns}, name=name
        )
        table = relations[read_id]
        matched = table.semi_join(
            keys,
            [table[schema.field(fields[slot]).column].identical_to(keys[slot]) for slot in names],
        )
        owner = prepared.sources[root]
        relation = (
            owner._bind_key_relation(parameter_data(key_columns, output))
            if owner.provider.name == "duckdb"
            else None
        )
        relations[read_id] = instantiate(
            matched,
            name,
            key_columns,
            output,
            relation=relation,
            provider=owner.provider.name,
            source=owner,
        )
    return replace(prepared, relations=relations)


def run_actual_windows(
    prepared: PreparedSources,
    stage: SourceRead,
    program: ActualWindowRead,
    inputs: tuple[StageOutput, ...],
    *,
    observe_read: Callable[[SourceRead, LoweredRead, StageOutput], None] | None,
) -> StageOutput:
    """Expand W from captured O without replaying Frame keys or widening O."""
    candidates, *key_outputs = inputs
    if candidates.table.empty:
        return StageOutput.from_frame(
            pd.DataFrame(columns=[c.slot_id for c in stage.columns]), stage.columns, stage.keys
        )
    axis = actual_window_axis(program.graph)
    assert axis is not None
    observation = program.graph.signature.observation
    assert observation is not None
    # An identity axis may occupy more than one physical key slot.
    slot = next(key for key, calendar in observation.contributions[0].calendars if calendar == axis)
    graph = _bind_actual_windows(program, prepared, axis, tuple(candidates.table[slot].unique()))
    frame_schemas = dict(program.frame_schemas)
    selected = prepare_query_sources(
        graph,
        program.state,
        prepared.source,
        sources={source.datasource.semantic_id: source for source in prepared.sources.values()},
        consumption_timezone=program.timezone,
        frame_schemas=frame_schemas,
    )
    selected = _restrict_window_reads(selected, graph, candidates)
    plan = optimize_query(plan_query(graph, selected.schemas))
    lowered = lower_query(plan, selected.relations)
    qualify_query_sources(selected, lowered)
    output = execute_numeric_query(
        selected,
        lowered,
        observe_read=observe_read,
        frame_key_inputs=dict(zip(program.key_inputs, key_outputs, strict=True)),
        report_domain=candidates.table,
    )
    return replace(output, receipts=(*candidates.receipts, *output.receipts))
