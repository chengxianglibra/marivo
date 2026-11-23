"""Finite remote Query relation qualification, separate from numeric finishing."""

from __future__ import annotations

import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import EntityCount, SourceAggregate, SourceWeightedMean
from marivo._kernel.validation import is_instance
from marivo.analysis.compiler.lowered_query import LoweredRead
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.compiler.query_stages import SourceReadStage, read_reduction, require_reduction
from marivo.analysis.model.data_types import DecimalDataType, ScalarDataType
from marivo.datasource.spi import SourceSession, physical_fields


def qualified_query_provider(owner: SourceSession) -> bool:
    """Recognize providers with an implemented relation route, not all shapes."""
    return owner.provider.query_policy.key_fusion != "none"


def _qualify_clickhouse_strings(
    owner: SourceSession, stage: SourceReadStage, read: LoweredRead
) -> str | None:
    leaves = set(read.expression.op().find(ops.DatabaseTable))
    matched: set[ops.Node] = set()
    for bound in owner.bindings():
        if bound.relation.op() not in leaves:
            continue
        try:
            issue = owner.table_join_issue(bound)
        except Exception:
            return "ClickHouse Query fusion requires visible system.tables topology metadata"
        if issue is not None:
            return issue
        decoded = owner.string_facts.query_string_columns(bound)
        for column in stage.columns:
            if column.dtype != ScalarDataType("string"):
                continue
            if any(
                physical.rel == bound.relation.op() and physical.name not in decoded
                for physical in physical_fields(read.expression[column.slot_id])
            ):
                return "ClickHouse encoded string carriers retain their typed reader boundary"
        matched.add(bound.relation.op())
    if not leaves or not leaves <= matched:
        return "ClickHouse Query fusion has an unqualified physical table"
    return None


def qualify_query_source(
    owner: SourceSession,
    stage: SourceReadStage,
    read: LoweredRead,
    identity_slots: frozenset[str],
    attribute_slots: frozenset[str] = frozenset(),
) -> str | None:
    """Qualify finite source values independently of key identity and string carriers."""
    policy = owner.provider.query_policy
    if not policy.needs_remote_carrier_qualification:
        return "this remote provider has no qualified Query relation route"
    if owner.provider.dialect.query_string_columns is not None:
        issue = _qualify_clickhouse_strings(owner, stage, read)
        if issue is not None:
            return issue
    if read_reduction(stage) is None:
        if any(
            column.dtype != ScalarDataType("string")
            and not (column.slot_id in identity_slots and column.dtype == ScalarDataType("int64"))
            and not (
                policy.key_fusion == "collated"
                and column.slot_id in attribute_slots
                and column.dtype == ScalarDataType("int64")
            )
            and not (
                policy.key_fusion == "numeric"
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
    node = require_reduction(stage).node
    if not (
        isinstance(node, EntityCount)
        or (isinstance(node, SourceAggregate) and node.reducer in ("count", "count_distinct"))
        or (
            policy.key_fusion == "numeric"
            and (
                isinstance(node, SourceWeightedMean)
                or (
                    is_instance(node, SourceAggregate)
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
    if policy.key_fusion == "collated" and stage.keys:
        return _aggregate_key_collation(owner, stage, read)
    return None


def _aggregate_key_collation(
    owner: SourceSession, stage: SourceReadStage, read: LoweredRead
) -> str | None:
    for key in stage.keys:
        fields = physical_fields(read.expression[key])
        if not fields:
            return "MySQL aggregate keys require a physical binary NO PAD collation"
        for physical in fields:
            found_owner = False
            for bound in owner.bindings():
                if physical.rel != bound.relation.op():
                    continue
                found_owner = True
                if not owner.string_facts.original_key_string(bound, physical.name):
                    return "MySQL aggregate keys retain their native collation grouping boundary"
            if not found_owner:
                return "MySQL aggregate key collation has no physical source owner"
    return None


def qualify_query_top(owner: SourceSession, program: ResultProgram, score: ir.Value) -> str | None:
    """Admit native numeric and byte-string ordering after leaf qualification."""
    policy = owner.provider.query_policy
    if not qualified_query_provider(owner):
        return "this provider has no qualified Query Top route"
    if not (
        score.type().is_string()
        or score.type().is_integer()
        or (policy.key_fusion == "numeric" and score.type().is_numeric())
    ):
        return "remote Top retains its established pandas score method"
    return None
