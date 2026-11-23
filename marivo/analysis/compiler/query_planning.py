"""Select a bound Query shape and coordinate its planning."""

from __future__ import annotations

from marivo._definition.query import ResultQuery
from marivo._definition.relations import ReadQuery
from marivo.analysis.binding.graph import BoundGraph, read_input_graph
from marivo.analysis.compiler.graph_plan import with_unique_keys
from marivo.analysis.compiler.optimization import InvocationMemo, compilation_key, retain_components
from marivo.analysis.compiler.parameters import plan_parameters
from marivo.analysis.compiler.queries import plan_literal, plan_read
from marivo.analysis.compiler.query_stages import QueryPlan, SourceSchema


def plan_query(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    *,
    require_original: bool = False,
    memo: InvocationMemo | None = None,
    prefix: tuple[int, ...] = (),
) -> QueryPlan:
    """Plan dependency producers before every parameterized source consumer."""
    if memo is not None:
        return memo.plan(
            compilation_key(
                graph,
                schemas,
                required=("prefix:" + repr(prefix),),
                require_original=require_original,
            ),
            lambda: _plan_bound_query(
                graph,
                schemas,
                require_original=require_original,
                memo=memo,
                prefix=prefix,
            ),
        )
    return _plan_bound_query(
        graph,
        schemas,
        require_original=require_original,
        memo=None,
        prefix=prefix,
    )


def _plan_bound_query(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    *,
    require_original: bool,
    memo: InvocationMemo | None,
    prefix: tuple[int, ...],
) -> QueryPlan:

    if isinstance(graph.root, ResultQuery):
        return with_unique_keys(plan_literal(graph, graph.root.operation, schemas))
    if isinstance(graph.root, ReadQuery):
        source = read_input_graph(graph)
        return with_unique_keys(plan_read(graph, source, plan_query(source, schemas, memo=memo)))

    planned = plan_parameters(graph, schemas, memo=memo)
    if require_original:
        return with_unique_keys(retain_components(planned, schemas))
    return with_unique_keys(planned)
