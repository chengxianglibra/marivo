"""Explicit, bounded transformations for current AnalysisFrame planning."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from math import isfinite

from marivo._definition.value_ir import (
    CoordinateSlot,
    FilterValues,
    PresenceSlot,
    SelectValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValuePartition,
    ValueShare,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.model.data_types import (
    DataColumn,
    DecimalDataType,
    ScalarDataType,
    TimestampDataType,
)


def short_circuit_selection_issue(
    *,
    single_consumer: bool,
    same_layout: bool,
    original_boundary: bool,
) -> tuple[str, str] | None:
    """L1(a) requires an unobserved boundary and the exact typed step input."""
    if not single_consumer:
        return "one consumer of the intermediate selection", "multiple consumers"
    if original_boundary:
        return "an unchanged original fine-grid completion boundary", "original completion"
    if not same_layout:
        return "the original typed layout at each step", "different layout"
    return None


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

    Both filters retain only True, so sequential application and Kleene
    conjunction agree on selected keys for total predicates.
    """
    issue = short_circuit_selection_issue(
        single_consumer=single_consumer,
        same_layout=same_layout,
        original_boundary=original_boundary,
    )
    if issue is not None:
        return issue
    if any(
        selection_predicate_columns(item.predicate, value_columns, columns, key_groups) is None
        for item in (first, second)
    ):
        return (
            "total scalar predicates with the same drop-NA mask policy",
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


_Carrier = tuple[str, bool]
_Operand = tuple[_Carrier, frozenset[str]]
_BOOLEAN: _Carrier = ("boolean", False)


def _column_carrier(column: DataColumn) -> _Carrier | None:
    dtype = column.dtype
    if isinstance(dtype, ScalarDataType):
        return dtype.name, False
    if isinstance(dtype, DecimalDataType):
        return "decimal", False
    if isinstance(dtype, TimestampDataType):
        return "timestamp", dtype.timezone is not None
    return None


def _literal_carrier(value: object) -> _Carrier | None:
    if isinstance(value, bool):
        return _BOOLEAN
    if type(value) is int:
        if -(2**63) <= value < 2**63:
            return "int64", False
        # The literal kernel already binds these integers as Decimal128.
        return ("decimal", False) if -(10**38) < value < 10**38 else None
    if isinstance(value, float) and isfinite(value):
        return "float64", False
    if isinstance(value, Decimal) and value.is_finite():
        # The existing literal carrier is Arrow decimal128.
        _, digits, exponent = value.as_tuple()
        if isinstance(exponent, int) and max(len(digits) + max(exponent, 0), -exponent) <= 38:
            return "decimal", False
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return None
        return "string", False
    if isinstance(value, datetime):
        return "timestamp", value.utcoffset() is not None
    if isinstance(value, date):
        return "date", False
    if value is None:
        return "null", False
    return None


def _comparable(left: _Carrier, right: _Carrier) -> bool:
    return (
        left == right
        or "null" in (left[0], right[0])
        or {left[0], right[0]} in ({"int64", "decimal"}, {"int64", "float64"})
    )


def selection_predicate_columns(
    expression: ValueExpression,
    value_columns: tuple[DataColumn, ...],
    columns: tuple[DataColumn, ...],
    key_groups: tuple[tuple[str, ...], ...],
    *,
    presence: tuple[tuple[tuple[str, ...], DataColumn], ...] = (),
) -> frozenset[str] | None:
    """Return only consumed slots for a proven total, row-local predicate."""
    by_slot = {column.slot_id: column for column in columns}
    presence_by_path = dict(presence)

    def operand(expression: ValueExpression) -> _Operand | None:
        column = None
        if isinstance(expression, ValueLiteral):
            kind = _literal_carrier(expression.value)
            return (kind, frozenset()) if kind is not None else None
        if isinstance(expression, ValueSlot) and 0 <= expression.index < len(value_columns):
            column = value_columns[expression.index]
        if isinstance(expression, CoordinateSlot) and 0 <= expression.index < len(key_groups):
            group = key_groups[expression.index]
            if len(group) == 1:
                column = by_slot.get(group[0])
        if isinstance(expression, PresenceSlot):
            column = presence_by_path.get(expression.path)
        if column is None or (kind := _column_carrier(column)) is None:
            return None
        return kind, frozenset((column.slot_id,))

    def expression_carrier(expression: ValueExpression) -> _Operand | None:
        if isinstance(expression, ValueUnary):
            inner = expression_carrier(expression.operand)
            if inner is not None and (
                expression.operation == "is_null"
                or (expression.operation == "not" and inner[0] == _BOOLEAN)
            ):
                return _BOOLEAN, inner[1]
            return None
        if not isinstance(expression, ValueBinary):
            return operand(expression)
        left, right = expression_carrier(expression.left), expression_carrier(expression.right)
        if left is None or right is None:
            return None
        if expression.operation in ("and", "or"):
            compatible = left[0] == right[0] == _BOOLEAN
        elif expression.operation in ("eq", "ne", "lt", "le", "gt", "ge"):
            compatible = _comparable(left[0], right[0])
        else:
            compatible = False
        return (_BOOLEAN, left[1] | right[1]) if compatible else None

    result = expression_carrier(expression)
    return result[1] if result is not None and result[0] == _BOOLEAN else None
