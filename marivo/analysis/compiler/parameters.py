"""Plan checked Query-key producers and typed source parameter consumers."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from marivo._definition.graph import DefinitionNode, children, computation_fingerprint
from marivo._definition.query import KeyImage, PopulationValues
from marivo._definition.relations import PopulationProgram, QueryKeys
from marivo.analysis.compiler.graph_plan import (
    LocalCompute,
    QueryPlan,
    SourceCheckRoute,
    SourceRead,
    SourceSchema,
    _plan_query,
    plan_query,
)
from marivo.analysis.compiler.query_program import (
    EMPTY_QUERY_CONTEXT,
    QueryContext,
    ResultProgram,
    SourceParameter,
)
from marivo.analysis.core.graph import BoundGraph, input_graph

if TYPE_CHECKING:
    from marivo.analysis.compiler.optimization import InvocationMemo


def parameter_domains(graph: BoundGraph) -> tuple[PopulationProgram, ...]:
    """Visit only the receiving graph; captured producer graphs own their closures."""
    result: dict[str, PopulationProgram] = {}
    seen: set[int] = set()

    def visit(node: DefinitionNode) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, PopulationProgram) and isinstance(node.root, QueryKeys):
            result[computation_fingerprint(node)] = node
            return
        for edge in children(node):
            visit(edge.child)

    visit(graph.root)
    observation = graph.signature.observation
    if observation is not None and observation.coordinates.population is not None:
        visit(observation.coordinates.population)
    return tuple(result.values())


def plan_parameters(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    checks: tuple[SourceCheckRoute, ...],
    context: QueryContext = EMPTY_QUERY_CONTEXT,
    *,
    memo: InvocationMemo | None = None,
) -> QueryPlan:
    from marivo._definition.relations import ObserveQuery

    assert isinstance(graph.root, ObserveQuery)
    own = tuple(schema for schema in schemas if schema.scope_id in ("", graph.fingerprint))
    parameters = {
        parameter.placeholder: parameter for schema in own for parameter in schema.parameters
    }
    base = _plan_query(graph, own, checks=checks)
    output = next(stage for stage in base.stages if stage.stage_id == base.publish)
    if graph.root.population is not None:
        schema = next(
            schema
            for schema in own
            if schema.source_id == "population:" + computation_fingerprint(graph.root.population)
        )
        groups_list = []
        offset = 0
        for group in schema.key_groups:
            groups_list.append(output.keys[offset : offset + len(group)])
            offset += len(group)
        groups = tuple(groups_list)
    else:
        from marivo.analysis.compiler.queries import key_groups

        groups = key_groups(graph, output.columns, output.keys)
    base = replace(base, output_groups=groups)
    if not parameters:
        return base
    producers = {
        name: plan_query(input_graph(parameter.query), schemas, context=context, memo=memo)
        for name, parameter in parameters.items()
    }
    stages = {stage.stage_id: stage for plan in producers.values() for stage in plan.stages}
    for stage in base.stages:
        if not isinstance(stage, SourceRead):
            stages[stage.stage_id] = stage
            continue
        schema = next(
            schema
            for schema in own
            if schema.source_id == stage.source_id
            and (schema.occurrence is None or schema.occurrence == stage.occurrence)
        )
        if schema.inline_population is not None:
            input_plans = tuple(producers[parameter.placeholder] for parameter in schema.parameters)
            output_groups = tuple(
                tuple(
                    stage.keys[tuple(key.column for key in schema.keys).index(column)]
                    for column in group
                )
                for group in schema.key_groups
            )
            stages[stage.stage_id] = LocalCompute(
                stage.stage_id,
                tuple(plan.publish for plan in input_plans),
                "query.result@v1",
                stage.columns,
                stage.keys,
                result=ResultProgram(
                    PopulationValues(schema.inline_population),
                    (),
                    (),
                    (),
                    (),
                    output_groups,
                    population_inputs=tuple(
                        parameter.definition for parameter in schema.parameters
                    ),
                    population_layouts=tuple(plan.output_groups for plan in input_plans),
                ),
            )
            continue
        direct = next(
            (
                parameter
                for parameter in schema.parameters
                if stage.source_id == "population:" + computation_fingerprint(parameter.definition)
            ),
            None,
        )
        if direct is not None:
            producer = producers[direct.placeholder]
            output = next(item for item in producer.stages if item.stage_id == producer.publish)
            from marivo.analysis.compiler.queries import key_groups

            input_definition = input_graph(direct.query)
            groups = (
                output.result.output_groups
                if isinstance(output, LocalCompute) and output.result is not None
                else key_groups(input_definition, output.columns, output.keys)
            )
            output_groups = tuple(
                tuple(
                    stage.keys[tuple(key.column for key in schema.keys).index(column)]
                    for column in group
                )
                for group in schema.key_groups
            )
            stages[stage.stage_id] = LocalCompute(
                stage.stage_id,
                (producer.publish,),
                "query.result@v1",
                stage.columns,
                stage.keys,
                result=ResultProgram(
                    KeyImage(),
                    output.columns,
                    output.keys,
                    input_definition.signature.axes,
                    groups,
                    output_groups,
                ),
            )
            continue
        bindings = tuple(
            SourceParameter(
                producers[parameter.placeholder].publish, parameter.placeholder, parameter.columns
            )
            for parameter in schema.parameters
        )
        stages[stage.stage_id] = replace(
            stage,
            inputs=tuple(dict.fromkeys(binding.stage_id for binding in bindings)),
            parameters=bindings,
        )
    ordered = tuple(stages.values())
    consumers = tuple(
        (stage.stage_id, tuple(item.stage_id for item in ordered if stage.stage_id in item.inputs))
        for stage in ordered
    )
    return QueryPlan(
        ordered,
        (*tuple(check for plan in producers.values() for check in plan.checks), *base.checks),
        consumers,
        base.publish,
        base.output_groups,
        base.original,
    )
