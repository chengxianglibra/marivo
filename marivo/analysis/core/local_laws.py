"""Explicit, bounded transformations for current AnalysisFrame planning."""

from __future__ import annotations

from marivo._definition.query import FilterValues
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
    from marivo._definition.query import (
        CoordinateSlot,
        ValueBinary,
        ValueExpression,
        ValueLiteral,
        ValueSlot,
    )

    if not single_consumer:
        return "one consumer of the intermediate selection", "multiple consumers"
    if original_boundary:
        return "an unchanged original fine-grid completion boundary", "original completion"
    if not same_layout:
        return "same-layout comparisons on the same actual input", "different layout"
    by_slot = {column.slot_id: column for column in columns}

    def operand(expression: ValueExpression) -> bool:
        if isinstance(expression, ValueLiteral):
            return type(expression.value) is int and -(2**63) <= expression.value < 2**63
        if isinstance(expression, ValueSlot):
            return 0 <= expression.index < len(value_columns) and value_columns[
                expression.index
            ].dtype in (ScalarDataType("int64"), DecimalDataType(38, 0))
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
            "closed int64 or Decimal(38,0) comparisons with the same drop-NA mask policy",
            "unqualified predicate or numeric operation",
        )
    return None
