"""Retain original Population check sources when a membership product is unused."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from marivo._definition.relations import PopulationDependency, condition_populations
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.lowered_query import LoweredQuery
from marivo.analysis.compiler.optimization import required_slots
from marivo.analysis.compiler.query_stages import LocalCompute, SourceReadStage, read_members
from marivo.analysis.materialization.query_plan_transport import _consumers

if TYPE_CHECKING:
    from marivo.analysis.materialization.prepared_sources import PreparedSources


def carrier_condition_domains(
    prepared: PreparedSources, stage: SourceReadStage, program: FormulaProgram
) -> tuple[PopulationDependency, ...]:
    if program.output_population is not None:
        return (program.output_population,)
    graph = prepared.graph
    observation = None if graph is None else graph.signature.observation
    if observation is None:
        return ()
    occurrences = {stage.occurrence, *(member.occurrence for member in read_members(stage))}
    return tuple(
        domain
        for contribution in observation.contributions
        if contribution.occurrence in occurrences
        for condition in (*contribution.intrinsic_conditions, contribution.common_condition)
        if condition is not None
        for domain in condition_populations(condition)
    )


def retain_factor_checks(
    lowered: LoweredQuery,
    candidate: LocalCompute,
    program: FormulaProgram,
    products: frozenset[str],
) -> LoweredQuery:
    updated = replace(
        program,
        populations=tuple(item for item in program.populations if item.stage_id not in products),
    )
    compute = replace(
        candidate,
        inputs=tuple(identifier for identifier in candidate.inputs if identifier not in products),
        program=updated,
    )
    stages = tuple(
        compute if stage.stage_id == candidate.stage_id else stage
        for stage in lowered.plan.stages
        if stage.stage_id not in products
    )
    plan = replace(
        lowered.plan,
        stages=stages,
        consumers=_consumers(stages),
        original=updated if lowered.plan.original == program else lowered.plan.original,
    )
    return LoweredQuery(
        replace(plan, required_slots=required_slots(plan)),
        tuple(read for read in lowered.reads if read.stage_id not in products),
    )
