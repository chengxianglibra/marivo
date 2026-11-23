"""Retain Query consumers, checked transport and host-owned selections."""

from __future__ import annotations

from dataclasses import replace

from marivo._definition.expressions import (
    MemberCount,
    PerReduce,
    PerWeightedMean,
)
from marivo._definition.graph import walk
from marivo._definition.query import (
    FilterValues,
    PopulationValues,
    TopValues,
)
from marivo._definition.relations import (
    PopulationDependency,
    PopulationProgram,
    sample_population,
)
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.compiler.query_stages import (
    LocalCompute,
    QueryPlan,
    QueryStage,
    SourceReadStage,
    read_members,
)


def _consumers(stages: tuple[QueryStage, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (stage.stage_id, tuple(item.stage_id for item in stages if stage.stage_id in item.inputs))
        for stage in stages
    )


def _prune_transport(plan: QueryPlan, producer: str) -> tuple[QueryPlan, tuple[str, ...]]:
    """Drop formula Population inputs that supply no output, member or sample domain."""
    removed: list[str] = []
    for original in plan.stages:
        if not isinstance(original, LocalCompute) or not isinstance(
            original.program, FormulaProgram
        ):
            continue
        program = original.program
        if program.output_population is not None or (bool(program.counts) and original.keys):
            continue
        closure = walk((node for _, node in program.outputs), resolve=dict(program.declared).get)
        if any(isinstance(node, MemberCount) for node in closure.order):
            continue
        sample_domains = tuple(
            domain
            for node in closure.order
            if isinstance(node, (PerReduce, PerWeightedMean))
            and isinstance(domain := sample_population(node.samples), PopulationProgram)
        )
        by_id = {stage.stage_id: stage for stage in plan.stages}
        redundant = tuple(
            binding.stage_id
            for binding in program.populations
            if binding.definition not in sample_domains
            and isinstance((stage := by_id[binding.stage_id]), LocalCompute)
            and isinstance(stage.program, ResultProgram)
            and isinstance(stage.program.operation, PopulationValues)
            and producer in stage.inputs
            and plan.consumers_of(stage.stage_id) == (original.stage_id,)
        )
        if not redundant:
            continue
        compute = replace(
            original,
            inputs=tuple(value for value in original.inputs if value not in redundant),
            program=replace(
                program,
                counts=() if not original.keys else program.counts,
                populations=tuple(
                    value for value in program.populations if value.stage_id not in redundant
                ),
            ),
        )
        stages = tuple(
            compute if stage.stage_id == original.stage_id else stage
            for stage in plan.stages
            if stage.stage_id not in redundant
        )
        plan = replace(plan, stages=stages, consumers=_consumers(stages))
        removed.extend(redundant)
    return plan, tuple(removed)


def _host_domain_selections(plan: QueryPlan) -> frozenset[str]:
    """Keep selections whose completed keys supply a real host Population domain."""
    by_id = {stage.stage_id: stage for stage in plan.stages}
    visited: set[str] = set()
    selected: set[str] = set()

    def include(identifier: str) -> None:
        if identifier in visited:
            return
        visited.add(identifier)
        stage = by_id[identifier]
        if (
            isinstance(stage, LocalCompute)
            and isinstance(stage.program, ResultProgram)
            and isinstance(stage.program.operation, (FilterValues, TopValues))
        ):
            selected.add(identifier)
        for dependency in stage.inputs:
            include(dependency)

    for stage in plan.stages:
        if not isinstance(stage, LocalCompute) or not isinstance(stage.program, FormulaProgram):
            continue
        program = stage.program
        closure = walk((node for _, node in program.outputs), resolve=dict(program.declared).get)
        domains: list[PopulationDependency] = [
            domain
            for node in closure.order
            if isinstance(node, (PerReduce, PerWeightedMean))
            and isinstance(domain := sample_population(node.samples), PopulationProgram)
        ]
        if program.output_population is not None:
            domains.append(program.output_population)
        if bool(program.counts):
            domains.extend(count.population for count in program.counts)
        for binding in program.populations:
            if binding.definition in domains:
                include(binding.stage_id)
    return frozenset(selected)


def _logical_reads(reads: tuple[SourceReadStage, ...]) -> tuple[SourceReadStage, ...]:
    logical: dict[str, SourceReadStage] = {}
    for read in reads:
        for item in (
            _logical_reads(read.native_reads or read_members(read))
            if (read.native_reads or read_members(read))
            else (read,)
        ):
            logical.setdefault(item.stage_id, item)
    return tuple(logical.values())
