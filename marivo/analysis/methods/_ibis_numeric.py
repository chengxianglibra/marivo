"""Demand-driven pandas evaluation of the prepared native Ibis numeric DAG."""

from __future__ import annotations

import math
import operator
from collections.abc import Callable, Mapping
from decimal import Decimal
from fractions import Fraction

import ibis.expr.operations as ops
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.semantic._numeric_lowering import numeric_failure


def _is_null(value: object) -> bool:
    """Use captured Arrow validity rather than pandas' floating NaN convention."""
    return value is None or value is pd.NA or value is pd.NaT


def _nulls(values: pd.Series) -> pd.Series:
    return pd.Series([_is_null(value) for value in values], index=values.index)


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
        if value is None:
            return pd.Series([None] * len(index), index=index, dtype=object)
        return pd.Series(
            pd.arrays.ArrowExtensionArray(
                pa.array([value if selected else None for selected in active])
            ),
            index=index,
        )

    def unary(
        arg: ops.Value, function: Callable[[object], object], *, nulls: bool = False
    ) -> pd.Series:
        source = visit(arg)
        result = constant(None)
        for key in index[active]:
            value = source.loc[key]
            if not _is_null(value) or nulls:
                result.loc[key] = function(value)
        return result

    def binary(
        left: ops.Value, right: ops.Value, function: Callable[[object, object], object]
    ) -> pd.Series:
        a, b = visit(left), visit(right)
        if exact_division or (
            any(
                isinstance(series.dtype, pd.ArrowDtype)
                and pa.types.is_decimal(series.dtype.pyarrow_dtype)
                for series in (a, b)
            )
            and not any(
                isinstance(series.dtype, pd.ArrowDtype)
                and pa.types.is_floating(series.dtype.pyarrow_dtype)
                for series in (a, b)
            )
        ):
            return pd.Series(
                [
                    None if _is_null(x) or _is_null(y) else function(x, y)
                    for x, y in zip(a, b, strict=True)
                ],
                index=index,
                dtype=object,
            )
        computed = function(a, b)
        if not isinstance(computed, pd.Series):
            raise TypeError("native binary kernel must return a Series")
        return computed.mask(~active, inplace=False)

    try:
        if isinstance(node, ops.Field):
            source = values[node.name]
            if (
                source.dtype == object
                and (node.dtype.is_numeric() or node.dtype.is_boolean())
                and not exact_division
            ):
                return pd.Series(
                    pd.arrays.ArrowExtensionArray(
                        pa.array(
                            [
                                None
                                if not selected
                                or _is_null(v)
                                or (
                                    not node.dtype.is_floating()
                                    and isinstance(v, float)
                                    and math.isnan(v)
                                )
                                else v
                                for v, selected in zip(source, active, strict=True)
                            ],
                            type=node.dtype.to_pyarrow(),
                        )
                    ),
                    index=index,
                )
            return source.mask(~active, None)
        if isinstance(node, ops.Literal):
            return pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(
                        [node.value if selected else None for selected in active],
                        type=node.dtype.to_pyarrow(),
                    )
                ),
                index=index,
            )
        if isinstance(node, ops.Alias):
            return visit(node.arg)
        if isinstance(node, ops.IfElse):
            condition = visit(node.bool_expr).astype("boolean").fillna(False).astype(bool)
            yes, no = (
                visit(node.true_expr, active & condition),
                visit(node.false_null_expr, active & ~condition),
            )
            if exact_division:
                return yes.astype(object).where(condition, no)
            return pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pc.if_else(pa.array(condition), pa.array(yes), pa.array(no))
                ),
                index=index,
            )
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
            return pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(
                        [None if _is_null(v) else v for v in result], type=node.dtype.to_pyarrow()
                    )
                ),
                index=index,
            )
        if isinstance(node, ops.Coalesce):
            remaining = active.copy()
            result = constant(None)
            for child in node.arg:
                part = visit(child, remaining)
                selected = remaining & ~_nulls(part)
                result.loc[selected] = part.loc[selected]
                remaining &= _nulls(part)
            return pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(
                        [None if _is_null(v) else v for v in result], type=node.dtype.to_pyarrow()
                    )
                ),
                index=index,
            )
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
            a, b = visit(node.left), visit(node.right)
            return pd.Series(
                [
                    None if _is_null(x) or _is_null(y) else Fraction(x) / Fraction(y)
                    for x, y in zip(a, b, strict=True)
                ],
                index=index,
                dtype=object,
            )
        if type(node) in binary_functions:
            return binary(node.left, node.right, binary_functions[type(node)])
        if isinstance(node, (ops.Cast, ops.TryCast)):
            if isinstance(node.arg, ops.Literal) and (
                node.arg.value is None
                or (node.to.is_decimal() and isinstance(node.arg.value, (int, float)))
            ):
                return pd.Series(
                    pd.arrays.ArrowExtensionArray(
                        pa.array(
                            [node.arg.value if selected else None for selected in active],
                            type=node.to.to_pyarrow(),
                        )
                    ),
                    index=index,
                )
            source = visit(node.arg)
            if exact_division and node.to.is_numeric():
                return source
            if isinstance(node, ops.TryCast):
                output: list[object] = []
                for value in source:
                    try:
                        output.append(
                            pa.scalar(None if _is_null(value) else value)
                            .cast(node.to.to_pyarrow())
                            .as_py()
                        )
                    except (pa.ArrowException, ArithmeticError, ValueError, TypeError):
                        output.append(None)
                return pd.Series(
                    pd.arrays.ArrowExtensionArray(pa.array(output, type=node.to.to_pyarrow())),
                    index=index,
                )
            return source.astype(pd.ArrowDtype(node.to.to_pyarrow()))
        if isinstance(node, ops.IsNull):
            return unary(
                node.arg,
                _is_null,
                nulls=True,
            )
        if isinstance(node, ops.NotNull):
            return unary(
                node.arg,
                lambda value: not _is_null(value),
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
        if isinstance(node, (ops.Abs, ops.Negate)):
            source = visit(node.arg)
            return source.abs() if isinstance(node, ops.Abs) else -source
        if isinstance(node, (ops.Sqrt, ops.Exp, ops.Ln, ops.Log2, ops.Log10)):
            function = {
                ops.Sqrt: pc.sqrt,
                ops.Exp: pc.exp,
                ops.Ln: pc.ln,
                ops.Log2: pc.log2,
                ops.Log10: pc.log10,
            }[type(node)]
            return pd.Series(
                pd.arrays.ArrowExtensionArray(function(pa.array(visit(node.arg)))), index=index
            )
        if isinstance(node, ops.Log):
            source = pa.array(visit(node.arg))
            result = (
                pc.ln(source) if node.base is None else pc.logb(source, pa.array(visit(node.base)))
            )
            return pd.Series(pd.arrays.ArrowExtensionArray(result), index=index)
        if isinstance(node, ops.Round):
            digits = int(node.digits.value) if isinstance(node.digits, ops.Literal) else 0

            def rounded(value: object) -> object:
                if not isinstance(value, (int, float, Decimal)):
                    raise TypeError("round requires a numeric value")
                return round(value, digits)

            return unary(node.arg, rounded)
        if isinstance(node, ops.NullIf):
            left, right = visit(node.arg), visit(node.null_if_expr)
            return left.mask((left == right).fillna(False), None)
        if isinstance(node, (ops.Least, ops.Greatest)):
            parts = [visit(child) for child in node.arg]
            result = constant(None)
            for key in index[active]:
                present = [part.loc[key] for part in parts if not _is_null(part.loc[key])]
                if present:
                    result.loc[key] = min(present) if isinstance(node, ops.Least) else max(present)
            return result
        if isinstance(node, ops.Clip):
            result = visit(node.arg)
            if node.lower is not None:
                lower = visit(node.lower)
                result = result.where(
                    _nulls(result) | _nulls(lower) | (result >= lower).fillna(False), lower
                )
            if node.upper is not None:
                upper = visit(node.upper)
                result = result.where(
                    _nulls(result) | _nulls(upper) | (result <= upper).fillna(False), upper
                )
            return result
    except (ArithmeticError, TypeError, ValueError) as error:
        numeric_failure("valid demanded numeric values", f"{type(node).__name__}: {error}")
    numeric_failure("a qualified local Ibis numeric operation", type(node).__name__)
