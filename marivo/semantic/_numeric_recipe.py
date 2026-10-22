"""Bind admitted Deferred recipes and lower their native Ibis operation DAG.

Slots carry real prepared types. Checks travel with each use and are narrowed by
branch demand before a source backend or the local operation adapter consumes them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
from ibis.common.annotations import SignatureValidationError
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

from marivo._definition.deferred import attribute_name, canonical_recipe
from marivo._numeric_types import bind_numeric_literal

if TYPE_CHECKING:
    from marivo.semantic._numeric_lowering import NumericCheck, NumericValue


def lower_recipe(recipe: Resolver, inputs: tuple[NumericValue, ...]) -> NumericValue:
    """Bind a checked recipe to actual typed slots, preserving per-use checks."""
    from marivo.semantic._numeric_lowering import numeric_failure

    names = tuple(f"slot_{i}" for i in range(len(inputs)))
    canonical_recipe(recipe, slots=names)
    table = ibis.table(
        {name: item.value.type() for name, item in zip(names, inputs, strict=True)},
        name="_numeric_recipe",
    )

    def bind(item: Resolver) -> object:
        if type(item) is Variable:
            return table[item.name]
        if type(item) is Just:
            return item.value
        if type(item) is Sequence:
            return tuple(bind(value) for value in item.values)
        if type(item) is BinaryOperator:
            return item.func(bind(item.left), bind(item.right))
        if type(item) is UnaryOperator:
            return item.func(bind(item.arg))
        if type(item) is Call:
            args = tuple(bind(arg) for arg in item.args)
            kwargs = {key: bind(value) for key, value in item.kwargs.items()}
            if type(item.func) is Attr:
                receiver = bind(item.func.obj)
                name = attribute_name(item.func)
                if not isinstance(receiver, ir.Value):
                    numeric_failure("an Ibis value receiver", type(receiver).__name__)
                method = getattr(receiver, name)
                return method(*args, **kwargs)
            if type(item.func) is Just:
                return item.func.value(*args, **kwargs)
        numeric_failure("an admitted numeric recipe", type(item).__name__)

    try:
        expression = bind(recipe)
    except (TypeError, ValueError, AttributeError, SignatureValidationError) as error:
        numeric_failure(
            "physical types admitted by the numeric recipe", f"{type(error).__name__}: {error}"
        )
    if not isinstance(expression, ir.Value):
        expression = ibis.literal(expression)
    return _lower_operation(expression.op(), dict(zip(names, inputs, strict=True)))


def _masked(checks: tuple[NumericCheck, ...], demand: ir.Value) -> tuple[NumericCheck, ...]:
    from marivo.semantic._numeric_lowering import NumericCheck

    return tuple(
        NumericCheck(demand.fill_null(False) & check.violation, check.expected, check.received)
        for check in checks
    )


def _lower_operation(node: ops.Value, slots: Mapping[str, NumericValue]) -> NumericValue:
    from marivo.semantic._numeric_lowering import (
        NumericCheck,
        NumericValue,
        _finite_check,
        numeric_binary,
        numeric_failure,
    )

    def visit(value: ops.Value) -> NumericValue:
        return _lower_operation(value, slots)

    if isinstance(node, ops.Field):
        return slots[node.name]
    if isinstance(node, ops.Literal):
        return NumericValue(bind_numeric_literal(node.to_expr()))
    if isinstance(node, ops.Alias):
        return visit(node.arg)
    if isinstance(node, (ops.Add, ops.Subtract, ops.Multiply, ops.Divide)):
        operation = {
            ops.Add: "add",
            ops.Subtract: "subtract",
            ops.Multiply: "multiply",
            ops.Divide: "divide",
        }[type(node)]
        return numeric_binary(
            operation,
            visit(node.left),
            visit(node.right),
        )
    if isinstance(node, (ops.Negate, ops.Abs)):
        item = visit(node.arg)
        value = node.copy(arg=item.value.op()).to_expr()
        return NumericValue(value, (*item.checks, *_finite_check(value, type(node).__name__)))
    if isinstance(node, ops.IfElse):
        condition, yes, no = (
            visit(node.bool_expr),
            visit(node.true_expr),
            visit(node.false_null_expr),
        )
        selected = condition.value.fill_null(False)
        return NumericValue(
            selected.ifelse(yes.value, no.value),
            (*condition.checks, *_masked(yes.checks, selected), *_masked(no.checks, ~selected)),
        )
    if isinstance(node, ops.SearchedCase):
        remaining = ibis.literal(True)
        checks: list[NumericCheck] = []
        branches: list[tuple[ir.Value, ir.Value]] = []
        for condition_op, result_op in zip(node.cases, node.results, strict=True):
            condition, result = visit(condition_op), visit(result_op)
            selected = condition.value.fill_null(False)
            checks.extend(_masked(condition.checks, remaining))
            checks.extend(_masked(result.checks, remaining & selected))
            branches.append((selected, result.value))
            remaining = remaining & ~selected
        default = visit(node.default)
        checks.extend(_masked(default.checks, remaining))
        return NumericValue(ibis.cases(*branches, else_=default.value), tuple(checks))
    if isinstance(node, ops.Coalesce):
        remaining = ibis.literal(True)
        items: list[ir.Value] = []
        checks = []
        for child in node.arg:
            item = visit(child)
            checks.extend(_masked(item.checks, remaining))
            remaining = remaining & item.value.isnull()
            items.append(item.value)
        return NumericValue(ibis.coalesce(*items), tuple(checks))
    if isinstance(node, (ops.Sqrt, ops.Ln, ops.Log, ops.Log2, ops.Log10, ops.Exp)):
        item = visit(node.arg)
        argument = item.value
        violation = argument < 0 if isinstance(node, ops.Sqrt) else argument <= 0
        if isinstance(node, ops.Exp):
            violation = argument > math.log(float.fromhex("0x1.fffffffffffffp+1023"))
        safe = violation.ifelse(ibis.null().cast(argument.type()), argument)
        checks = [
            *item.checks,
            NumericCheck(violation, "valid finite numeric function inputs", type(node).__name__),
        ]
        kwargs: dict[str, object] = {"arg": safe.op()}
        if isinstance(node, ops.Log) and node.base is not None:
            base = visit(node.base)
            bad_base = (base.value <= 0) | (base.value == 1)
            kwargs["base"] = bad_base.ifelse(ibis.null().cast(base.value.type()), base.value).op()
            checks.extend(
                (
                    *base.checks,
                    NumericCheck(bad_base, "positive logarithm base other than one", "log"),
                )
            )
        value = node.copy(**kwargs).to_expr()
        return NumericValue(value, (*checks, *_finite_check(value, type(node).__name__)))
    # These operations have no eager invalid-value behavior. Cast is handled with
    # TryCast so invalid demanded inputs become a structured check, not a backend exception.
    if isinstance(node, ops.Round) and node.dtype.is_integer():
        item, digits = visit(node.arg), visit(node.digits)
        overflow = (item.value < -(2**63)) | (item.value >= float(2**63))
        safe = overflow.ifelse(ibis.null().cast(item.value.type()), item.value)
        value = node.copy(arg=safe.op(), digits=digits.value.op()).to_expr()
        return NumericValue(
            value,
            (
                *item.checks,
                *digits.checks,
                NumericCheck(overflow, "signed int64 rounded values", "round: integer overflow"),
            ),
        )
    if isinstance(node, ops.Cast):
        item = visit(node.arg)
        value = item.value.try_cast(node.to)
        bad = item.value.notnull() & value.isnull()
        return NumericValue(
            value,
            (
                *item.checks,
                NumericCheck(bad, f"values castable to {node.to}", "cast"),
                *_finite_check(value, "cast"),
            ),
        )
    allowed = (
        ops.Abs,
        ops.Round,
        ops.Clip,
        ops.Least,
        ops.Greatest,
        ops.NullIf,
        ops.IsNull,
        ops.NotNull,
        ops.InValues,
        ops.Not,
        ops.And,
        ops.Or,
        ops.Xor,
        ops.Equals,
        ops.NotEquals,
        ops.Greater,
        ops.GreaterEqual,
        ops.Less,
        ops.LessEqual,
        ops.IsNan,
        ops.IsInf,
    )
    if not isinstance(node, allowed):
        numeric_failure("a qualified native numeric operation", type(node).__name__)
    gathered: list[NumericCheck] = []

    def replace(value: object) -> object:
        if isinstance(value, ops.Value):
            item = visit(value)
            gathered.extend(item.checks)
            return item.value.op()
        if isinstance(value, tuple):
            return tuple(replace(child) for child in value)
        return value

    value = node.copy(
        **{name: replace(arg) for name, arg in zip(node.argnames, node.args, strict=True)}
    ).to_expr()
    return NumericValue(value, (*gathered, *_finite_check(value, type(node).__name__)))
