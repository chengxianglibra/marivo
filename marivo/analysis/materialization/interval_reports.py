"""Resolve a report's interval from its checked output coordinates."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from marivo._definition.graph import computation_fingerprint
from marivo._definition.observations import CoordinateBinding, ObservationBinding, ObservationPeriod
from marivo._definition.relations import ObserveQuery, PopulationDependency
from marivo._definition.types import IntervalCoordinate
from marivo.analysis.binding.windows import bind_windows
from marivo.analysis.compiler.graph_plan import QueryPlan, checked_output
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import BoundSignature, reject
from marivo.analysis.materialization.graph_exchange import StageOutput


def domain_graph(graph: BoundGraph) -> BoundGraph:
    """Keep the exact domain graph, including checks and every Frame-key input."""
    root = graph.root
    assert isinstance(root, ObserveQuery) and root.population is not None
    population = root.population
    axes = population.axes
    observation = ObservationBinding(None, None, CoordinateBinding(axes, "grouped", population), ())
    return BoundGraph(
        ObserveQuery((), axes, root.owner, population),
        BoundSignature(graph.signature.owner, (), axes, observation=observation),
    )


def resolve_interval(graph: BoundGraph, domain: StageOutput) -> BoundGraph:
    """Use only represented ordinal values and preserve their original directory keys."""
    root = graph.root
    observation = graph.signature.observation
    assert isinstance(root, ObserveQuery) and observation is not None
    position = observation.pending_interval
    assert position is not None
    interval = graph.signature.axes[position]
    assert isinstance(interval, IntervalCoordinate)
    (slot,) = domain.key_groups[position]
    selected = tuple(sorted(int(key) for key in domain.table[slot].unique()))
    if len(selected) > 1:
        reject(
            "at most one distinct report interval in observe by",
            repr(tuple(interval.bounds[key] for key in selected[:6])),
            "Observe each interval separately or select a single interval in the Population.",
            "Session.observe.by",
            canonical_id="session.observe",
        )
    if not selected:
        return replace(
            graph,
            signature=replace(
                graph.signature,
                observation=replace(observation, pending_interval=None, empty_interval=True),
            ),
        )
    (key,) = selected
    start, end = interval.bounds[key]
    selected_axis = replace(
        interval, start=start, end=end, intervals=interval.bounds, report_key=key
    )
    period = ObservationPeriod(start, end, interval.timezone)
    axes = (*root.by[:position], selected_axis, *root.by[position + 1 :])
    contributions = bind_windows(
        tuple(
            replace(
                leaf,
                period=period if leaf.time is not None else None,
                calendars=tuple(
                    (slot, selected_axis if candidate is interval else candidate)
                    for slot, candidate in leaf.calendars
                ),
            )
            for leaf in observation.contributions
        ),
        root.metrics,
        dict(graph.signature.declared_metrics),
        period,
        None,
        interval=selected_axis,
    )
    return BoundGraph(
        replace(root, by=axes),
        replace(
            graph.signature,
            axes=axes,
            observation=replace(
                observation,
                period=period,
                coordinates=replace(observation.coordinates, axes=axes),
                contributions=contributions,
                pending_interval=None,
            ),
        ),
    )


def restrict_plan(plan: QueryPlan, available: dict[str, StageOutput], *, empty: bool) -> QueryPlan:
    """Retain only reachable stages and keep prior results out of source lowering."""
    by_id = {stage.stage_id: stage for stage in plan.stages}
    endpoint = by_id[plan.publish]
    if empty:
        receipts = tuple(receipt for output in available.values() for receipt in output.receipts)
        available.clear()
        available[endpoint.stage_id] = StageOutput.from_frame(
            pd.DataFrame(columns=[column.slot_id for column in endpoint.columns]),
            endpoint.columns,
            endpoint.keys,
            receipts=receipts,
        )

    retained = {
        stage.stage_id: checked_output(stage.stage_id, stage.columns, stage.keys)
        if stage.stage_id in available
        else stage
        for stage in plan.stages
    }
    if empty:
        retained = {endpoint.stage_id: retained[endpoint.stage_id]}
    elif plan.original is not None and plan.original.output_population is not None:
        from marivo._definition.expressions import PerReduce, PerWeightedMean
        from marivo._definition.graph import walk
        from marivo._definition.relations import (
            LiteralPopulation,
            PopulationProgram,
            sample_population,
        )
        from marivo.analysis.compiler.graph_plan import LocalCompute

        domain = plan.original.output_population
        descendants = {
            node
            for node in walk((domain,), resolve=lambda _: None).order
            if isinstance(node, (PopulationProgram, LiteralPopulation)) and node != domain
        }
        required_counts = {count.population for count in plan.original.counts}
        sampled = {
            item
            for node in walk(
                (node for _, node in plan.original.outputs),
                resolve=dict(plan.original.declared).get,
            ).order
            if isinstance(node, (PerReduce, PerWeightedMean))
            and (item := sample_population(node.samples)) is not None
        }
        for stage in plan.stages:
            if not isinstance(stage, LocalCompute) or not isinstance(stage.program, FormulaProgram):
                continue
            skipped = {
                binding.stage_id
                for binding in stage.program.populations
                if binding.definition in descendants
                and binding.definition not in required_counts | sampled
            }
            retained[stage.stage_id] = replace(
                stage,
                inputs=tuple(
                    identifier for identifier in stage.inputs if identifier not in skipped
                ),
                program=replace(
                    stage.program,
                    populations=tuple(
                        binding
                        for binding in stage.program.populations
                        if binding.stage_id not in skipped
                    ),
                ),
            )
        live: set[str] = set()

        def visit(identifier: str) -> None:
            if identifier in live:
                return
            live.add(identifier)
            for dependency in retained[identifier].inputs:
                visit(dependency)

        visit(plan.publish)
        retained = {
            identifier: stage for identifier, stage in retained.items() if identifier in live
        }
    return replace(
        plan,
        stages=tuple(retained.values()),
        consumers=tuple(
            (
                identifier,
                tuple(stage.stage_id for stage in retained.values() if identifier in stage.inputs),
            )
            for identifier in retained
        ),
    )


def retain_domain(
    population: PopulationDependency,
    output: StageOutput,
    available: dict[str, StageOutput],
) -> None:
    """Make the checked output domain available under the formula's exact slot layout."""
    identifier = "population:" + computation_fingerprint(population)
    if identifier in available:
        return
    columns = tuple(
        replace(column, slot_id=f"population.{index}")
        for index, column in enumerate(output.columns)
    )
    keys = tuple(column.slot_id for column in columns)
    rows = output.table.copy(deep=False)
    rows.columns = list(keys)
    available[identifier] = StageOutput.from_frame(rows, columns, keys, receipts=output.receipts)


def prune_domain_plan(plan: QueryPlan, population: PopulationDependency) -> QueryPlan:
    """Skip contained stages already checked inside the complete domain's SQL."""
    from marivo.analysis.compiler.graph_plan import LocalCompute

    by_id = {stage.stage_id: stage for stage in plan.stages}
    replacements = {}
    for stage in plan.stages:
        if not isinstance(stage, LocalCompute) or not isinstance(stage.program, FormulaProgram):
            continue
        population_bindings = {binding.definition: binding for binding in stage.program.populations}
        if population not in population_bindings:
            continue
        binding = population_bindings[population]
        # Other Formula inputs in this keys-only plan are artifacts of walking
        # the output definition. Its producer already enforces their checks.
        replacements[stage.stage_id] = replace(
            stage,
            inputs=(binding.stage_id,),
            program=replace(stage.program, populations=(binding,)),
        )
    live: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in live:
            return
        live.add(identifier)
        stage = replacements.get(identifier, by_id[identifier])
        for dependency in stage.inputs:
            visit(dependency)

    visit(plan.publish)
    stages = tuple(
        replacements.get(stage.stage_id, stage) for stage in plan.stages if stage.stage_id in live
    )
    return replace(
        plan,
        stages=stages,
        consumers=tuple(
            (
                stage.stage_id,
                tuple(other.stage_id for other in stages if stage.stage_id in other.inputs),
            )
            for stage in stages
        ),
    )
