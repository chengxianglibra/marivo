"""Explicit source-free rescaling of typed semantic numeric expressions."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import overload

from ibis.common.deferred import Deferred

from marivo._definition.deferred import RescaleResolver, resolver_of
from marivo.semantic._expression_values import (
    MetricExpression,
    NumericMetric,
    NumericRow,
    RowExpression,
)
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.unit_algebra import render_unit, unit_state


@overload
def rescale(expr: NumericRow, *, divisor: int | float | Decimal, unit: str) -> NumericRow: ...


@overload
def rescale(expr: NumericMetric, *, divisor: int | float | Decimal, unit: str) -> NumericMetric: ...


@overload
def rescale(expr: Deferred, *, divisor: int | float | Decimal, unit: str) -> Deferred: ...


def rescale(
    expr: Deferred | NumericRow | NumericMetric, *, divisor: int | float | Decimal, unit: str
) -> Deferred | NumericRow | NumericMetric:
    """Divide a numeric expression and explicitly assert its resulting unit.

    Args:
        expr: A numeric Row or completed Metric Deferred expression with a known
            unit after binding its semantic dependencies.
        divisor: A finite, strictly positive int, float or Decimal literal.
            Boolean values and dynamic expressions are rejected.
        unit: A nonempty printable ASCII unit token without whitespace. Existing
            factorable units are canonicalized; opaque unit tokens are retained.
    Returns:
        A Deferred expression at the same stage whose value is expr / divisor
        and whose unit is the explicit target unit.
    Example:
        >>> bandwidth = ms.rescale(total_bytes.sum(), divisor=86400 * 1024**3, unit="GiB/s")
    Constraints:
        Source roots, time restrictions and sum permissions are preserved. Existing
        numeric and null rules apply. The author owns the business and physical
        correctness of divisor and unit, including cross-dimension assertions.
        No unit conversion is inferred; divisor=1 is a valid explicit assertion.
        Stage and input-unit checks occur when the recipe is bound or consumed.
    """
    if not isinstance(expr, (Deferred, RowExpression, MetricExpression)) or (
        isinstance(expr, (RowExpression, MetricExpression)) and expr._role != "numeric"
    ):
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_STAGE,
            message="rescale requires a numeric Row or completed Metric expression.",
            expected="a numeric Row or Metric Deferred expression",
            received=type(expr).__name__,
            help_target="rescale",
            action="Pass a Measure expression or a completed numeric reduction to ms.rescale.",
        )
    finite = (
        type(divisor) is int
        or (type(divisor) is float and math.isfinite(divisor))
        or (isinstance(divisor, Decimal) and divisor.is_finite())
    )
    if not finite or divisor <= 0:
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            message="rescale divisor must be a finite, strictly positive numeric literal.",
            expected="a finite int, float or Decimal greater than zero, excluding bool",
            received=repr(divisor),
            help_target="rescale",
            action="Pass a finite positive numeric literal as divisor.",
        )
    try:
        if not isinstance(unit, str):
            raise ValueError("unit must be a string")
        canonical_unit = render_unit(unit_state(unit))
    except ValueError as error:
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            message="rescale requires a valid target unit token.",
            expected="a nonempty printable ASCII unit token without whitespace",
            received=repr(unit),
            help_target="rescale",
            action="Use a unit token such as 'GiB/s', '1', or '{request}'.",
        ) from error
    assert canonical_unit is not None
    if isinstance(expr, RowExpression):
        return RowExpression(
            RescaleResolver(expr._recipe, divisor, canonical_unit), expr._context, "numeric"
        )
    if isinstance(expr, MetricExpression):
        return MetricExpression(
            RescaleResolver(expr._recipe, divisor, canonical_unit), expr._context, "numeric"
        )
    return Deferred(RescaleResolver(resolver_of(expr), divisor, canonical_unit))
