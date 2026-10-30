"""Schema-only Query-key templates and invocation-local parameter relations."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import TYPE_CHECKING

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa

from marivo._definition.graph import computation_fingerprint
from marivo._definition.relations import AttributeKeys, FrameKeys
from marivo.analysis.compiler.parameters import parameter_domains
from marivo.analysis.compiler.query_program import (
    FrameParameter,
    PopulationParameterPlan,
)
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import reject
from marivo.analysis.core.value_types import ibis_type
from marivo.analysis.materialization.graph_exchange import StageOutput

if TYPE_CHECKING:
    from marivo.analysis.compiler.optimization import InvocationMemo
    from marivo.analysis.materialization.graph_source_execution import PreparedSources
    from marivo.datasource.adapters import SourceSession
    from marivo.semantic._compiled_state import CompiledSemanticState


def prepare_frame_parameters(
    graph: BoundGraph,
    frame_schemas: Mapping[str, DataSchema],
    entities: dict[str, ir.Table],
) -> tuple[FrameParameter, ...]:
    """Declare complete Frame-key relations using prepared input schemas only."""
    from marivo.analysis.compiler.populations import frame_key_columns

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
    import pandas as pd

    from marivo.analysis.compiler.source_predicates import source_parameter_comparisons

    if provider is not None:
        expression = source_parameter_comparisons(expression, name, provider)
    records = tuple(output.table.loc[:, list(output.keys)].itertuples(index=False, name=None))
    from marivo.analysis.core.data_types import DecimalDataType, TimestampDataType

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
            provider in ("duckdb", "clickhouse")
            or not any(
                isinstance(value, float) and not isfinite(value) for row in records for value in row
            )
        )
    ):
        from marivo.datasource._calendar_relations import calendar_data

        records = tuple(
            tuple(row[column.slot_id] for column in columns)
            for row in parameter_data(columns, output).to_pylist()
        )
        literal = calendar_data(
            ibis.schema({column.slot_id: ibis_type(column.dtype) for column in columns}), records
        )
        return _replace_parameter(expression, name, literal)

    if provider == "mysql":
        # Native readers can return nullable integer keys as unambiguous floats.
        # Normalize against the planned key schema before forming JSON names.
        records = tuple(
            tuple(row[column.slot_id] for column in columns)
            for row in parameter_data(columns, output).to_pylist()
        )
    if (
        provider in ("postgres", "trino", "clickhouse")
        and (records or provider in ("trino", "clickhouse"))
        and columns
        and all(
            isinstance(column.dtype, ScalarDataType)
            and column.dtype.name
            in (
                ("int64", "string", "boolean", "date")
                if provider == "postgres"
                else ("int64", "string")
            )
            for column in columns
        )
    ):
        if provider == "clickhouse":
            fields = {
                f"field_{index}": ibis_type(column.dtype) for index, column in enumerate(columns)
            }
            array_type = dt.Array(dt.Struct(fields))
            literal = ibis.literal(
                tuple(
                    {
                        f"field_{index}": None if pd.isna(value) else value
                        for index, value in enumerate(row)
                    }
                    for row in records
                ),
                type=array_type,
            )
            # Empty arrays otherwise have element type Nothing in native SQL.
            # The explicit key schema supplies the tuple field types and NA contract.
            literal = ops.Cast(literal, to=array_type).to_expr()
            expanded = literal.name("__keys").as_table().unnest("__keys")
            keys = expanded.select(
                **{
                    column.slot_id: expanded["__keys"][f"field_{index}"]
                    for index, column in enumerate(columns)
                }
            )
            return _replace_parameter(expression, name, keys)
        arrays = (
            ibis.literal(True)
            .as_table()
            .select(
                **{
                    f"__keys_{index}": ibis.literal(
                        tuple(None if pd.isna(row[index]) else row[index] for row in records),
                        type=dt.Array(ibis_type(column.dtype)),
                    )
                    for index, column in enumerate(columns)
                }
            )
        )
        expanded = arrays.unnest("__keys_0", offset="__keys_position")
        keys = expanded.select(
            **{
                column.slot_id: expanded["__keys_0"]
                if index == 0
                else expanded[f"__keys_{index}"][expanded["__keys_position"]]
                for index, column in enumerate(columns)
            }
        )
        return _replace_parameter(expression, name, keys)
    expression = _tuple_predicates(
        expression, name, columns, records, provider=provider, source=source
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
        from marivo.analysis.core.model import reject

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
    columns: tuple[DataColumn, ...],
    records: tuple[tuple[object, ...], ...],
    *,
    provider: str | None = None,
    source: SourceSession | None = None,
) -> ir.Table:
    """Instantiate qualified right-side key lookups as null-safe AND/OR predicates."""
    import math

    import pandas as pd

    def missing(value: object) -> bool:
        return (
            value is None
            or value is pd.NA
            or value is pd.NaT
            or (isinstance(value, float) and math.isnan(value))
        )

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
            pairs: list[tuple[ops.Value, str]] = []
            for condition in link.predicates:
                if not isinstance(condition, ops.IdenticalTo):
                    break
                right = parameter_field(condition.right)
                left = parameter_field(condition.left)
                if right is not None and left is None:
                    pairs.append((condition.left, right))
                elif left is not None and right is None:
                    pairs.append((condition.right, left))
                else:
                    break
            else:
                if {key for _, key in pairs} != {column.slot_id for column in columns}:
                    continue
                predicate = None
                if provider == "mysql" and len(pairs) == len(columns):
                    from marivo.analysis.materialization.mysql_key_parameters import (
                        membership,
                        qualifies_strings,
                    )

                    values_by_key = {key: value.to_expr() for value, key in pairs}
                    if qualifies_strings(values_by_key, source):
                        predicate = membership(columns, records, values_by_key, source)
                typed = {column.slot_id: column.dtype for column in columns}
                rows = []
                for record in records if predicate is None else ():
                    values = dict(zip((column.slot_id for column in columns), record, strict=True))
                    rows.append(
                        ibis.and_(
                            *(
                                left.to_expr().identical_to(
                                    ibis.literal(
                                        None if missing(values[key]) else values[key],
                                        type=ibis_type(typed[key]),
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
                        break
                    substitutions[field] = ibis.ifelse(
                        predicate, value, ibis.null().cast(field.dtype)
                    ).op()
                else:
                    values = {
                        key: value.replace(substitutions) for key, value in chain.values.items()
                    }
                    remaining = ops.JoinChain(
                        chain.first, tuple(item for item in chain.rest if item is not link), values
                    )
                    result: ops.Relation = remaining
                    if link.how == "semi":
                        projected = {
                            value: ops.Field(remaining, key)
                            for key, value in remaining.values.items()
                        }
                        result = (
                            remaining.to_expr()
                            .filter(predicate.op().replace(projected).to_expr())
                            .op()
                        )
                    current = current.replace({chain: result})
                    break
    return current.to_expr()
