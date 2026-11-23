"""Finite-state interval certificates with the owning numeric DAG's branch demand."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction

import ibis.expr.datatypes as dt
import ibis.expr.operations as ops

from marivo.analysis.methods.attribute_game_carriers import (
    IntegerCarrier,
    arrow_float32,
    arrow_integer,
    common_integer,
    decimal_operation_bounds,
    decimal_round_fits,
    declared_integer,
    float32_literal,
    float_limit,
    integer_bounds,
    nonzero_quantum,
    outward_float_bounds,
)


class DomainProofError(ValueError):
    """The conservative fixed-state proof did not establish a complete game."""


@dataclass(frozen=True, slots=True)
class Domain:
    low: Fraction | None
    high: Fraction | None
    nullable: bool = False
    low_open: bool = False
    high_open: bool = False
    only_null: bool = False
    finite: bool = True
    nonzero: bool = False

    @classmethod
    def null(cls) -> Domain:
        return cls(None, None, True, only_null=True)

    @classmethod
    def constant(cls, value: object) -> Domain:
        if value is None:
            return cls.null()
        if isinstance(value, float) and not math.isfinite(value):
            return cls(None, None, finite=False)
        if isinstance(value, (int, float, Fraction)):
            bound = Fraction(value)
        else:
            if not isinstance(value, Decimal):
                raise DomainProofError(f"unsupported domain literal {type(value).__name__}")
            if not value.is_finite():
                return cls(None, None, finite=False)
            bound = Fraction(value)
        return cls(bound, bound)

    def excludes_zero(self) -> bool:
        return (
            self.nonzero
            or (self.low is not None and (self.low > 0 or (self.low == 0 and self.low_open)))
            or (self.high is not None and (self.high < 0 or (self.high == 0 and self.high_open)))
        )


def _union(a: Domain, b: Domain) -> Domain:
    finite = a.finite and b.finite
    if a.only_null:
        return replace(b, nullable=True, finite=finite)
    if b.only_null:
        return replace(a, nullable=True, finite=finite)
    low = None if a.low is None or b.low is None else min(a.low, b.low)
    high = None if a.high is None or b.high is None else max(a.high, b.high)
    return Domain(low, high, a.nullable or b.nullable, finite=finite)


_Truth = bool | None
_TRUE: frozenset[_Truth] = frozenset((True,))
_FALSE: frozenset[_Truth] = frozenset((False,))
_NOT_TRUE: frozenset[_Truth] = frozenset((False, None))


def _truth(value: Domain) -> frozenset[_Truth]:
    if not value.finite:
        raise DomainProofError(
            "a demanded condition contains an unqualified nonfinite Metric result"
        )
    if value.only_null:
        return frozenset((None,))
    if value.low == value.high == 0:
        values = _FALSE
    elif value.low == value.high == 1:
        values = _TRUE
    else:
        values = _FALSE | _TRUE
    return values | {None} if value.nullable else values


def _boolean(values: frozenset[_Truth]) -> Domain:
    definite = values - {None}
    if not definite:
        return Domain.null()
    return Domain(Fraction(False not in definite), Fraction(True in definite), None in values)


def _negated(values: frozenset[_Truth]) -> frozenset[_Truth]:
    return frozenset(None if value is None else not value for value in values)


def _fallback_can_be_true(
    children: tuple[ops.Value, ...], fields: dict[str, Domain], assumptions: dict[ops.Value, Domain]
) -> bool:
    for child in children:
        value = evaluate_domain(child, fields, assumptions)
        if True in _truth(value):
            return True
        if not value.nullable:
            return False
        assumptions = _assume(child, frozenset((None,)), assumptions, fields)
    return False


def _assume(
    condition: ops.Value,
    outcomes: frozenset[_Truth],
    assumptions: dict[ops.Value, Domain],
    fields: dict[str, Domain],
) -> dict[ops.Value, Domain]:
    result = dict(assumptions)
    result[condition] = _boolean(outcomes)
    if isinstance(condition, ops.Coalesce) and condition.arg:
        first = condition.arg[0]
        # Only a non-NULL first arm fixes the Coalesce result. A false
        # fallback makes an observed TRUE sufficient to exclude NULL.
        if evaluate_domain(first, fields, result).nullable and (
            outcomes != _TRUE
            or _fallback_can_be_true(
                condition.arg[1:], fields, _assume(first, frozenset((None,)), result, fields)
            )
        ):
            outcomes = outcomes | {None}
        return _assume(first, outcomes, result, fields)
    if isinstance(condition, ops.Not):
        return _assume(condition.arg, _negated(outcomes), result, fields)
    if (isinstance(condition, ops.And) and False not in outcomes) or (
        isinstance(condition, ops.Or) and True not in outcomes
    ):
        compatible = (
            outcomes if None not in outcomes else outcomes | {isinstance(condition, ops.And)}
        )
        return _assume(
            condition.right,
            compatible,
            _assume(condition.left, compatible, result, fields),
            fields,
        )
    if isinstance(condition, (ops.IsNull, ops.NotNull)) and outcomes - {None} in (_TRUE, _FALSE):
        nonnull = (True in outcomes) == isinstance(condition, ops.NotNull)
        before = evaluate_domain(condition.arg, fields, result)
        result[condition.arg] = (
            replace(before, nullable=False, only_null=False) if nonnull else Domain.null()
        )
    if isinstance(
        condition,
        (ops.Greater, ops.GreaterEqual, ops.Less, ops.LessEqual, ops.Equals, ops.NotEquals),
    ):
        _assume_comparison(condition, outcomes, result, fields)
    return result


def _assume_comparison(
    condition: ops.Comparison,
    outcomes: frozenset[_Truth],
    result: dict[ops.Value, Domain],
    fields: dict[str, Domain],
) -> None:
    left, right = condition.left, condition.right
    if _mixed_decimal_float(left.dtype, right.dtype):
        return
    right_value = evaluate_domain(right, fields, result)
    point = right_value.low
    if point is None or point != right_value.high or right_value.nullable or not right_value.finite:
        return
    before = evaluate_domain(left, fields, result)
    if outcomes == {None}:
        result[left] = Domain.null()
        return
    if outcomes - {None} not in (_TRUE, _FALSE) or before.only_null:
        return
    # SQL FALSE and UNKNOWN share a CASE arm. Refine the non-NULL values
    # using the false comparison while retaining its NULL possibility.
    before = replace(before, nullable=before.nullable and None in outcomes)
    truth = True in outcomes
    lower = isinstance(condition, (ops.Greater, ops.GreaterEqual))
    upper = isinstance(condition, (ops.Less, ops.LessEqual))
    inclusive = isinstance(condition, (ops.GreaterEqual, ops.LessEqual))
    if not truth:
        lower, upper, inclusive = upper, lower, not inclusive
    if lower and (before.low is None or point >= before.low):
        result[left] = replace(
            before, low=point, low_open=not inclusive or (point == before.low and before.low_open)
        )
    elif upper and (before.high is None or point <= before.high):
        result[left] = replace(
            before,
            high=point,
            high_open=not inclusive or (point == before.high and before.high_open),
        )
    elif not (lower or upper) and truth == isinstance(condition, ops.Equals):
        result[left] = replace(before, low=point, high=point, low_open=False, high_open=False)
    elif not (lower or upper) and point == 0:
        result[left] = replace(before, nonzero=True)
    else:
        result[left] = before


def _carrier_domain(node: ops.Value, value: Domain) -> Domain:
    """Require every demanded numeric intermediate to fit its original carrier."""
    if value.only_null or not value.finite or not node.dtype.is_numeric():
        return value
    if value.low is None or value.high is None:
        return replace(value, finite=False)
    dtype = node.dtype
    if dtype.is_integer():
        return replace(
            value,
            finite=_fits_integer(value, declared_integer(node))
            and _fits_integer(value, arrow_integer(node)),
        )
    if dtype.is_decimal():
        quantum = Fraction(1, 10 ** (dtype.scale or 0))
        upper = (10 ** (dtype.precision or 38) - 1) * quantum
        return replace(value, finite=value.low >= -upper and value.high <= upper)
    if dtype.is_floating():
        if dtype.nbytes not in (4, 8):
            return replace(value, finite=False)
        width = 4 if arrow_float32(node) else 8
        limit = float_limit(width)
        if value.low < -limit or value.high > limit:
            return replace(value, finite=False)
        if isinstance(node, ops.Literal) and width == 4:
            rounded = float32_literal(value.low)
            return Domain(rounded, rounded)
        if isinstance(node, (ops.Literal, ops.Alias, ops.Field, ops.Negate, ops.Abs)):
            return value
        if isinstance(node, (ops.Cast, ops.TryCast)) and width == 8:
            return _float64_input(value, node.arg)
        return Domain(
            *outward_float_bounds(node, width, value.low, value.high), nullable=value.nullable
        )
    return value


def _fits_integer(value: Domain, carrier: IntegerCarrier) -> bool:
    lower, upper = integer_bounds(carrier)
    return value.only_null or (
        value.low is not None
        and value.high is not None
        and value.low >= lower
        and value.high <= upper
    )


def _float64_input(value: Domain, node: ops.Value) -> Domain:
    if node.dtype.is_floating() or value.low is None or value.high is None:
        return value
    lower, upper = Fraction(float(value.low)), Fraction(float(value.high))
    if node.dtype.is_decimal() and not (value.low == value.high == 0):
        # The Arrow Decimal conversion may round both the coefficient and
        # decimal scaling, giving a nearby Float64 rather than exact RNE.
        lower = min(lower, value.low - abs(value.low) / 2**50)
        upper = max(upper, value.high + abs(value.high) / 2**50)
    return replace(
        value,
        low=lower,
        high=upper,
        low_open=False,
        high_open=False,
        nonzero=value.excludes_zero() and nonzero_quantum(node) is not None,
    )


def _mixed_decimal_float(a: dt.DataType, b: dt.DataType) -> bool:
    return bool((a.is_decimal() and b.is_floating()) or (a.is_floating() and b.is_decimal()))


def _integer_fits_float(domain: Domain, width: int) -> bool:
    limit = 2 ** (24 if width == 4 else 53)
    return domain.only_null or (
        domain.low is not None
        and domain.high is not None
        and domain.low >= -limit
        and domain.high <= limit
    )


def _safe_comparison_inputs(left: ops.Value, a: Domain, right: ops.Value, b: Domain) -> bool:
    if not a.finite or not b.finite:
        return False
    if left.dtype.is_integer() and right.dtype.is_integer():
        carrier = common_integer(arrow_integer(left), arrow_integer(right))
        return _fits_integer(a, carrier) and _fits_integer(b, carrier)
    for numeric, physical, other in ((left, a, right), (right, b, left)):
        if (
            numeric.dtype.is_integer()
            and other.dtype.is_floating()
            and not _integer_fits_float(physical, 4 if arrow_float32(other) else 8)
        ):
            return False
    return True


def evaluate_domain(
    node: ops.Value, fields: dict[str, Domain], assumptions: dict[ops.Value, Domain] | None = None
) -> Domain:
    # Assumed bounds already describe the physical result of this node.
    if assumptions is not None and node in assumptions:
        return assumptions[node]
    return _carrier_domain(node, _evaluate_domain(node, fields, assumptions))


def _comparison_domain(node: ops.Comparison, a: Domain, b: Domain) -> Domain:
    if not _safe_comparison_inputs(node.left, a, node.right, b):
        return Domain(Fraction(0), Fraction(1), a.nullable or b.nullable, finite=False)
    if a.only_null or b.only_null:
        return Domain.null()
    if _mixed_decimal_float(node.left.dtype, node.right.dtype):
        a, b = _float64_input(a, node.left), _float64_input(b, node.right)
    if isinstance(node, (ops.Less, ops.LessEqual)):
        a, b = b, a
    if isinstance(node, (ops.Greater, ops.GreaterEqual, ops.Less, ops.LessEqual)):
        inclusive = isinstance(node, (ops.GreaterEqual, ops.LessEqual))
        definitely = (
            a.low is not None
            and b.high is not None
            and (
                a.low >= b.high
                if inclusive
                else a.low > b.high or (a.low == b.high and (a.low_open or b.high_open))
            )
        )
        impossible = (
            a.high is not None
            and b.low is not None
            and (
                a.high < b.low or (a.high == b.low and (a.high_open or b.low_open))
                if inclusive
                else a.high <= b.low
            )
        )
    else:
        disjoint = (
            a.high is not None
            and b.low is not None
            and (a.high < b.low or (a.high == b.low and (a.high_open or b.low_open)))
        ) or (
            b.high is not None
            and a.low is not None
            and (b.high < a.low or (b.high == a.low and (b.high_open or a.low_open)))
        )
        disjoint = (
            disjoint or (a.nonzero and b.low == b.high == 0) or (b.nonzero and a.low == a.high == 0)
        )
        equal = a.low is not None and a.low == a.high == b.low == b.high
        definitely, impossible = (
            (equal, disjoint) if isinstance(node, ops.Equals) else (disjoint, equal)
        )
    possibilities = _TRUE if definitely else _FALSE if impossible else _FALSE | _TRUE
    return replace(_boolean(possibilities), nullable=a.nullable or b.nullable)


def _binary_domain(
    node: ops.Add | ops.Subtract | ops.Multiply | ops.Divide, a: Domain, b: Domain
) -> Domain:
    nullable = a.nullable or b.nullable
    if not a.finite or not b.finite:
        return Domain(None, None, nullable, finite=False)
    decimal_kernel = (node.left.dtype.is_decimal() or node.right.dtype.is_decimal()) and not (
        node.left.dtype.is_floating() or node.right.dtype.is_floating()
    )
    if isinstance(node, ops.Divide) and decimal_kernel:
        # The owning numeric recipe inserts Float64 casts before division.
        return Domain(None, None, nullable, finite=False)
    if node.dtype.is_float64():
        a, b = _float64_input(a, node.left), _float64_input(b, node.right)
    elif _mixed_decimal_float(node.left.dtype, node.right.dtype) or not _safe_comparison_inputs(
        node.left, a, node.right, b
    ):
        return Domain(None, None, nullable, finite=False)
    if a.only_null or b.only_null:
        return Domain.null()
    if isinstance(node, ops.Divide) and not b.excludes_zero():
        raise DomainProofError("a demanded dynamic denominator interval includes zero")
    if None in (a.low, a.high, b.low, b.high):
        return Domain(None, None, nullable)
    assert a.low is not None and a.high is not None and b.low is not None and b.high is not None
    if isinstance(node, ops.Add):
        value = Domain(
            a.low + b.low,
            a.high + b.high,
            nullable,
            a.low_open or b.low_open,
            a.high_open or b.high_open,
        )
    elif isinstance(node, ops.Subtract):
        value = Domain(
            a.low - b.high,
            a.high - b.low,
            nullable,
            a.low_open or b.high_open,
            a.high_open or b.low_open,
        )
    elif isinstance(node, ops.Divide) and (b.low <= 0 <= b.high):
        quantum = nonzero_quantum(node.right)
        if quantum is None:
            return Domain(None, None, nullable, finite=False)
        if node.right.dtype.is_decimal():
            quantum /= 2  # Float64 promotion can round below the exact quantum.
        radius = max(abs(a.low), abs(a.high)) / quantum
        return Domain(-radius, radius, nullable)
    else:
        cells = tuple(
            x * y if isinstance(node, ops.Multiply) else x / y
            for x in (a.low, a.high)
            for y in (b.low, b.high)
        )
        value = Domain(min(cells), max(cells), nullable)
    if not decimal_kernel or isinstance(node, ops.Divide):
        return value
    assert value.low is not None and value.high is not None
    bounds = decimal_operation_bounds(node, value.low, value.high)
    return replace(value, finite=False) if bounds is None else Domain(*bounds, nullable=nullable)


def _math_domain(
    node: ops.Sqrt | ops.Ln | ops.Log | ops.Log2 | ops.Log10 | ops.Exp | ops.Round,
    a: Domain,
    visit: Callable[[ops.Value], Domain],
) -> Domain:
    base = visit(node.base) if isinstance(node, ops.Log) and node.base is not None else None
    nullable = a.nullable or (base is not None and base.nullable)
    if not a.finite or (base is not None and not base.finite):
        return Domain(None, None, nullable, finite=False)
    if base is not None and base.only_null:
        return Domain.null()
    if a.only_null:
        return a
    if isinstance(node, ops.Round):
        return _round_domain(node, a)
    if node.arg.dtype.is_integer() and not _integer_fits_float(a, 8):
        return Domain(None, None, nullable, finite=False)
    if node.arg.dtype.is_decimal():
        a = _float64_input(a, node.arg)
    if isinstance(node, ops.Sqrt):
        if a.low is None or a.low < 0:
            raise DomainProofError("a demanded sqrt argument is not proven nonnegative")
    elif isinstance(node, (ops.Ln, ops.Log, ops.Log2, ops.Log10)) and (
        a.low is None or a.low < 0 or (a.low == 0 and not a.excludes_zero())
    ):
        raise DomainProofError("a demanded log argument is not proven positive")
    if isinstance(node, ops.Exp):
        # Arrow dispatches Exp by input carrier, even when Ibis declares Float64.
        width = 4 if arrow_float32(node) else 8
        if a.high is None or a.high > (88 if width == 4 else 709):
            return Domain(None, None, a.nullable, finite=False)
        # e < 11/4 supplies a simple outward bound without evaluating or
        # changing the original numeric function.
        upper_exp = min(Fraction(11, 4) ** max(0, math.ceil(a.high)), float_limit(width))
        return Domain(Fraction(0), upper_exp, a.nullable)
    if isinstance(node, ops.Sqrt):
        if a.high is None or a.high < 0:
            return Domain(None, None, a.nullable, finite=False)
        return Domain(Fraction(0), Fraction(math.isqrt(math.ceil(a.high)) + 1), a.nullable)
    if base is not None:
        assert isinstance(node, ops.Log) and node.base is not None
        return _log_base_domain(base, node.base, nullable=nullable)
    # All positive finite Float64 values lie between 2**-1074 and 2**1024;
    # the supported natural/base-2/base-10 logarithms fit this outer bound.
    return Domain(Fraction(-2048), Fraction(2048), a.nullable)


def _log_base_domain(base: Domain, argument: ops.Value, *, nullable: bool) -> Domain:
    if argument.dtype.is_integer() and not _integer_fits_float(base, 8):
        return Domain(None, None, nullable, finite=False)
    base = _float64_input(base, argument)
    if base.low is None or base.high is None:
        return Domain(None, None, nullable, finite=False)
    lower, upper = float(base.low), float(base.high)
    if lower <= 0 or lower <= 1 <= upper:
        return Domain(None, None, nullable, finite=False)
    return Domain(Fraction(-(2**66)), Fraction(2**66), nullable)


def _round_domain(node: ops.Round, a: Domain) -> Domain:
    digits = int(node.digits.value) if isinstance(node.digits, ops.Literal) else 0
    if digits < -308:
        return Domain(None, None, a.nullable, finite=False)
    padding = Fraction(10**-digits, 2) if digits < 0 else Fraction(1)
    if node.arg.dtype.is_decimal() and not decimal_round_fits(node, a.low, a.high, digits):
        return Domain(None, None, a.nullable, finite=False)
    return Domain(
        None if a.low is None else a.low - padding,
        None if a.high is None else a.high + padding,
        a.nullable,
    )


def _conditional_domain(
    node: ops.IfElse | ops.SearchedCase | ops.Coalesce,
    fields: dict[str, Domain],
    assumptions: dict[ops.Value, Domain],
) -> Domain:
    def visit(child: ops.Value, state: dict[ops.Value, Domain] = assumptions) -> Domain:
        return evaluate_domain(child, fields, state)

    def arm(child: ops.Value, state: dict[ops.Value, Domain]) -> Domain:
        return _materialized_arm(node, child, visit(child, state))

    if isinstance(node, ops.IfElse):
        possibilities = _truth(visit(node.bool_expr))
        branches = []
        if True in possibilities:
            branches.append(
                arm(node.true_expr, _assume(node.bool_expr, _TRUE, assumptions, fields))
            )
        fallback = possibilities & _NOT_TRUE
        if fallback:
            branches.append(
                arm(node.false_null_expr, _assume(node.bool_expr, fallback, assumptions, fields))
            )
        return branches[0] if len(branches) == 1 else _union(branches[0], branches[1])
    if isinstance(node, ops.SearchedCase):
        remaining = assumptions
        result: Domain | None = None
        for condition, branch in zip(node.cases, node.results, strict=True):
            possibilities = _truth(visit(condition, remaining))
            if True in possibilities:
                part = arm(branch, _assume(condition, _TRUE, remaining, fields))
                result = part if result is None else _union(result, part)
            fallback = possibilities & _NOT_TRUE
            if not fallback:
                return result if result is not None else Domain.null()
            remaining = _assume(condition, fallback, remaining, fields)
        final = arm(node.default, remaining)
        return final if result is None else _union(result, final)
    coalesced: Domain | None = None
    remaining = assumptions
    for child in node.arg:
        part = arm(child, remaining)
        if not part.finite:
            return part
        if not part.only_null:
            nonnull = replace(part, nullable=False)
            coalesced = nonnull if coalesced is None else _union(coalesced, nonnull)
        if not part.nullable:
            assert coalesced is not None
            return coalesced
        remaining = _assume(ops.IsNull(child), _TRUE, remaining, fields)
    return Domain.null() if coalesced is None else replace(coalesced, nullable=True)


def _materialized_arm(node: ops.Value, child: ops.Value, value: Domain) -> Domain:
    if value.only_null or not value.finite or not node.dtype.is_numeric():
        return value
    target, source = node.dtype, child.dtype
    if isinstance(node, ops.IfElse) and _mixed_decimal_float(
        node.true_expr.dtype, node.false_null_expr.dtype
    ):
        # Arrow if_else promotes Decimal/float to Float64, even when the
        # containing numeric DAG expects its declared Decimal carrier.
        return replace(value, finite=False)
    if target.is_floating() and source.is_integer():
        return replace(value, finite=_integer_fits_float(value, 4 if arrow_float32(node) else 8))
    if _mixed_decimal_float(source, target):
        return replace(value, finite=False)
    if source != target and (target.is_integer() or target.is_decimal()):
        return _cast_domain(ops.Cast(child, to=target), value)
    return value


def _cast_domain(node: ops.Cast | ops.TryCast, arg: Domain) -> Domain:
    if arg.only_null or not arg.finite or node.to == node.arg.dtype:
        return arg
    source = node.arg.dtype
    if not source.is_numeric() or not node.to.is_numeric():
        return replace(arg, finite=False)
    if (
        source.is_floating()
        and node.to.is_decimal()
        and (isinstance(node, ops.TryCast) or isinstance(node.arg, ops.Literal))
    ):
        # Decimal array construction rejects floats. A scalar TryCast can round.
        return replace(arg, finite=False)
    # Arrow's safe cast does not truncate fractional points to integer or a
    # smaller complete Decimal lattice. A failed TryCast can additionally be NULL.
    if node.to.is_integer() or node.to.is_decimal():
        target_scale = node.to.scale or 0 if node.to.is_decimal() else 0
        lattice_fits = source.is_integer() or (
            source.is_decimal() and (source.scale or 0) <= target_scale
        )
        point_fits = (
            arg.low is not None
            and arg.low == arg.high
            and (arg.low * 10**target_scale).denominator == 1
        )
        fits = lattice_fits or point_fits
    elif source.is_integer() and (isinstance(node, ops.TryCast) or not node.to.is_float64()):
        fits = _integer_fits_float(arg, node.to.nbytes)
    else:
        fits = True
    if fits:
        return arg
    return (
        replace(arg, nullable=True) if isinstance(node, ops.TryCast) else replace(arg, finite=False)
    )


def _null_test_domain(
    node: ops.IsNull | ops.NotNull | ops.IsNan | ops.IsInf, arg: Domain
) -> Domain:
    if not arg.finite:
        return Domain(Fraction(0), Fraction(1), finite=False)
    if isinstance(node, (ops.IsNan, ops.IsInf)):
        return Domain.constant(False)
    possibilities = _TRUE if arg.only_null else _FALSE | _TRUE if arg.nullable else _FALSE
    if isinstance(node, ops.NotNull):
        possibilities = _negated(possibilities)
    return _boolean(possibilities)


def _signed_domain(node: ops.Negate | ops.Abs, a: Domain) -> Domain:
    if (
        isinstance(node, ops.Negate)
        and node.arg.dtype.is_integer()
        and not arrow_integer(node.arg)[1]
    ):
        return Domain(None, None, a.nullable, finite=False)
    if a.only_null:
        return a
    if not a.finite:
        return Domain(None, None, a.nullable, finite=False)
    if isinstance(node, ops.Negate):
        return Domain(
            None if a.high is None else -a.high,
            None if a.low is None else -a.low,
            a.nullable,
            a.high_open,
            a.low_open,
        )
    if a.low is None or a.high is None:
        return Domain(Fraction(0), None, a.nullable)
    return Domain(
        Fraction(0) if a.low <= 0 <= a.high else min(abs(a.low), abs(a.high)),
        max(abs(a.low), abs(a.high)),
        a.nullable,
    )


def _clamp(a: Domain, b: Domain, *, lower: bool) -> Domain:
    lows = [endpoint for endpoint in (a.low, b.low) if endpoint is not None]
    highs = [endpoint for endpoint in (a.high, b.high) if endpoint is not None]
    if lower:
        clipped = Domain(
            max(lows, default=None), max(highs) if len(highs) == 2 else None, a.nullable
        )
    else:
        clipped = Domain(
            min(lows) if len(lows) == 2 else None, min(highs, default=None), a.nullable
        )
    return _union(a, clipped) if b.nullable else clipped


def _clip_domain(node: ops.Clip, visit: Callable[[ops.Value], Domain]) -> Domain:
    result = visit(node.arg)
    for endpoint, lower in ((node.lower, True), (node.upper, False)):
        if endpoint is None:
            continue
        bound = visit(endpoint)
        if not _safe_comparison_inputs(node.arg, result, endpoint, bound):
            return Domain(None, None, result.nullable, finite=False)
        if result.only_null or bound.only_null:
            continue
        mixed = _mixed_decimal_float(node.arg.dtype, endpoint.dtype)
        if not mixed and _cannot_clip(result, bound, lower=lower):
            continue
        if endpoint.dtype != node.arg.dtype:
            cast = ops.Cast(endpoint, to=node.arg.dtype)
            bound = _carrier_domain(cast, _cast_domain(cast, bound))
            if not bound.finite:
                return Domain(None, None, result.nullable, finite=False)
        if mixed:
            result = replace(_union(result, bound), nullable=result.nullable)
        else:
            result = _clamp(result, bound, lower=lower)
    return result


def _cannot_clip(value: Domain, bound: Domain, *, lower: bool) -> bool:
    if lower:
        return bound.high is not None and value.low is not None and bound.high <= value.low
    return bound.low is not None and value.high is not None and bound.low >= value.high


def _logical_domain(
    node: ops.And | ops.Or, fields: dict[str, Domain], assumptions: dict[ops.Value, Domain]
) -> Domain:
    def visit(child: ops.Value, state: dict[ops.Value, Domain] = assumptions) -> Domain:
        return evaluate_domain(child, fields, state)

    combined_truths: set[_Truth] = set()
    short_circuit = isinstance(node, ops.Or)
    for left in _truth(visit(node.left)):
        if left is short_circuit:
            combined_truths.add(left)
            continue
        state = _assume(node.left, frozenset((left,)), assumptions, fields)
        for right in _truth(visit(node.right, state)):
            combined_truths.add(right if left is not None or right is short_circuit else None)
    return _boolean(frozenset(combined_truths))


def _remaining_domain(node: ops.Value, visit: Callable[[ops.Value], Domain]) -> Domain:
    if isinstance(node, (ops.Sqrt, ops.Ln, ops.Log, ops.Log2, ops.Log10, ops.Exp, ops.Round)):
        return _math_domain(node, visit(node.arg), visit)
    if isinstance(node, ops.NullIf):
        a, b = visit(node.arg), visit(node.null_if_expr)
        return replace(
            a, nullable=True, finite=_safe_comparison_inputs(node.arg, a, node.null_if_expr, b)
        )
    if isinstance(node, (ops.Least, ops.Greatest)):
        result = visit(node.arg[0])
        for child in node.arg[1:]:
            result = _union(result, visit(child))
        return result
    if isinstance(node, ops.Clip):
        return _clip_domain(node, visit)
    raise DomainProofError(f"no fixed-state interval proof for {type(node).__name__}")


def _evaluate_domain(
    node: ops.Value, fields: dict[str, Domain], assumptions: dict[ops.Value, Domain] | None = None
) -> Domain:
    assumptions = {} if assumptions is None else assumptions

    def visit(child: ops.Value, state: dict[ops.Value, Domain] = assumptions) -> Domain:
        return evaluate_domain(child, fields, state)

    if isinstance(node, ops.Field):
        return fields[node.name]
    if isinstance(node, ops.Literal):
        return Domain.constant(node.value)
    if isinstance(node, ops.Alias):
        return visit(node.arg)
    if isinstance(node, (ops.Cast, ops.TryCast)):
        return _cast_domain(node, visit(node.arg))
    if isinstance(node, (ops.IfElse, ops.SearchedCase, ops.Coalesce)):
        return _conditional_domain(node, fields, assumptions)
    if isinstance(node, (ops.IsNull, ops.NotNull, ops.IsNan, ops.IsInf)):
        return _null_test_domain(node, visit(node.arg))
    if isinstance(node, ops.Not):
        return _boolean(_negated(_truth(visit(node.arg))))
    if isinstance(node, (ops.And, ops.Or)):
        return _logical_domain(node, fields, assumptions)
    if isinstance(
        node, (ops.Equals, ops.NotEquals, ops.Greater, ops.GreaterEqual, ops.Less, ops.LessEqual)
    ):
        return _comparison_domain(node, visit(node.left), visit(node.right))
    if isinstance(node, (ops.Add, ops.Subtract, ops.Multiply, ops.Divide)):
        return _binary_domain(node, visit(node.left), visit(node.right))
    if isinstance(node, (ops.Negate, ops.Abs)):
        return _signed_domain(node, visit(node.arg))
    return _remaining_domain(node, visit)


def prove_domain(
    value: ops.Value, checks: tuple[ops.Value, ...], fields: dict[str, Domain]
) -> tuple[str, ...]:
    for check in checks:
        if True in _truth(evaluate_domain(check, fields)):
            raise DomainProofError(
                "a demanded numeric/NULL check cannot be proven false for every mixed state"
            )
    result = evaluate_domain(value, fields)
    if not result.finite:
        raise DomainProofError("the demanded whole Metric is not proven finite")
    if result.nullable:
        raise DomainProofError("the whole Metric can be undefined in an unobserved mixed state")
    return (
        "all captured leaf intervals cover every endpoint replacement",
        "all demanded numeric and NULL checks are false over the fixed domain",
        "every demanded numeric intermediate fits its original carrier",
        "the whole Metric is defined over every endpoint replacement",
    )
