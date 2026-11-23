"""Demand-driven pandas evaluation of the prepared native Ibis numeric DAG."""

from __future__ import annotations

import math
import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.semantic.spi import numeric_failure


def _is_null(value: object) -> bool:
    """Use captured Arrow validity rather than pandas' floating NaN convention."""
    return value is None or value is pd.NA or value is pd.NaT


def _nulls(values: pd.Series) -> pd.Series:
    return pd.Series([_is_null(value) for value in values], index=values.index)


def _float64_operand(values: pd.Series) -> pd.Series:
    """Allow ordinary rounding only at an already-bound Float64 arithmetic boundary."""
    if values.dtype == pd.ArrowDtype(pa.float64()):
        return values
    converted = pc.cast(
        pa.array(values, from_pandas=False),
        options=pc.CastOptions(pa.float64(), allow_float_truncate=True),
    )
    return pd.Series(pd.arrays.ArrowExtensionArray(converted), index=values.index)


_BINARY_FUNCTIONS = {
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


@dataclass(frozen=True, slots=True)
class _NumericDemand:
    values: Mapping[str, pd.Series]
    index: pd.Index
    active: pd.Series
    exact_division: bool
    output_name: str | None

    def visit(self, child: ops.Value, needed: pd.Series | None = None) -> pd.Series:
        return evaluate_numeric(
            child,
            self.values,
            self.index,
            self.active if needed is None else needed,
            exact_division=self.exact_division,
            output_name=self.output_name,
        )

    def constant(self, value: object) -> pd.Series:
        if value is None:
            return pd.Series([None] * len(self.index), index=self.index, dtype=object)
        return pd.Series(
            pd.arrays.ArrowExtensionArray(
                pa.array([value if selected else None for selected in self.active])
            ),
            index=self.index,
        )

    def unary(
        self, arg: ops.Value, function: Callable[[object], object], *, nulls: bool = False
    ) -> pd.Series:
        source = self.visit(arg)
        result = self.constant(None)
        for key in self.index[self.active]:
            value = source.loc[key]
            if not _is_null(value) or nulls:
                result.loc[key] = function(value)
        return result

    def binary(
        self,
        node: ops.Value,
        left: ops.Value,
        right: ops.Value,
        function: Callable[[object, object], object],
    ) -> pd.Series:
        a, b = self.visit(left), self.visit(right)
        if self.exact_division or (
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
                index=self.index,
                dtype=object,
            )
        if (
            isinstance(node, (ops.Add, ops.Subtract, ops.Multiply, ops.Divide))
            and node.dtype == dt.float64
        ):
            a, b = _float64_operand(a), _float64_operand(b)
        computed = function(a, b)
        if not isinstance(computed, pd.Series):
            raise TypeError("native binary kernel must return a Series")
        return computed.mask(~self.active, inplace=False)

    def read(self, node: ops.Field | ops.Literal) -> pd.Series:
        if isinstance(node, ops.Field):
            source = self.values[node.name]
            if (
                source.dtype == object
                and (node.dtype.is_numeric() or node.dtype.is_boolean())
                and not self.exact_division
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
                                for v, selected in zip(source, self.active, strict=True)
                            ],
                            type=node.dtype.to_pyarrow(),
                        )
                    ),
                    index=self.index,
                )
            return source.mask(~self.active, None)
        return pd.Series(
            pd.arrays.ArrowExtensionArray(
                pa.array(
                    [node.value if selected else None for selected in self.active],
                    type=node.dtype.to_pyarrow(),
                )
            ),
            index=self.index,
        )

    def choose(
        self, node: ops.IfElse | ops.SearchedCase | ops.Coalesce | ops.And | ops.Or
    ) -> pd.Series:
        if isinstance(node, ops.IfElse):
            condition = self.visit(node.bool_expr).astype("boolean").fillna(False).astype(bool)
            yes, no = (
                self.visit(node.true_expr, self.active & condition),
                self.visit(node.false_null_expr, self.active & ~condition),
            )
            if self.exact_division:
                return yes.astype(object).where(condition, no)
            return pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pc.if_else(pa.array(condition), pa.array(yes), pa.array(no))
                ),
                index=self.index,
            )
        if isinstance(node, ops.SearchedCase):
            remaining = self.active.copy()
            result = self.constant(None)
            for condition_op, value_op in zip(node.cases, node.results, strict=True):
                condition = (
                    self.visit(condition_op, remaining).astype("boolean").fillna(False).astype(bool)
                )
                selected = remaining & condition
                part = self.visit(value_op, selected)
                result.loc[selected] = part.loc[selected]
                remaining &= ~condition
            part = self.visit(node.default, remaining)
            result.loc[remaining] = part.loc[remaining]
            return pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(
                        [None if _is_null(v) else v for v in result], type=node.dtype.to_pyarrow()
                    )
                ),
                index=self.index,
            )
        if isinstance(node, ops.Coalesce):
            remaining = self.active.copy()
            result = self.constant(None)
            for child in node.arg:
                part = self.visit(child, remaining)
                selected = remaining & ~_nulls(part)
                result.loc[selected] = part.loc[selected]
                remaining &= _nulls(part)
            return pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(
                        [None if _is_null(v) else v for v in result], type=node.dtype.to_pyarrow()
                    )
                ),
                index=self.index,
            )
        # Both operands remain in the static graph. Demand short circuit here
        # also protects branch-masked NumericCheck violation expressions.
        left = self.visit(node.left).astype("boolean")
        needed = self.active & (
            left.fillna(True) if isinstance(node, ops.And) else ~left.fillna(False)
        )
        right = self.visit(node.right, needed).astype("boolean")
        result = left & right if isinstance(node, ops.And) else left | right
        return result.astype(object).where(self.active, self.constant(None))

    def convert(self, node: ops.Cast | ops.TryCast) -> pd.Series:

        if isinstance(node.arg, ops.Literal) and (
            node.arg.value is None
            or (node.to.is_decimal() and isinstance(node.arg.value, (int, float)))
        ):
            return pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(
                        [node.arg.value if selected else None for selected in self.active],
                        type=node.to.to_pyarrow(),
                    )
                ),
                index=self.index,
            )
        source = self.visit(node.arg)
        if self.exact_division and node.to.is_numeric():
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
                index=self.index,
            )
        if node.to == dt.float64 and node.arg.dtype.is_numeric():
            return _float64_operand(source)
        return source.astype(pd.ArrowDtype(node.to.to_pyarrow()))

    def predicate(
        self, node: ops.IsNull | ops.NotNull | ops.Not | ops.InValues | ops.IsNan | ops.IsInf
    ) -> pd.Series:
        if isinstance(node, ops.IsNull):
            return self.unary(
                node.arg,
                _is_null,
                nulls=True,
            )
        if isinstance(node, ops.NotNull):
            return self.unary(
                node.arg,
                lambda value: not _is_null(value),
                nulls=True,
            )
        if isinstance(node, ops.Not):
            return self.unary(node.arg, lambda value: not value)
        if isinstance(node, ops.InValues):
            value = self.visit(node.value)
            result = pd.Series(False, index=self.index, dtype="boolean")
            for option in node.options:
                result |= (value == self.visit(option)).astype("boolean")
            return result.mask(~self.active)
        if isinstance(node, ops.IsNan):
            return self.unary(
                node.arg, lambda value: isinstance(value, float) and math.isnan(value), nulls=True
            )
        if isinstance(node, ops.IsInf):
            return self.unary(
                node.arg, lambda value: isinstance(value, float) and math.isinf(value), nulls=True
            )
        raise AssertionError(type(node).__name__)

    def calculate(
        self,
        node: ops.Abs
        | ops.Negate
        | ops.Sqrt
        | ops.Exp
        | ops.Ln
        | ops.Log2
        | ops.Log10
        | ops.Log
        | ops.Round,
    ) -> pd.Series:
        if isinstance(node, (ops.Abs, ops.Negate)):
            source = self.visit(node.arg)
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
                pd.arrays.ArrowExtensionArray(function(pa.array(self.visit(node.arg)))),
                index=self.index,
            )
        if isinstance(node, ops.Log):
            source = pa.array(self.visit(node.arg))
            result = (
                pc.ln(source)
                if node.base is None
                else pc.logb(source, pa.array(self.visit(node.base)))
            )
            return pd.Series(pd.arrays.ArrowExtensionArray(result), index=self.index)
        if isinstance(node, ops.Round):
            digits = int(node.digits.value) if isinstance(node.digits, ops.Literal) else 0

            def rounded(value: object) -> object:
                if not isinstance(value, (int, float, Decimal)):
                    raise TypeError("round requires a numeric value")
                return round(value, digits)

            return self.unary(node.arg, rounded)
        raise AssertionError(type(node).__name__)

    def bound(self, node: ops.NullIf | ops.Least | ops.Greatest | ops.Clip) -> pd.Series:
        if isinstance(node, ops.NullIf):
            left, right = self.visit(node.arg), self.visit(node.null_if_expr)
            return left.mask((left == right).fillna(False), None)
        if isinstance(node, (ops.Least, ops.Greatest)):
            parts = [self.visit(child) for child in node.arg]
            result = self.constant(None)
            for key in self.index[self.active]:
                present = [part.loc[key] for part in parts if not _is_null(part.loc[key])]
                if present:
                    result.loc[key] = min(present) if isinstance(node, ops.Least) else max(present)
            return result
        if isinstance(node, ops.Clip):
            result = self.visit(node.arg)
            if node.lower is not None:
                lower = self.visit(node.lower)
                result = result.where(
                    _nulls(result) | _nulls(lower) | (result >= lower).fillna(False), lower
                )
            if node.upper is not None:
                upper = self.visit(node.upper)
                result = result.where(
                    _nulls(result) | _nulls(upper) | (result <= upper).fillna(False), upper
                )
            return result
        raise AssertionError(type(node).__name__)


def evaluate_numeric(
    node: ops.Value,
    values: Mapping[str, pd.Series],
    index: pd.Index,
    demand: pd.Series | None = None,
    *,
    exact_division: bool = False,
    output_name: str | None = None,
) -> pd.Series:
    """Evaluate only demanded coordinates; static admission already covered all branches."""
    active = (
        pd.Series(True, index=index)
        if demand is None
        else demand.astype("boolean").fillna(False).astype(bool)
    )

    context = _NumericDemand(values, index, active, exact_division, output_name)

    try:
        if isinstance(node, (ops.Field, ops.Literal)):
            return context.read(node)
        if isinstance(node, ops.Alias):
            return context.visit(node.arg)
        if isinstance(node, (ops.IfElse, ops.SearchedCase, ops.Coalesce, ops.And, ops.Or)):
            return context.choose(node)
        if isinstance(node, ops.Divide) and exact_division:
            a, b = context.visit(node.left), context.visit(node.right)
            return pd.Series(
                [
                    None if _is_null(x) or _is_null(y) else Fraction(x) / Fraction(y)
                    for x, y in zip(a, b, strict=True)
                ],
                index=index,
                dtype=object,
            )
        if type(node) in _BINARY_FUNCTIONS:
            return context.binary(node, node.left, node.right, _BINARY_FUNCTIONS[type(node)])
        if isinstance(node, (ops.Cast, ops.TryCast)):
            return context.convert(node)
        if isinstance(node, (ops.IsNull, ops.NotNull, ops.Not, ops.InValues, ops.IsNan, ops.IsInf)):
            return context.predicate(node)
        if isinstance(
            node,
            (
                ops.Abs,
                ops.Negate,
                ops.Sqrt,
                ops.Exp,
                ops.Ln,
                ops.Log2,
                ops.Log10,
                ops.Log,
                ops.Round,
            ),
        ):
            return context.calculate(node)
        if isinstance(node, (ops.NullIf, ops.Least, ops.Greatest, ops.Clip)):
            return context.bound(node)
    except (ArithmeticError, TypeError, ValueError) as error:
        fields = sorted({field.name for field in node.find(ops.Field)})
        inputs = ", ".join(f"{name}: {values[name].dtype}" for name in fields[:6])
        if len(fields) > 6:
            inputs += f", ... ({len(fields)} inputs)"
        output_context = f"output={output_name!r}; " if output_name is not None else ""
        numeric_failure(
            "valid demanded numeric values",
            f"{output_context}expression={type(node).__name__}; inputs=[{inputs}]; {error}",
        )
    numeric_failure("a qualified local Ibis numeric operation", type(node).__name__)
