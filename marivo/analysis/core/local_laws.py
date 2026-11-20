"""Explicit, bounded transformations for current AnalysisFrame planning."""

from __future__ import annotations

from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
)
from marivo.analysis.core.data_types import DataColumn, DecimalDataType, ScalarDataType


def query_selection_fusion_issue(
    first: FilterValues,
    second: FilterValues,
    value_columns: tuple[DataColumn, ...],
    columns: tuple[DataColumn, ...],
    key_groups: tuple[tuple[str, ...], ...],
    *,
    single_consumer: bool,
    same_layout: bool,
    original_boundary: bool,
) -> tuple[str, str] | None:
    """L1 adapter for AnalysisFrame's same-mask nullable int64/integer-SUM comparisons.

    Both filters retain only True, so a common nullable comparison mask has
    exactly the same selected keys under sequential application and Kleene
    conjunction. Arithmetic, presence selection and independent domains remain
    outside this bounded law.
    """
    if not single_consumer:
        return "one consumer of the intermediate selection", "multiple consumers"
    if original_boundary:
        return "an unchanged original fine-grid completion boundary", "original completion"
    if not same_layout:
        return "same-layout comparisons on the same actual input", "different layout"
    if any(
        selection_predicate_columns(item.predicate, value_columns, columns, key_groups) is None
        for item in (first, second)
    ):
        return (
            "closed int64 or Decimal(38,0) comparisons with the same drop-NA mask policy",
            "unqualified predicate or numeric operation",
        )
    return None


def selection_predicate_columns(
    expression: ValueExpression,
    value_columns: tuple[DataColumn, ...],
    columns: tuple[DataColumn, ...],
    key_groups: tuple[tuple[str, ...], ...],
) -> frozenset[str] | None:
    """Return the columns of a total row-local comparison predicate, if admitted."""
    by_slot = {column.slot_id: column for column in columns}

    def operand(expression: ValueExpression) -> frozenset[str] | None:
        if isinstance(expression, ValueLiteral):
            if type(expression.value) is int and -(2**63) <= expression.value < 2**63:
                return frozenset()
        elif isinstance(expression, ValueSlot) and 0 <= expression.index < len(value_columns):
            column = value_columns[expression.index]
            if column.dtype in (ScalarDataType("int64"), DecimalDataType(38, 0)):
                return frozenset((column.slot_id,))
        elif isinstance(expression, CoordinateSlot) and 0 <= expression.index < len(key_groups):
            group = key_groups[expression.index]
            if len(group) == 1 and by_slot[group[0]].dtype == ScalarDataType("int64"):
                return frozenset(group)
        return None

    def predicate(expression: ValueExpression) -> frozenset[str] | None:
        if not isinstance(expression, ValueBinary):
            return None
        if expression.operation in ("and", "or"):
            left, right = predicate(expression.left), predicate(expression.right)
        elif expression.operation in ("eq", "ne", "lt", "le", "gt", "ge"):
            left, right = operand(expression.left), operand(expression.right)
        else:
            return None
        return None if left is None or right is None else left | right

    return predicate(expression)
