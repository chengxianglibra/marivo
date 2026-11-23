"""Derive common bounded time ranges from required predicate branches."""

from __future__ import annotations

import operator
from datetime import datetime

from ibis.common.deferred import BinaryOperator, Just, Resolver, Variable

from marivo._definition.expressions import (
    PredicateExpression,
    RowExpression,
    RowField,
)
from marivo._definition.types import (
    TimeInstantFieldRead,
)


def _required_time_ranges(
    conditions: tuple[PredicateExpression, ...],
) -> dict[TimeInstantFieldRead, tuple[datetime, datetime]]:
    """Find explicit half-open time ranges required by the full predicate.

    Only direct comparisons in top-level conjunctions are eligible. In
    particular, descending through a negation or disjunction could remove
    rows which satisfy the complete predicate.
    """
    bounds: dict[TimeInstantFieldRead, tuple[list[datetime], list[datetime]]] = {}

    def row(recipe: Resolver, expression: RowExpression) -> None:
        if isinstance(recipe, BinaryOperator) and recipe.func is operator.and_:
            row(recipe.left, expression)
            row(recipe.right, expression)
            return
        if not (
            isinstance(recipe, BinaryOperator)
            and recipe.func in (operator.ge, operator.lt)
            and isinstance(recipe.left, Variable)
            and isinstance(recipe.right, Just)
            and isinstance(recipe.right.value, datetime)
        ):
            return
        index = int(recipe.left.name.removeprefix("slot_"))
        field = expression.inputs[index]
        if not isinstance(field, RowField) or not isinstance(field.read, TimeInstantFieldRead):
            return
        lower, upper = bounds.setdefault(field.read, ([], []))
        (lower if recipe.func is operator.ge else upper).append(recipe.right.value)

    def predicate(recipe: Resolver, condition: PredicateExpression) -> None:
        if isinstance(recipe, BinaryOperator) and recipe.func is operator.and_:
            predicate(recipe.left, condition)
            predicate(recipe.right, condition)
        elif isinstance(recipe, Variable):
            value = condition.inputs[int(recipe.name.removeprefix("slot_"))]
            if isinstance(value, RowExpression):
                row(value.recipe, value)

    for condition in conditions:
        predicate(condition.recipe, condition)
    result: dict[TimeInstantFieldRead, tuple[datetime, datetime]] = {}
    for read, (lower, upper) in bounds.items():
        if lower and upper:
            start, end = max(lower), min(upper)
            if start < end:
                result[read] = (start, end)
    return result
