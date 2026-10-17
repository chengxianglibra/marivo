"""Prepare explicit declared correspondence inputs from the completed scope."""

from marivo._definition.correspondence import DeclaredCorrespondence
from marivo._definition.expressions import EntityCount
from marivo._definition.query import CombineValues, ResultQuery, operation_dependencies
from marivo._definition.relations import ObserveQuery
from marivo._definition.types import AxisBinding, IdentityRead
from marivo.analysis.binding.correspondence import bind_declared_relation
from marivo.analysis.binding.observe import bind_observation
from marivo.analysis.compiler.query_program import QueryContext, RelationInput
from marivo.analysis.core.graph import BoundGraph, capture_query, input_graph
from marivo.analysis.core.model import BoundQuantity, BoundSignature, reject
from marivo.semantic._compiled_state import CompiledSemanticState


def prepare_context(graph: BoundGraph, state: CompiledSemanticState) -> QueryContext:
    """Bind relationship definitions without schema or business reads."""
    if not isinstance(graph.root, ResultQuery):
        return QueryContext()
    relations = [
        item
        for child in (*graph.root.inputs, *operation_dependencies(graph.root.operation))
        for item in prepare_context(input_graph(child), state).relations
    ]
    operation = graph.root.operation
    if isinstance(operation, CombineValues) and isinstance(operation.match, DeclaredCorrespondence):
        left, right = graph.root.inputs[:2]
        identity_axes = tuple(
            tuple(
                axis
                for axis in endpoint.axes
                if isinstance(axis, AxisBinding) and axis.field is None
            )
            for endpoint in (left, right)
        )
        if any(len(axes) != 1 for axes in identity_axes):
            reject(
                "one identity axis on each declared correspondence endpoint",
                repr(identity_axes),
                "Project the intended identity roles before corresponding.",
                "Query.corresponding",
            )
        a, b = (axes[0] for axes in identity_axes)
        relation = bind_declared_relation(a, b, operation.match, state.registry)
        query = None
        if not relation.direct:
            node = EntityCount(a.entity, None)
            binding = bind_observation(
                (("mapping", node),),
                {},
                during=None,
                by=(
                    IdentityRead(a.entity, a.path, a.at),
                    IdentityRead(b.entity, tuple(step.ref for step in operation.match.via), b.at),
                ),
                where=None,
                timezone="UTC",
                state=state,
            )
            mapping = BoundGraph(
                ObserveQuery((("mapping", node),), binding.coordinates.axes, graph.signature.owner),
                BoundSignature(
                    graph.signature.owner,
                    (BoundQuantity("mapping", node, None),),
                    binding.coordinates.axes,
                    observation=binding,
                ),
            )
            query = capture_query(mapping)
        relations.append(RelationInput(relation, query))
    return QueryContext(tuple(dict.fromkeys(relations)))
