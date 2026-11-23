"""Schema-only Query-key templates and invocation-local parameter relations."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from math import isfinite
from typing import TYPE_CHECKING

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.schema as sch
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa

from marivo._definition.graph import computation_fingerprint
from marivo._definition.relations import AttributeKeys, FrameKeys
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.compiler.parameters import parameter_domains
from marivo.analysis.compiler.populations import frame_key_columns
from marivo.analysis.compiler.query_program import (
    FrameParameter,
    PopulationParameterPlan,
    SourceParameter,
)
from marivo.analysis.compiler.source_predicates import (
    distinct_returned_keys,
    source_parameter_comparisons,
)
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.value_types import ibis_type
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.data_types import (
    DataColumn,
    DecimalDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.model.errors import reject
from marivo.datasource.spi import KeyRows, calendar_data, query_policy

if TYPE_CHECKING:
    from marivo.analysis.compiler.optimization import InvocationMemo
    from marivo.analysis.materialization.prepared_sources import PreparedSources
    from marivo.datasource.spi import SourceSession
    from marivo.semantic.spi import CompiledSemanticState


def prepare_frame_parameters(
    graph: BoundGraph,
    frame_schemas: Mapping[str, DataSchema],
    entities: dict[str, ir.Table],
) -> tuple[FrameParameter, ...]:
    """Declare complete Frame-key relations using prepared input schemas only."""

    result = []
    for domain in parameter_domains(graph):
        if not isinstance(domain.root, (FrameKeys, AttributeKeys)):
            continue
        schema = frame_schemas.get(domain.root.input_id)
        if schema is None:
            reject(
                "a prepared schema for every retained Frame key input",
                domain.root.input_id,
                "Retain the input sidecar before capturing the source graph.",
                "frame.population",
            )
        columns = frame_key_columns(domain.root, schema)
        identifier = computation_fingerprint(domain)
        placeholder = "__marivo_population_" + identifier
        table = ibis.table(
            {column.slot_id: ibis_type(column.dtype) for column in columns}
            if columns
            else {"__population_unit": dt.boolean},
            name=placeholder,
        )
        entities["population-parameter:" + identifier] = table
        result.append(FrameParameter(domain, placeholder, columns))
    return tuple(result)


def prepare_parameters(
    graph: BoundGraph,
    state: CompiledSemanticState,
    source: SourceSession,
    sources: dict[str, SourceSession],
    entities: dict[str, ir.Table],
    memo: InvocationMemo | None = None,
    frame_schemas: Mapping[str, DataSchema] | None = None,
) -> tuple[tuple[PopulationParameterPlan, ...], tuple[PreparedSources, ...]]:
    return prepare_frame_parameters(graph, frame_schemas or {}, entities), ()


def parameter_data(columns: tuple[DataColumn, ...], output: StageOutput) -> pa.Table:
    """Encode checked complete keys with the planned names and physical types."""
    keys = output.table.loc[:, list(output.keys)].copy(deep=False)
    keys.columns = [column.slot_id for column in columns]
    schema = pa.schema(
        [
            pa.field(column.slot_id, stage_arrow_type(column.dtype), nullable=column.nullable)
            for column in columns
        ]
    )
    return pa.Table.from_pandas(keys, schema=schema, preserve_index=False, safe=True)


def instantiate(
    expression: ir.Table,
    name: str,
    columns: tuple[DataColumn, ...],
    output: StageOutput,
    *,
    relation: ir.Table | None = None,
    provider: str | None = None,
    source: SourceSession | None = None,
) -> ir.Table:
    """Substitute an owned relation, or instantiate admitted inline key tuples."""
    if relation is not None:
        # Arrow preserves physical nonnullability. The placeholder's nullable
        # relation schema must stay stable while sibling set operands are
        # substituted one at a time; the checked Arrow input retains its own
        # stricter null contract.
        relation = relation.select(
            **{
                column.slot_id: relation[column.slot_id].cast(ibis_type(column.dtype))
                for column in columns
            }
        )
        return _replace_parameter(expression, name, relation)
    if not columns:
        unit = ibis.literal(True).name("__population_unit").as_table()
        return _replace_parameter(
            expression, name, unit if len(output.table) else unit.filter(ibis.literal(False))
        )

    if provider is not None:
        expression = source_parameter_comparisons(expression, name, provider)
    policy = query_policy(provider)
    records = tuple(output.table.loc[:, list(output.keys)].itertuples(index=False, name=None))

    if (
        any(isinstance(column.dtype, TimestampDataType) for column in columns)
        and all(
            isinstance(column.dtype, (ScalarDataType, DecimalDataType))
            or column.dtype == TimestampDataType("us", "UTC")
            for column in columns
        )
        # JSON has no nonfinite numbers. Retain the existing generic key path
        # when a mixed coordinate needs a native infinity, rather than rejecting it.
        and (
            policy.accepts_nonfinite_inline_time_keys
            or not any(
                isinstance(value, float) and not isfinite(value) for row in records for value in row
            )
        )
    ):
        records = tuple(
            tuple(row[column.slot_id] for column in columns)
            for row in parameter_data(columns, output).to_pylist()
        )
        literal = calendar_data(
            ibis.schema({column.slot_id: ibis_type(column.dtype) for column in columns}), records
        )
        return _replace_parameter(expression, name, literal)

    if policy.bind_key_membership is not None:
        # Native readers can return nullable integer keys as unambiguous floats.
        # Normalize against the planned key schema before forming JSON names.
        records = tuple(
            tuple(row[column.slot_id] for column in columns)
            for row in parameter_data(columns, output).to_pylist()
        )
    schema = ibis.schema({column.slot_id: ibis_type(column.dtype) for column in columns})
    if policy.inline_key_relation is not None:
        normalized = tuple(
            tuple(None if pd.isna(value) else value for value in row) for row in records
        )
        inline = policy.inline_key_relation(schema, normalized)
        if inline is not None:
            return _replace_parameter(expression, name, inline)
    expression = _tuple_predicates(
        expression, name, schema, records, provider=provider, source=source
    )
    if not any(node.name == name for node in expression.op().find(ops.UnboundTable)):
        return expression
    rows = tuple(
        ibis.literal(True)
        .as_table()
        .select(
            **{
                column.slot_id: ibis.literal(
                    None if pd.isna(value) else value, type=ibis_type(column.dtype)
                )
                for column, value in zip(columns, row, strict=True)
            }
        )
        for row in records
    )
    if rows:
        table = ibis.union(rows[0], *rows[1:])
    else:
        table = (
            ibis.literal(True)
            .as_table()
            .select(
                **{column.slot_id: ibis.null().cast(ibis_type(column.dtype)) for column in columns}
            )
            .filter(ibis.literal(False))
        )
    return _replace_parameter(expression, name, table)


def _replace_parameter(expression: ir.Table, name: str, table: ir.Table) -> ir.Table:
    replacements = {
        node: table.op() for node in expression.op().find(ops.UnboundTable) if node.name == name
    }
    value: object = expression.op().replace(replacements)
    if not isinstance(value, ops.Relation):
        reject(
            "a typed source relation after parameter substitution",
            type(value).__name__,
            "Preserve the planned source relation.",
            "Query.parameters",
        )
    return value.to_expr()


def _tuple_predicates(
    expression: ir.Table,
    name: str,
    schema: sch.Schema,
    records: KeyRows,
    *,
    provider: str | None = None,
    source: SourceSession | None = None,
) -> ir.Table:
    """Instantiate qualified right-side key lookups as null-safe AND/OR predicates."""

    def origin(value: ops.Value) -> ops.Value:
        if isinstance(value, ops.Field):
            relation = value.rel
            if isinstance(relation, ops.Reference):
                return origin(ops.Field(relation.parent, value.name))
            prior = relation.values.get(value.name)
            if prior is not None:
                return origin(prior)
        return value

    def parameter_field(value: ops.Value) -> str | None:
        leaf = origin(value)
        return (
            leaf.name
            if isinstance(leaf, ops.Field)
            and isinstance(leaf.rel, ops.UnboundTable)
            and leaf.rel.name == name
            else None
        )

    current = expression.op()
    for chain in current.find(ops.JoinChain):
        for link in chain.rest:
            if link.how not in ("semi", "left") or not any(
                node.name == name for node in link.table.find(ops.UnboundTable)
            ):
                continue
            pairs = _key_join_pairs(link, parameter_field)
            if pairs is None or {key for _, key in pairs} != set(schema.names):
                continue
            predicate = _key_membership_predicate(schema, records, pairs, provider, source)
            result = _remove_key_join(chain, link, pairs, predicate, parameter_field, origin)
            if result is not None:
                current = current.replace({chain: result})
                break
    return current.to_expr()


def _key_join_pairs(
    link: ops.JoinLink, parameter_field: Callable[[ops.Value], str | None]
) -> list[tuple[ops.Value, str]] | None:
    pairs: list[tuple[ops.Value, str]] = []
    for condition in link.predicates:
        if not isinstance(condition, ops.IdenticalTo):
            return None
        right = parameter_field(condition.right)
        left = parameter_field(condition.left)
        if right is not None and left is None:
            pairs.append((condition.left, right))
        elif left is not None and right is None:
            pairs.append((condition.right, left))
        else:
            return None
    return pairs


def _key_membership_predicate(
    schema: sch.Schema,
    records: KeyRows,
    pairs: list[tuple[ops.Value, str]],
    provider: str | None,
    source: SourceSession | None,
) -> ir.BooleanValue:
    def missing(value: object) -> bool:
        return (
            value is None
            or value is pd.NA
            or value is pd.NaT
            or (isinstance(value, float) and math.isnan(value))
        )

    predicate = None
    bind = query_policy(provider).bind_key_membership
    if bind is not None and len(pairs) == len(schema):
        values_by_key = {key: value.to_expr() for value, key in pairs}
        if source is not None:
            predicate = source.complete_key_membership(schema, records, values_by_key)
        elif all(not value.type().is_string() for value in values_by_key.values()):
            predicate = bind(schema, records, values_by_key, None)
    rows = []
    for record in records if predicate is None else ():
        values = dict(zip(schema.names, record, strict=True))
        rows.append(
            ibis.and_(
                *(
                    left.to_expr().identical_to(
                        ibis.literal(
                            None if missing(values[key]) else values[key],
                            type=schema[key],
                        )
                    )
                    for left, key in pairs
                )
            )
        )
    # Keep the OR tree shallow for complete domains with many tuples.
    while len(rows) > 1:
        rows = [
            rows[index] | rows[index + 1] if index + 1 < len(rows) else rows[index]
            for index in range(0, len(rows), 2)
        ]
    if predicate is None:
        predicate = rows[0] if rows else ibis.literal(False)
    return predicate


def _remove_key_join(
    chain: ops.JoinChain,
    link: ops.JoinLink,
    pairs: list[tuple[ops.Value, str]],
    predicate: ir.BooleanValue,
    parameter_field: Callable[[ops.Value], str | None],
    origin: Callable[[ops.Value], ops.Value],
) -> ops.Relation | None:
    substitutions: dict[ops.Node, ops.Node] = {}
    left_by_key = {key: value for value, key in pairs}
    for field in chain.find(ops.Field):
        if field.rel != link.table:
            continue
        key = parameter_field(field)
        leaf = origin(field)
        value = (
            left_by_key[key].to_expr()
            if key is not None
            else leaf.to_expr()
            if isinstance(leaf, ops.Literal)
            else None
        )
        if value is None:
            return None
        substitutions[field] = ibis.ifelse(predicate, value, ibis.null().cast(field.dtype)).op()
    values = {key: value.replace(substitutions) for key, value in chain.values.items()}
    remaining = ops.JoinChain(
        chain.first, tuple(item for item in chain.rest if item is not link), values
    )
    result: ops.Relation = remaining
    if link.how == "semi":
        projected = {value: ops.Field(remaining, key) for key, value in remaining.values.items()}
        result = remaining.to_expr().filter(predicate.op().replace(projected).to_expr()).op()
    return result


def inline_key_relation(
    consumer: ir.Table,
    parameter: SourceParameter,
    producer: ir.Table,
    producer_keys: tuple[str, ...],
    provider: str,
) -> ir.Table:
    keys = distinct_returned_keys(
        producer.select(
            **dict(
                zip(
                    (column.slot_id for column in parameter.columns),
                    (producer[key] for key in producer_keys),
                    strict=True,
                )
            )
        ),
        provider,
    )
    return _replace_parameter(
        source_parameter_comparisons(consumer, parameter.placeholder, provider),
        parameter.placeholder,
        keys,
    )
