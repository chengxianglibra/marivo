"""Bind finite literal coordinate carriers to the real schema without business reads."""

from __future__ import annotations

from collections.abc import Mapping

import ibis.expr.types as ir

from marivo._definition.expressions import EntityCount
from marivo._definition.observations import ContributionBinding
from marivo._definition.query import LiteralValues, ResultQuery, operation_dependencies
from marivo._definition.types import AxisBinding, FieldRead, field_read_key
from marivo.analysis.compiler.graph_plan import SourceField, SourceSchema
from marivo.analysis.compiler.observation import data_type
from marivo.analysis.core.graph import BoundGraph, input_graph
from marivo.analysis.materialization.query_preparation import prepare_fields
from marivo.datasource.timezone import probe_engine_timezone
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic.validator import normalize_target_entity


def literal_schemas(
    graph: BoundGraph, entities: Mapping[str, ir.Table], state: CompiledSemanticState
) -> tuple[SourceSchema, ...]:
    """Prepare coordinate type facts while leaving literal value kinds independent."""
    if not isinstance(graph.root, ResultQuery):
        return ()
    result = tuple(
        schema
        for captured in (*graph.root.inputs, *operation_dependencies(graph.root.operation))
        for schema in literal_schemas(input_graph(captured), entities, state)
    )
    if not isinstance(graph.root.operation, LiteralValues) or not graph.signature.axes:
        return result
    keys: list[SourceField] = []
    groups = []
    for axis in graph.signature.axes:
        assert isinstance(axis, AxisBinding)
        table = entities[axis.entity.ref.path]
        if axis.field is None:
            native = normalize_target_entity(state.registry, axis.entity.ref.path).primary_key
            typed = tuple((name, data_type(table[name].type())) for name in native)
        else:
            read = FieldRead(axis.field, axis.entity, (), axis.at)
            contribution = ContributionBinding(
                (), EntityCount(axis.entity, None), axis.entity, (), None, (read,), (), None, None
            )
            prepared = prepare_fields(
                contribution, entities, state, probe_engine_timezone(table._find_backend())
            )
            typed = (
                (
                    axis.field.ref.path,
                    data_type(prepared.table[dict(prepared.columns)[field_read_key(read)]].type()),
                ),
            )
        group = []
        for name, dtype in typed:
            slot = f"key.{len(keys)}"
            keys.append(SourceField(name, slot, dtype, axis.field is not None))
            group.append(slot)
        groups.append(tuple(group))
    return (
        *result,
        SourceSchema("literal:" + graph.fingerprint, tuple(keys), (), key_groups=tuple(groups)),
    )
