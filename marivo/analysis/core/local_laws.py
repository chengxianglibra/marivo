"""Explicit, bounded transformations for current Query planning."""

from __future__ import annotations

from collections.abc import Mapping

from marivo._definition.expressions import (
    DeclaredMetricRef,
    EntityCount,
    MetricNode,
    SourceAggregate,
)
from marivo._definition.query import FilterValues, RollupValues
from marivo._definition.types import ExactRef
from marivo.analysis.core.data_types import DataColumn, DataType, DecimalDataType, ScalarDataType


def query_selection_fusion_issue(
    first: FilterValues,
    second: FilterValues,
    value_columns: tuple[DataColumn, ...],
    columns: tuple[DataColumn, ...],
    key_groups: tuple[tuple[str, ...], ...],
    *,
    single_consumer: bool,
    same_layout: bool,
    pending_checks: bool,
    original_boundary: bool,
) -> tuple[str, str] | None:
    """L1 adapter for the current Query's same-mask nullable int64 comparisons.

    Both filters retain only True, so a common nullable comparison mask has
    exactly the same selected keys under sequential application and Kleene
    conjunction. Arithmetic, presence selection and independent domains remain
    outside this bounded law.
    """
    from marivo._definition.query import (
        CoordinateSlot,
        ValueBinary,
        ValueExpression,
        ValueLiteral,
        ValueSlot,
    )

    if not single_consumer:
        return "one consumer of the intermediate selection", "multiple consumers"
    if pending_checks:
        return "completed checks before selection fusion", "a pending check boundary"
    if original_boundary:
        return "an unchanged original fine-grid completion boundary", "original completion"
    if not same_layout or first.domains or second.domains:
        return "same-layout comparisons on the same actual input", "different domains or layout"
    by_slot = {column.slot_id: column for column in columns}

    def operand(expression: ValueExpression) -> bool:
        if isinstance(expression, ValueLiteral):
            return type(expression.value) is int and -(2**63) <= expression.value < 2**63
        if isinstance(expression, ValueSlot):
            return 0 <= expression.index < len(value_columns) and value_columns[
                expression.index
            ].dtype == ScalarDataType("int64")
        if isinstance(expression, CoordinateSlot):
            return (
                0 <= expression.index < len(key_groups)
                and all(
                    by_slot[slot].dtype == ScalarDataType("int64")
                    for slot in key_groups[expression.index]
                )
                and len(key_groups[expression.index]) == 1
            )
        return False

    def predicate(expression: object) -> bool:
        if not isinstance(expression, ValueBinary):
            return False
        if expression.operation in ("and", "or"):
            return predicate(expression.left) and predicate(expression.right)
        return (
            expression.operation in ("eq", "ne", "lt", "le", "gt", "ge")
            and operand(expression.left)
            and operand(expression.right)
        )

    if not predicate(first.predicate) or not predicate(second.predicate):
        return (
            "closed int64 comparisons with the same drop-NA mask policy",
            "unqualified predicate or numeric operation",
        )
    return None


def query_original_fusion_issue(
    first: RollupValues,
    second: RollupValues,
    outputs: tuple[MetricNode, ...],
    declared: Mapping[ExactRef, MetricNode],
    dtypes: tuple[DataType, ...],
    *,
    single_consumer: bool,
    same_components: bool,
    pending_checks: bool,
    ordinary: bool,
) -> tuple[str, str] | None:
    """L8 ordered direct-key group with every original finish retained.

    Unlike eliminating a fixed-state finish, the group executes both checked
    reductions in their original order. Thus no regrouping bound is inferred
    from a final total, and intermediate int64/Decimal failures still occur.
    """
    if not single_consumer:
        return "one consumer of the intermediate original reduction", "multiple consumers"
    if pending_checks:
        return "fixed external check positions", "a pending check boundary"
    if not same_components or not ordinary or first.calendar_maps or second.calendar_maps:
        return (
            "identical ordinary components and direct retained axes",
            "time/sample/component boundary",
        )
    if not all(
        dtype == ScalarDataType("int64") or isinstance(dtype, DecimalDataType) for dtype in dtypes
    ):
        return "exact int64 or Decimal original quantities", "unqualified numeric finish"

    def leaf(node: MetricNode) -> bool:
        if isinstance(node, DeclaredMetricRef):
            return leaf(declared[node.ref])
        return isinstance(node, EntityCount) or (
            isinstance(node, SourceAggregate) and node.reducer in ("sum", "count")
        )

    if not outputs or not all(leaf(node) for node in outputs):
        return "direct exact source sum/count finishes", "formula/per/window finish boundary"
    return None
