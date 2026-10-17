"""Prepare new-attribute dependencies after binding the original fine program."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import ibis.expr.types as ir

from marivo._definition.expressions import (
    EntityCount,
    MemberCount,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import walk
from marivo._definition.query import DrillValues, LiteralValues, ResultQuery, operation_dependencies
from marivo._definition.types import AxisBinding, ExactRef
from marivo.analysis.compiler.graph_plan import SourceRead, SourceSchema, plan_query
from marivo.analysis.compiler.original import contribution_root
from marivo.analysis.compiler.queries import original_program, prefixed_input
from marivo.analysis.compiler.query_program import QueryContext
from marivo.analysis.core.graph import BoundGraph, input_graph
from marivo.analysis.core.model import BoundQuantity, reject
from marivo.analysis.materialization.query_drill import drill_entities, prepare_drill
from marivo.semantic._compiled_state import CompiledSemanticState

if TYPE_CHECKING:
    from marivo.analysis.compiler.optimization import InvocationMemo


def query_roots(graph: BoundGraph) -> tuple[ExactRef, ...]:
    nodes = tuple(
        quantity.definition
        for quantity in graph.signature.quantities
        if isinstance(quantity, BoundQuantity)
    )
    closure = walk(nodes, resolve=dict(graph.signature.declared_metrics).get).order
    roots = tuple(
        contribution_root(node)
        for node in closure
        if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount))
    )
    if any(isinstance(node, MemberCount) for node in closure):
        source = graph
        while isinstance(source.root, ResultQuery):
            source = input_graph(source.root.inputs[0])
        observation = source.signature.observation
        if observation is not None and observation.members is not None:
            roots = (
                *roots,
                *(
                    axis.entity
                    for axis in observation.members.axes
                    if isinstance(axis, AxisBinding) and axis.field is None
                ),
            )
    return tuple(dict.fromkeys(roots))


def extra_entities(graph: BoundGraph, state: CompiledSemanticState) -> tuple[str, ...]:
    if not isinstance(graph.root, ResultQuery):
        return ()
    result = tuple(
        path
        for captured in (*graph.root.inputs, *operation_dependencies(graph.root.operation))
        for path in extra_entities(input_graph(captured), state)
    )
    if isinstance(graph.root.operation, LiteralValues):
        result = (
            *result,
            *(
                axis.entity.ref.path
                for axis in graph.signature.axes
                if isinstance(axis, AxisBinding)
            ),
        )
    if isinstance(graph.root.operation, DrillValues):
        for root in query_roots(input_graph(graph.root.inputs[0])):
            paths = drill_entities((root,), graph.root.operation.added, state)
            if any(
                state.registry.entities[path].datasource
                != state.registry.entities[root.ref.path].datasource
                for path in paths
            ):
                reject(
                    "one qualified datasource per scalar classification path",
                    repr(paths),
                    "Classify independent datasource roots separately before combining outputs.",
                    "Query.drill",
                )
            result = (*result, *paths)
    return tuple(dict.fromkeys(result))


def prepare_drills(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    context: QueryContext,
    entities: dict[str, ir.Table],
    state: CompiledSemanticState,
    prefix: tuple[int, ...] = (),
    memo: InvocationMemo | None = None,
) -> tuple[QueryContext, dict[str, ir.Table]]:
    if not isinstance(graph.root, ResultQuery):
        return context, {}
    relations: dict[str, ir.Table] = {}
    for index, captured in enumerate(
        (*graph.root.inputs, *operation_dependencies(graph.root.operation))
    ):
        child = prefixed_input(captured, (*prefix, index))
        context, ready = prepare_drills(
            child, schemas, context, entities, state, (*prefix, index), memo
        )
        relations.update(ready)
    if isinstance(graph.root.operation, DrillValues):
        parent = prefixed_input(graph.root.inputs[0], (*prefix, 0))
        plan = plan_query(
            parent, schemas, prefix=(*prefix, 0), context=context, require_original=True, memo=memo
        )
        original = original_program(plan)
        if original is None:
            reject(
                "the original Metric program",
                "no sufficient original input",
                "Drill a source Query retaining original quantities.",
                "Query.drill",
            )
        prepared = prepare_drill(
            graph.fingerprint,
            original,
            tuple(stage for stage in plan.stages if isinstance(stage, SourceRead)),
            graph.root.operation.added,
            entities,
            state,
        )
        context = replace(context, drills=(*context.drills, (graph.fingerprint, prepared.schemas)))
        relations.update(prepared.relations)
    return context, relations
