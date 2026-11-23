"""Explicit, bounded transformations for current AnalysisFrame planning."""

from __future__ import annotations

from marivo._definition.value_ir import (
    CoordinateSlot,
    FilterValues,
    SelectValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValuePartition,
    ValueShare,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.model.data_types import DataColumn, DecimalDataType, ScalarDataType


def short_circuit_selection_issue(
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
    """L1(a) admission for left-to-right selection with the second mask gated."""
    # The current Frame rewrite uses the same conservative grammar as L1(b).
    return total_selection_issue(
        first,
        second,
        value_columns,
        columns,
        key_groups,
        single_consumer=single_consumer,
        same_layout=same_layout,
        original_boundary=original_boundary,
    )


def total_selection_issue(
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
    """L1(b) admission for total predicates over the same input domain.

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


def _row_local(expression: ValueExpression) -> bool:
    if isinstance(expression, ValueUnary):
        return _row_local(expression.operand)
    if isinstance(expression, ValueBinary):
        return _row_local(expression.left) and _row_local(expression.right)
    return not isinstance(expression, (ValuePartition, ValueShare))


def select_fusion_issue(
    first: SelectValues,
    second: SelectValues,
    *,
    single_consumer: bool,
    same_key_domain: bool,
    original_boundary: bool,
    protected_intermediate: bool,
    value_policy_preserved: bool,
    checks_preserved: bool,
    demand_preserved: bool,
) -> tuple[str, str] | None:
    """L5 preconditions for per-key composition with a proposed replacement.

    The caller establishes the replacement's value policy, checks and demand.
    The gate returns the first unmet premise.
    """
    if not single_consumer:
        return "one consumer of the intermediate projection", "multiple consumers"
    if original_boundary:
        return "an unchanged original fine-grid completion boundary", "original completion"
    if protected_intermediate:
        return "an unreferenced intermediate projection", "output or key checkpoint"
    if not same_key_domain:
        return "row-local projections over the same complete key domain", "different key domain"
    if any(not _row_local(expression) for _, expression in (*first.values, *second.values)):
        return "only row-local expressions", "partition or share expression"
    if not value_policy_preserved:
        return "the original value policies", "changed value policy"
    if not checks_preserved:
        return "all required projection checks", "lost check obligation"
    if not demand_preserved:
        return "the original conditional demand masks", "changed demand mask"
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
