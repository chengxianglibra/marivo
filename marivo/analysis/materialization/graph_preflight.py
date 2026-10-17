"""Bind semantic Entity outputs and qualify their source operations."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.datasource.adapters import (
    BoundSource,
    SourceSession,
)
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.materializer import materialize_entity_output
from marivo.semantic.validator import Registry


def _local_file(path: str) -> bool:
    file = Path(path)
    return (not urlsplit(path).scheme or bool(file.drive)) and file.is_file()


PhysicalOperation = Literal[
    "scan", "filter", "project", "group", "count", "join", "union", "window", "sort"
]


def relation_operations(relation: ir.Table) -> frozenset[PhysicalOperation]:
    """Return the basic physical operations one bound relation requires."""
    operations: set[PhysicalOperation] = {"scan"}
    for node in relation.op().find(ops.Node):
        if isinstance(node, ops.Filter):
            operations.add("filter")
        elif isinstance(node, ops.Project):
            operations.add("project")
        elif isinstance(node, ops.Aggregate):
            operations.add("group")
        elif isinstance(node, (ops.JoinChain, ops.JoinLink)):
            operations.add("join")
        elif isinstance(node, ops.Union):
            operations.add("union")
        elif isinstance(node, ops.WindowFunction):
            operations.add("window")
        elif isinstance(node, ops.Sort):
            operations.add("sort")
    return frozenset(operations)


def bind_entity_output(
    registry: Registry,
    sidecar: CompiledExpressionSidecar | None,
    entity_id: str,
    source: SourceSession,
    bound: BoundSource,
    fingerprint: str,
) -> ir.Table:
    """Bind and qualify the Entity output without reading business rows."""
    entity = registry.entities[entity_id]
    relation = materialize_entity_output(
        registry=registry,
        sidecar=sidecar,
        entity=entity,
        source_table=bound.relation,
        catalog_definition_fingerprint=fingerprint,
    )
    if sidecar is not None and sidecar.bodies.get(ref.entity(entity_id)) is not None:
        from marivo.datasource.adapters import PhysicalRequirement

        qualified = source.qualify(
            bound, PhysicalRequirement("semantic.entity_output", 1, relation_operations(relation))
        )
        source.compile(
            qualified,
            relation,
            purpose="semantic.entity_schema",
            expected_schema=relation.schema().to_pyarrow(),
        )
    return relation
