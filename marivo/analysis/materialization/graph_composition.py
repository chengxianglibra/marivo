"""Ordered source endpoint composition with exact frozen node sharing."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo.analysis.core.graph import Edge, MethodNode, SourceLeaf, method_node, topology
from marivo.analysis.core.model import DomainSignature, ObservedQuantity
from marivo.analysis.core.rules import (
    AssociationScore,
    CellDerive,
    ObserveCount,
    ObserveMetric,
    OriginalRatio,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.graph_members import MemberGraph
from marivo.analysis.materialization.graph_protocol import NODE, digest, encode
from marivo.analysis.methods.physical import ScalarType


def combine_observations(
    current: MemberGraph,
    baseline: MemberGraph,
    method: Literal["difference", "spearman", "ratio"],
    *,
    ratio: ObservedQuantity | None = None,
) -> MemberGraph:
    """Share identical frozen nodes, preserving independent ordered endpoints."""

    def invalid(received: str) -> DatasetConstructionError:
        return DatasetConstructionError(
            expected="two source observations over one exact member realization",
            received=received,
            repair="Build both observations from the same member domain.",
            location="analysis.graph_composition",
        )

    if current.runtime is not baseline.runtime or current.registry is not baseline.registry:
        raise invalid("different Session or Semantic binding")
    known = {node.identity: node for node in topology(current.root)}
    for node in topology(baseline.root):
        prior = known.get(node.identity)
        if prior is not None:
            if encode(prior, NODE) != encode(node, NODE):
                raise invalid("one node identity has different frozen definitions")
            continue
        if isinstance(node, MethodNode):
            sources = tuple(known[source.identity] for source in node.sources)
            if any(not isinstance(source, SourceLeaf) for source in sources):
                raise invalid("non-source dependency in a source node")
            node = replace(
                node,
                inputs=tuple(Edge(edge.role, known[edge.node.identity]) for edge in node.inputs),
                sources=tuple(source for source in sources if isinstance(source, SourceLeaf)),
            )
        known[node.identity] = node
    first, second = current.root, known[baseline.root.identity]
    quantity = first.signature.quantity
    if quantity is None or second.signature.quantity is None:
        raise invalid("an endpoint has no observation quantity")
    if first.signature.domain.binding != second.signature.domain.binding:
        raise invalid("endpoints do not share the exact frozen member binding")
    if (
        not isinstance(second, MethodNode)
        or not isinstance(first.parameters, (ObserveMetric, ObserveCount))
        or not isinstance(second.parameters, (ObserveMetric, ObserveCount))
    ):
        raise invalid("endpoints are not exact original observations")
    left_member, right_member = first.inputs[0].node, second.inputs[0].node
    if (
        left_member.identity != right_member.identity
        or left_member.fingerprint != right_member.fingerprint
    ):
        raise invalid("endpoints do not share the same frozen member node")
    if method == "difference" and (
        first.parameters.path != second.parameters.path
        or first.parameters.coordinates != second.parameters.coordinates
    ):
        raise invalid("comparison endpoints have different routes or coordinates")
    if method == "difference":
        other_quantity = second.signature.quantity
        if (
            not isinstance(quantity, ObservedQuantity)
            or not isinstance(other_quantity, ObservedQuantity)
            or quantity.metric_ref != other_quantity.metric_ref
            or quantity.graph_fingerprint != other_quantity.graph_fingerprint
            or quantity.time_scope == other_quantity.time_scope
        ):
            raise invalid("comparison requires one original Metric and distinct windows")
    definition = digest(method + first.fingerprint + second.fingerprint)
    if method == "ratio":
        if (
            ratio is None
            or not isinstance(quantity, ObservedQuantity)
            or not isinstance(second.signature.quantity, ObservedQuantity)
        ):
            raise invalid("missing exact original ratio definition")
        root = method_node(
            (Edge("quantity", first), Edge("quantity", second)),
            OriginalRatio(ratio, quantity.metric_ref, second.signature.quantity.metric_ref),
            value_type=ScalarType("float64"),
        )
    elif method == "difference":
        root = method_node(
            (Edge("current", first), Edge("baseline", second)),
            CellDerive(
                "difference",
                definition,
                "strict",
                quantity.unit,
                quantity.time_scope,
                "source.exact_pairing@v1",
                "source.finite_numeric@v1",
            ),
            value_type=first.value_type,
        )
    else:
        domain = DomainSignature(first.signature.domain.binding, "singleton", (), (), definition)
        root = method_node(
            (Edge("quantity", first), Edge("quantity", second)),
            AssociationScore(
                domain, definition, "source.exact_pairing@v1", "source.finite_numeric@v1"
            ),
            value_type=ScalarType("float64"),
        )
    schemas = {leaf.identity: schema for schema, leaf in (*current.sources, *baseline.sources)}
    source_nodes = tuple(node for node in topology(root) if isinstance(node, SourceLeaf))
    bindings = tuple((schemas[node.identity], node) for node in source_nodes)
    return replace(current, root=root, sources=bindings)
