"""Capture and plan complete Population dependencies over Query stage outputs."""

from __future__ import annotations

from dataclasses import replace

from marivo._definition.graph import computation_fingerprint, walk
from marivo._definition.observations import CoordinateBinding, ObservationBinding
from marivo._definition.query import PopulationValues, ResultQuery
from marivo._definition.relations import (
    LiteralPopulation,
    ObserveQuery,
    PopulationDependency,
    PopulationFilter,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    QueryKeys,
    condition_populations,
)
from marivo._definition.types import Owner
from marivo.analysis.compiler.graph_plan import LocalCompute, QueryPlan
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.core.data_types import DataColumn
from marivo.analysis.core.graph import BoundGraph, capture_query
from marivo.analysis.core.model import BoundSignature, reject


def population_atoms(definition: PopulationDependency) -> tuple[PopulationDependency, ...]:
    """Keep independent domains intact; split only operations consuming Query keys."""
    if isinstance(definition, LiteralPopulation):
        return (definition,)
    closure = walk((definition,), resolve=lambda _: None)
    if not any(isinstance(node, QueryKeys) for node in closure.order) or isinstance(
        definition.root, QueryKeys
    ):
        return (definition,)
    node = definition.root
    operands: tuple[PopulationDependency, ...]
    if isinstance(node, PopulationSet):
        operands = (node.left, node.right)
    elif isinstance(node, PopulationProduct):
        operands = node.factors
    elif isinstance(node, PopulationFilter):
        operands = (node.operand, *condition_populations(node.predicate))
    elif isinstance(node, PopulationProject):
        operands = (node.operand,)
    else:
        return (definition,)  # Source lookups own their checked Query-key parameters.
    return tuple(
        {
            computation_fingerprint(atom): atom
            for operand in operands
            for atom in population_atoms(operand)
        }.values()
    )


def population_inputs(definition: PopulationDependency, owner: Owner) -> tuple[QueryKeys, ...]:
    result = []
    for atom in population_atoms(definition):
        if isinstance(atom, PopulationProgram) and isinstance(atom.root, QueryKeys):
            result.append(atom.root)
        else:
            binding = ObservationBinding(
                None, None, CoordinateBinding(atom.axes, "grouped", atom, atom.memberships), ()
            )
            graph = BoundGraph(
                ObserveQuery((), atom.axes, owner, atom),
                BoundSignature(owner, (), atom.axes, observation=binding),
            )
            result.append(capture_query(graph))
    return tuple(result)


def plan_population_result(
    graph: BoundGraph, plans: tuple[QueryPlan, ...], inputs: tuple[BoundGraph, ...]
) -> LocalCompute:
    root = graph.root
    assert isinstance(root, ResultQuery) and isinstance(root.operation, PopulationValues)
    from marivo.analysis.compiler.queries import key_groups

    atoms = population_atoms(root.operation.definition)
    layouts = []
    stages = []
    for plan, child in zip(plans, inputs, strict=True):
        stage = next(item for item in plan.stages if item.stage_id == plan.publish)
        stages.append(stage)
        layouts.append(
            stage.result.output_groups
            if isinstance(stage, LocalCompute) and stage.result is not None
            else key_groups(child, stage.columns, stage.keys)
        )
    columns: list[DataColumn] = []
    output_groups = []
    for axis in graph.signature.axes:
        found = next(
            (
                (stage, groups[child.signature.axes.index(axis)])
                for stage, child, groups in zip(stages, inputs, layouts, strict=True)
                if axis in child.signature.axes
            ),
            None,
        )
        if found is None:
            reject(
                "a typed input for every output axis",
                repr(axis),
                "Retain complete key schemas across the Population dependency.",
                "Population.prepare",
            )
        stage, group = found
        output = []
        for key in group:
            slot = f"key.{len(columns)}"
            columns.append(
                replace(
                    next(column for column in stage.columns if column.slot_id == key), slot_id=slot
                )
            )
            output.append(slot)
        output_groups.append(tuple(output))
    return LocalCompute(
        "local:population:" + graph.fingerprint,
        tuple(plan.publish for plan in plans),
        "query.result@v1",
        tuple(columns),
        tuple(column.slot_id for column in columns),
        result=ResultProgram(
            root.operation,
            (),
            (),
            (),
            (),
            tuple(output_groups),
            population_inputs=atoms,
            population_layouts=tuple(layouts),
        ),
    )
