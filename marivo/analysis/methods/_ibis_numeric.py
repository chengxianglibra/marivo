"""Demand-driven pandas evaluation of the prepared native Ibis numeric DAG."""

from __future__ import annotations

import math
import operator
from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

import ibis.expr.operations as ops
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.semantic._numeric_lowering import numeric_failure


def evaluate_numeric(
    node: ops.Value,
    values: Mapping[str, pd.Series],
    index: pd.Index,
    demand: pd.Series | None = None,
    *,
    exact_division: bool = False,
) -> pd.Series:
    """Evaluate only demanded coordinates; static admission already covered all branches."""
    active = (
        pd.Series(True, index=index)
        if demand is None
        else demand.astype("boolean").fillna(False).astype(bool)
    )

    def visit(child: ops.Value, needed: pd.Series = active) -> pd.Series:
        return evaluate_numeric(child, values, index, needed, exact_division=exact_division)

    def constant(value: object) -> pd.Series:
        return pd.Series(
            [value if selected else None for selected in active], index=index, dtype=object
        )

    def unary(
        arg: ops.Value, function: Callable[[object], object], *, nulls: bool = False
    ) -> pd.Series:
        source = visit(arg)
        result = constant(None)
        for key in index[active]:
            value = source.loc[key]
            if pd.notna(value) or nulls:
                result.loc[key] = function(value)
        return result

    def binary(
        left: ops.Value, right: ops.Value, function: Callable[[object, object], object]
    ) -> pd.Series:
        a, b = visit(left), visit(right)
        result = constant(None)
        with localcontext() as context:
            context.prec = 120
            for key in index[active]:
                x, y = a.loc[key], b.loc[key]
                if pd.notna(x) and pd.notna(y):
                    if isinstance(x, float) and isinstance(y, Decimal):
                        y = float(y)
                    elif isinstance(x, Decimal) and isinstance(y, float):
                        x = float(x)
                    result.loc[key] = function(x, y)
        return result

    try:
        if isinstance(node, ops.Field):
            return values[node.name].where(active, constant(None)).astype(object)
        if isinstance(node, ops.Literal):
            return constant(node.value)
        if isinstance(node, ops.Alias):
            return visit(node.arg)
        if isinstance(node, ops.IfElse):
            condition = visit(node.bool_expr).astype("boolean").fillna(False).astype(bool)
            yes, no = (
                visit(node.true_expr, active & condition),
                visit(node.false_null_expr, active & ~condition),
            )
            return yes.where(condition, no)
        if isinstance(node, ops.SearchedCase):
            remaining = active.copy()
            result = constant(None)
            for condition_op, value_op in zip(node.cases, node.results, strict=True):
                condition = (
                    visit(condition_op, remaining).astype("boolean").fillna(False).astype(bool)
                )
                selected = remaining & condition
                part = visit(value_op, selected)
                result.loc[selected] = part.loc[selected]
                remaining &= ~condition
            part = visit(node.default, remaining)
            result.loc[remaining] = part.loc[remaining]
            return result
        if isinstance(node, ops.Coalesce):
            remaining = active.copy()
            result = constant(None)
            for child in node.arg:
                part = visit(child, remaining)
                selected = remaining & part.notna()
                result.loc[selected] = part.loc[selected]
                remaining &= part.isna()
            return result
        if isinstance(node, (ops.And, ops.Or)):
            # Both operands remain in the static graph. Demand short circuit here
            # also protects branch-masked NumericCheck violation expressions.
            left = visit(node.left).astype("boolean")
            needed = active & (
                left.fillna(True) if isinstance(node, ops.And) else ~left.fillna(False)
            )
            right = visit(node.right, needed).astype("boolean")
            result = left & right if isinstance(node, ops.And) else left | right
            return result.astype(object).where(active, constant(None))
        binary_functions = {
            ops.Add: operator.add,
            ops.Subtract: operator.sub,
            ops.Multiply: operator.mul,
            ops.Divide: operator.truediv,
            ops.Equals: operator.eq,
            ops.NotEquals: operator.ne,
            ops.Greater: operator.gt,
            ops.GreaterEqual: operator.ge,
            ops.Less: operator.lt,
            ops.LessEqual: operator.le,
            ops.Xor: operator.xor,
        }
        if isinstance(node, ops.Divide) and exact_division:
            return binary(
                node.left,
                node.right,
                lambda left, right: Fraction(str(left)) / Fraction(str(right)),
            )
        if isinstance(node, (ops.Add, ops.Subtract, ops.Multiply)) and node.dtype.is_integer():
            kernel = {
                ops.Add: pc.add_checked,
                ops.Subtract: pc.subtract_checked,
                ops.Multiply: pc.multiply_checked,
            }[type(node)]
            return binary(
                node.left,
                node.right,
                lambda a, b: kernel(
                    pa.scalar(a, type=pa.int64()), pa.scalar(b, type=pa.int64())
                ).as_py(),
            )
        if type(node) in binary_functions:
            return binary(node.left, node.right, binary_functions[type(node)])
        if isinstance(node, (ops.Cast, ops.TryCast)):

            def convert(value: object) -> object:
                try:
                    if node.to.is_floating():
                        return float(str(value))
                    if node.to.is_integer():
                        integer = (
                            int(value)
                            if isinstance(value, bool)
                            else round(value)
                            if isinstance(value, float)
                            else int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))
                        )
                        return integer if -(2**63) <= integer < 2**63 else None
                    if node.to.is_decimal():
                        with localcontext() as context:
                            context.prec = 120
                            decimal = Decimal(str(value)).quantize(
                                Decimal(1).scaleb(-(node.to.scale or 0)), rounding=ROUND_HALF_UP
                            )
                        return pa.scalar(
                            decimal, type=pa.decimal128(node.to.precision or 38, node.to.scale or 0)
                        ).as_py()
                    if node.to.is_boolean():
                        return bool(value)
                    if node.to.is_string():
                        return str(value)
                    return value
                except (ArithmeticError, ValueError, TypeError):
                    if isinstance(node, ops.TryCast):
                        return None
                    raise

            return unary(node.arg, convert)
        if isinstance(node, ops.IsNull):
            return unary(
                node.arg,
                lambda value: (
                    value is None
                    or value is pd.NA
                    or (isinstance(value, float) and math.isnan(value))
                ),
                nulls=True,
            )
        if isinstance(node, ops.NotNull):
            return unary(
                node.arg,
                lambda value: (
                    value is not None
                    and value is not pd.NA
                    and not (isinstance(value, float) and math.isnan(value))
                ),
                nulls=True,
            )
        if isinstance(node, ops.Not):
            return unary(node.arg, lambda value: not value)
        if isinstance(node, ops.IsNan):
            return unary(
                node.arg, lambda value: isinstance(value, float) and math.isnan(value), nulls=True
            )
        if isinstance(node, ops.IsInf):
            return unary(
                node.arg, lambda value: isinstance(value, float) and math.isinf(value), nulls=True
            )
        if isinstance(node, (ops.Abs, ops.Negate)) and node.dtype.is_integer():
            kernel = pc.abs_checked if isinstance(node, ops.Abs) else pc.negate_checked
            return unary(node.arg, lambda value: kernel(pa.scalar(value, type=pa.int64())).as_py())
        if isinstance(node, ops.Abs):
            return unary(
                node.arg,
                lambda value: (
                    value.copy_abs()
                    if isinstance(value, Decimal)
                    else abs(float(str(value)))
                    if isinstance(value, float)
                    else abs(int(str(value)))
                ),
            )
        if isinstance(node, ops.Negate):
            return unary(
                node.arg,
                lambda value: (
                    value.copy_negate()
                    if isinstance(value, Decimal)
                    else -float(str(value))
                    if isinstance(value, float)
                    else -int(str(value))
                ),
            )
        if isinstance(node, (ops.Sqrt, ops.Exp, ops.Ln, ops.Log2, ops.Log10)):
            functions = {
                ops.Sqrt: math.sqrt,
                ops.Exp: math.exp,
                ops.Ln: math.log,
                ops.Log2: math.log2,
                ops.Log10: math.log10,
            }
            return unary(node.arg, lambda value: functions[type(node)](float(str(value))))
        if isinstance(node, ops.Log):
            if node.base is None:
                return unary(node.arg, lambda value: math.log(float(str(value))))
            return binary(
                node.arg,
                node.base,
                lambda value, base: math.log(float(str(value)), float(str(base))),
            )
        if isinstance(node, ops.Round):

            def rounded(value: object, digits: object) -> object:
                number = Decimal(str(value)).quantize(
                    Decimal(1).scaleb(-int(str(digits))), rounding=ROUND_HALF_UP
                )
                if node.dtype.is_integer():
                    return int(number)
                return float(number) if node.dtype.is_floating() else number

            return binary(node.arg, node.digits, rounded)
        if isinstance(node, ops.NullIf):
            left, right = visit(node.arg), visit(node.null_if_expr)
            return left.mask((left == right).fillna(False), None)
        if isinstance(node, (ops.Least, ops.Greatest)):
            parts = [visit(child) for child in node.arg]
            result = constant(None)
            for key in index[active]:
                present = [part.loc[key] for part in parts if pd.notna(part.loc[key])]
                if present:
                    result.loc[key] = min(present) if isinstance(node, ops.Least) else max(present)
            return result
        if isinstance(node, ops.Clip):
            result = visit(node.arg)
            if node.lower is not None:
                lower = visit(node.lower)
                result = result.where(
                    result.isna() | lower.isna() | (result >= lower).fillna(False), lower
                )
            if node.upper is not None:
                upper = visit(node.upper)
                result = result.where(
                    result.isna() | upper.isna() | (result <= upper).fillna(False), upper
                )
            return result
    except (ArithmeticError, TypeError, ValueError) as error:
        numeric_failure("valid demanded numeric values", f"{type(node).__name__}: {error}")
    numeric_failure("a qualified local Ibis numeric operation", type(node).__name__)
