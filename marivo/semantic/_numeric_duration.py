"""Shared fixed Duration propagation through native numeric recipes."""

import operator
from collections.abc import Callable
from typing import TypeVar

from ibis.common.deferred import (
    Attr,
    BinaryOperator,
    Call,
    Just,
    Resolver,
    Sequence,
    UnaryOperator,
    Variable,
)

from marivo._definition.deferred import RescaleResolver, attribute_name
from marivo.semantic.numeric_operations import numeric_failure

DurationT = TypeVar("DurationT")


def _combine_duration(parts: tuple[DurationT | None, ...]) -> DurationT | None:
    present = tuple(part for part in parts if part is not None)
    if len(set(present)) > 1:
        numeric_failure("matching fixed Duration units", repr(present))
    return present[0] if present else None


def _duration_arithmetic(
    divide: bool, multiply: bool, left: DurationT | None, right: DurationT | None
) -> DurationT | None:
    common = _combine_duration((left, right))
    if divide and right is not None:
        if left is None:
            numeric_failure("a numeric or fixed Duration quotient", "inverse Duration")
        return None
    if multiply and left is not None and right is not None:
        numeric_failure("one Duration operand in multiplication", "two Duration operands")
    return common


def _duration_call(item: Call, visit: Callable[[Resolver], DurationT | None]) -> DurationT | None:
    if type(item.func) is Attr:
        name = attribute_name(item.func)
        parts = (visit(item.func.obj), *(visit(arg) for arg in item.args))
    else:
        name = item.func.value.__name__
        parts = tuple(visit(arg) for arg in item.args)
    if name in ("isnull", "notnull"):
        return None
    if name == "divide":
        return _duration_arithmetic(True, False, parts[0], parts[1])
    if name in ("sqrt", "log", "ln", "exp") and any(part is not None for part in parts):
        numeric_failure("a dimensionless input to nonlinear numeric functions", "Duration")
    return _combine_duration((*parts, *(visit(value) for value in item.kwargs.values())))


def recipe_duration(recipe: Resolver, inputs: tuple[DurationT | None, ...]) -> DurationT | None:
    """Propagate fixed tick units through the admitted native recipe structure."""

    def visit(item: Resolver) -> DurationT | None:
        if isinstance(item, RescaleResolver):
            return _duration_arithmetic(True, False, visit(item.recipe), None)
        if type(item) is Variable:
            return inputs[int(item.name.removeprefix("slot_"))]
        if type(item) is Just:
            return None
        if type(item) is Sequence:
            return _combine_duration(tuple(visit(child) for child in item.values))
        if type(item) is UnaryOperator:
            return None if item.func is operator.invert else visit(item.arg)
        if type(item) is BinaryOperator:
            left, right = visit(item.left), visit(item.right)
            if item.func in (
                operator.eq,
                operator.ne,
                operator.lt,
                operator.le,
                operator.gt,
                operator.ge,
                operator.and_,
                operator.or_,
                operator.xor,
            ):
                return None
            return _duration_arithmetic(
                item.func is operator.truediv, item.func is operator.mul, left, right
            )
        if type(item) is Call:
            return _duration_call(item, visit)
        numeric_failure("an admitted Duration recipe", type(item).__name__)

    return visit(recipe)
