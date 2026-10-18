"""Finite remote Query relation qualification, separate from numeric finishing."""

from __future__ import annotations

import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import EntityCount, SourceAggregate, SourceWeightedMean
from marivo._definition.query import ReduceValues
from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.compiler.graph_lowering import LoweredRead
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.core.data_types import DecimalDataType, ScalarDataType
from marivo.datasource.adapters import SourceSession
from marivo.datasource.ir import TableSourceIR


def qualified_query_provider(owner: SourceSession) -> bool:
    """Recognize providers with an implemented relation route, not all shapes."""
    return owner.provider.name in ("duckdb", "postgres", "mysql", "trino", "clickhouse")


def qualify_query_join(
    owner: SourceSession, endpoints: tuple[ComparisonEndpoint, ...]
) -> str | None:
    """Admit joins whose complete axes survive nullable endpoint transport."""
    if owner.provider.name == "duckdb":
        return None
    if owner.provider.name not in ("trino", "clickhouse"):
        return "this provider has no qualified joined Query endpoint route"
    if any(
        endpoint.column(key).dtype != ScalarDataType("string")
        for endpoint in endpoints
        for key in endpoint.keys
    ):
        return "remote joined endpoints retain exact non-string identity carriers"
    return None


def qualify_query_source(
    owner: SourceSession,
    read: LoweredRead,
    identity_slots: frozenset[str],
    attribute_slots: frozenset[str] = frozenset(),
) -> str | None:
    """Qualify finite source values independently of key identity and string carriers."""
    if owner.provider.name not in ("mysql", "trino", "clickhouse"):
        return "this remote provider has no qualified Query relation route"
    if owner.provider.name == "clickhouse":
        from marivo.analysis.materialization.query_preparation import physical_fields
        from marivo.datasource.engines.clickhouse import decoded_string_columns

        leaves = set(read.expression.op().find(ops.DatabaseTable))
        matched: set[ops.Node] = set()
        for identity, bound in owner._bound_sources.items():
            if bound.relation.op() not in leaves:
                continue
            if owner.clickhouse_table_engine(bound) != "MergeTree":
                return "ClickHouse Query fusion currently requires local MergeTree inputs"
            assert isinstance(bound.source, TableSourceIR)
            if identity not in owner._query_decoded_strings:
                owner._query_decoded_strings[identity] = decoded_string_columns(
                    owner._backend, owner.datasource, bound.source
                )
            for column in read.stage.columns:
                if column.dtype != ScalarDataType("string"):
                    continue
                if any(
                    physical.rel == bound.relation.op()
                    and physical.name not in owner._query_decoded_strings[identity]
                    for physical in physical_fields(read.expression[column.slot_id])
                ):
                    return "ClickHouse encoded string carriers retain their typed reader boundary"
            matched.add(bound.relation.op())
        if not leaves or not leaves <= matched:
            return "ClickHouse Query fusion has an unqualified physical table"
    stage = read.stage
    if stage.reduction is None:
        if any(
            column.dtype != ScalarDataType("string")
            and not (column.slot_id in identity_slots and column.dtype == ScalarDataType("int64"))
            and not (
                owner.provider.name in ("trino", "clickhouse")
                and (column.slot_id not in stage.keys or column.slot_id in attribute_slots)
                and (
                    column.dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
                    or isinstance(column.dtype, DecimalDataType)
                )
            )
            for column in stage.columns
        ):
            return "remote field relations retain numeric, time and complete integer carrier boundaries"
        return None
    node = stage.reduction.node
    if not (
        isinstance(node, EntityCount)
        or (isinstance(node, SourceAggregate) and node.reducer in ("count", "count_distinct"))
        or (
            owner.provider.name in ("trino", "clickhouse")
            and (
                isinstance(node, SourceWeightedMean)
                or (
                    isinstance(node, SourceAggregate)
                    and node.reducer in ("sum", "min", "max", "mean")
                )
            )
        )
    ):
        return "remote aggregate producers retain their owning numeric finish"
    if any(
        column.dtype != ScalarDataType("string")
        for column in stage.columns
        if column.slot_id in stage.keys
    ):
        return "remote aggregate producer numeric coordinates retain their pandas key boundary"
    if owner.provider.name == "mysql" and stage.keys:
        from marivo.analysis.materialization.query_preparation import physical_fields
        from marivo.datasource.engines.mysql import original_string_column

        for key in stage.keys:
            fields = physical_fields(read.expression[key])
            if not fields:
                return "MySQL aggregate keys require a physical binary NO PAD collation"
            for physical in fields:
                found_owner = False
                for identity, bound in owner._bound_sources.items():
                    if physical.rel != bound.relation.op():
                        continue
                    found_owner = True
                    fact = (identity, physical.name)
                    if fact not in owner._query_string_facts:
                        owner._query_string_facts[fact] = isinstance(
                            bound.source, TableSourceIR
                        ) and original_string_column(
                            owner._backend, owner.datasource, bound.source, physical.name
                        )
                    if not owner._query_string_facts[fact]:
                        return (
                            "MySQL aggregate keys retain their native collation grouping boundary"
                        )
                if not found_owner:
                    return "MySQL aggregate key collation has no physical source owner"
    return None


def qualify_query_top(owner: SourceSession, program: ResultProgram, score: ir.Value) -> str | None:
    """Admit native numeric and byte-string ordering after leaf qualification."""
    if not qualified_query_provider(owner):
        return "this provider has no qualified Query Top route"
    if not (
        score.type().is_string()
        or score.type().is_integer()
        or (owner.provider.name in ("trino", "clickhouse") and score.type().is_numeric())
    ):
        return "remote Top retains its established pandas score method"
    return None


def qualify_query_reduce(owner: SourceSession, program: ResultProgram) -> str | None:
    """Push qualified cell counts and native sums with their independent NA masks."""
    if not qualified_query_provider(owner):
        return "this provider has no qualified cell reduction route"
    if not isinstance(program.operation, ReduceValues) or any(
        statistic.method != "row_count"
        and not (owner.provider.name in ("trino", "clickhouse") and statistic.method == "sum")
        for _, statistic in program.operation.values
    ):
        return "remote cell statistics retain their existing local numeric method"
    return None
