"""Unit and null policy for controlled expression operations."""

from __future__ import annotations

from typing import Literal

from marivo._definition.types import (
    NullPolicy,
    SumAll,
    SumAllExcept,
    SumAxes,
    SumNone,
    SumPermission,
)
from marivo.semantic.errors import ErrorKind
from marivo.semantic.expressions.objects import (
    _SYMBOLS,
    RowOperator,
    _fail,
    _received,
)
from marivo.semantic.unit_algebra import (
    divide_unit_states,
    linear_unit,
    linear_units_conflict,
    multiply_unit_states,
    render_unit,
    unit_state,
)


def _intersect(left: SumPermission, right: SumPermission) -> SumPermission:
    """Sum permission granted by both operands; never wider than either input."""
    if isinstance(left, SumNone) or isinstance(right, SumNone):
        return SumNone()
    if isinstance(left, SumAll):
        return right
    if isinstance(right, SumAll):
        return left
    left_refs = {axis.ref for axis in left.axes}
    right_refs = {axis.ref for axis in right.axes}
    if isinstance(left, SumAllExcept) and isinstance(right, SumAllExcept):
        return SumAllExcept(
            (*left.axes, *(axis for axis in right.axes if axis.ref not in left_refs))
        )
    if isinstance(left, SumAxes) and isinstance(right, SumAxes):
        axes = tuple(axis for axis in left.axes if axis.ref in right_refs)
    elif isinstance(left, SumAxes) and isinstance(right, SumAllExcept):
        axes = tuple(axis for axis in left.axes if axis.ref not in right_refs)
    else:
        assert isinstance(left, SumAllExcept) and isinstance(right, SumAxes)
        axes = tuple(axis for axis in right.axes if axis.ref not in left_refs)
    return SumAxes(axes) if axes else SumNone()


def _arith_unit(
    op: RowOperator,
    left: str | None,
    right: str | None,
    *,
    constant: Literal["left", "right"] | None,
    stage: Literal["Row", "Metric"],
) -> str | None:
    """Unit of one arithmetic use; a bare constant takes the other operand's unit for
    addition and subtraction and is dimensionless for multiplication and division."""
    if constant == "left":
        if op == "divide":
            return render_unit(divide_unit_states(unit_state("1"), unit_state(right)))
        return right
    if constant == "right":
        return left
    if op in ("add", "subtract"):
        if linear_units_conflict((left, right)):
            symbol = _SYMBOLS[op]
            _fail(
                ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
                f"{stage} {symbol} combines incompatible units {left} and {right}.",
                expected="operands with one commensurable unit",
                received=f"{left} {symbol} {right}",
                action=(
                    "Convert one operand to the other unit explicitly, then combine again; "
                    "units are never converted implicitly."
                ),
            )
        return linear_unit((left, right))
    states = (unit_state(left), unit_state(right))
    if op == "multiply":
        return render_unit(multiply_unit_states(*states))
    return render_unit(divide_unit_states(*states))


def _nulls(value: object, *, operation: str) -> NullPolicy:
    if value == "ignore":
        return "ignore"
    if value == "error":
        return "error"
    _fail(
        ErrorKind.INVALID_EXPRESSION_VALUE,
        f"{operation} received an unsupported nulls policy.",
        expected='nulls="ignore" or nulls="error"',
        received=_received(value),
        action='Use "ignore" (default) or "error" for this reduction.',
    )


def _quantile(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            "quantile received a q that is not a number.",
            expected="a finite float q with 0 <= q <= 1",
            received=_received(value),
            action="Pass q such as 0.95.",
        )
    if not 0 <= value <= 1:
        # Range-check integers before float conversion, which overflows for huge values.
        _fail(
            ErrorKind.INVALID_EXPRESSION_VALUE,
            "quantile received a q outside [0, 1].",
            expected="a finite float q with 0 <= q <= 1",
            received=_received(value),
            action="Pass q such as 0.95.",
        )
    return float(value)
