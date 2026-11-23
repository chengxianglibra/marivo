"""Discover only source entities required by the captured Query graph."""

from __future__ import annotations

from marivo._definition.expressions import (
    DeclaredMetricRef,
)
from marivo._definition.graph import DefinitionNode, children, computation_fingerprint
from marivo._definition.query import ResultQuery
from marivo._definition.relations import (
    AttributeKeys,
    FrameKeys,
    LiteralPopulation,
    PopulationDependency,
    PopulationProgram,
    ReadQuery,
)
from marivo._definition.types import (
    AxisBinding,
    EntityIdentityRead,
)
from marivo.analysis.binding.coordinates import identity_projection
from marivo.analysis.binding.graph import BoundGraph, read_input_graph
from marivo.analysis.materialization.population_preparation import population_entities
from marivo.analysis.materialization.source_qualification import _route_gap
from marivo.semantic.spi import (
    CompiledSemanticState,
)


def _query_populations(graph: BoundGraph) -> tuple[PopulationDependency, ...]:
    populations: dict[str, PopulationDependency] = {}
    seen: set[int] = set()

    def visit(node: DefinitionNode) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, (PopulationProgram, LiteralPopulation)):
            populations[computation_fingerprint(node)] = node
            if isinstance(node, PopulationProgram) and isinstance(
                node.root, (FrameKeys, AttributeKeys)
            ):
                return
        if isinstance(node, DeclaredMetricRef):
            visit(dict(graph.signature.declared_metrics)[node.ref])
        for edge in children(node):
            visit(edge.child)

    visit(graph.root)
    observation = graph.signature.observation
    if observation is not None:
        if observation.coordinates.population is not None:
            visit(observation.coordinates.population)
        if observation.condition is not None:
            visit(observation.condition)
        if observation.coordinates.parent_scope is not None:
            visit(observation.coordinates.parent_scope)
    return tuple(populations.values())


def _query_entities(graph: BoundGraph, state: CompiledSemanticState) -> tuple[str, ...]:
    """Resolve every required Entity before opening connections."""
    if isinstance(graph.root, ReadQuery):
        return _query_entities(read_input_graph(graph), state)
    if isinstance(graph.root, ResultQuery):
        return tuple(
            dict.fromkeys(
                axis.entity.ref.path
                for axis in graph.signature.axes
                if isinstance(axis, AxisBinding)
            )
        )

    observation = graph.signature.observation
    entities: dict[str, None] = {}
    for contribution in () if observation is None else observation.contributions:
        root = contribution.root.ref.path
        entities[root] = None
        for read in (
            *contribution.reads,
            *contribution.coordinates,
            *(read for _, read in contribution.population_reads),
        ):
            if (
                isinstance(read, EntityIdentityRead)
                and bool(read.components)
                and identity_projection(read, contribution.root, state.registry) is not None
            ):
                continue
            entities[read.entity.ref.path] = None
            for step in read.path:
                relation = state.registry.relationships[step.ref.path]
                entities[relation.from_entity] = None
                entities[relation.to_entity] = None
            if (
                state.registry.entities[root].datasource
                != state.registry.entities[read.entity.ref.path].datasource
            ):
                _route_gap(
                    "scalar relationship endpoints on one datasource",
                    read.entity.ref.path,
                    "Reduce independent datasource roots separately before combining Metrics.",
                )
    for population in _query_populations(graph):
        entities.update(dict.fromkeys(population_entities(population, state)))
    return tuple(entities)
