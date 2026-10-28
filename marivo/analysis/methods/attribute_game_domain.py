"""Finite-state interval certificates with the owning numeric DAG's branch demand."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, replace
from fractions import Fraction

import ibis.expr.operations as ops


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
            from decimal import Decimal

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
    if a.only_null:
        return replace(b, nullable=True)
    if b.only_null:
        return replace(a, nullable=True)
    low = None if a.low is None or b.low is None else min(a.low, b.low)
    high = None if a.high is None or b.high is None else max(a.high, b.high)
    return Domain(low, high, a.nullable or b.nullable, finite=a.finite and b.finite)


def _truth(value: Domain) -> frozenset[bool]:
    if not value.finite:
        raise DomainProofError(
            "a demanded condition contains an unqualified nonfinite Metric result"
        )
    if value.only_null:
        return frozenset((False,))
    if value.low == value.high == 0:
        return frozenset((False,))
    if value.low == value.high == 1 and not value.nullable:
        return frozenset((True,))
    return frozenset((False, True))


def _boolean(values: frozenset[bool]) -> Domain:
    return Domain(Fraction(int(min(values))), Fraction(int(max(values))))


def _assume(
    condition: ops.Value,
    truth: bool,
    assumptions: dict[ops.Value, Domain],
    fields: dict[str, Domain],
) -> dict[ops.Value, Domain]:
    result = dict(assumptions)
    result[condition] = Domain.constant(truth)
    if isinstance(condition, ops.Coalesce) and condition.arg:
        first = evaluate_domain(condition.arg[0], fields, result)
        if not first.nullable or (
            truth
            and all(
                _truth(evaluate_domain(child, fields, result)) == frozenset((False,))
                for child in condition.arg[1:]
            )
        ):
            return _assume(condition.arg[0], truth, result, fields)
    if isinstance(condition, ops.Not):
        return _assume(condition.arg, not truth, result, fields)
    if (isinstance(condition, ops.And) and truth) or (isinstance(condition, ops.Or) and not truth):
        return _assume(
            condition.right, truth, _assume(condition.left, truth, result, fields), fields
        )
    if isinstance(condition, (ops.IsNull, ops.NotNull)):
        nonnull = truth == isinstance(condition, ops.NotNull)
        before = evaluate_domain(condition.arg, fields, result)
        result[condition.arg] = (
            replace(before, nullable=False, only_null=False) if nonnull else Domain.null()
        )
    if isinstance(
        condition,
        (ops.Greater, ops.GreaterEqual, ops.Less, ops.LessEqual, ops.Equals, ops.NotEquals),
    ):
        left, right = condition.left, condition.right
        right_value = evaluate_domain(right, fields, result)
        if (
            right_value.low is not None
            and right_value.low == right_value.high
            and not right_value.nullable
        ):
            before = evaluate_domain(left, fields, result)
            point = right_value.low
            lower = isinstance(condition, (ops.Greater, ops.GreaterEqual))
            upper = isinstance(condition, (ops.Less, ops.LessEqual))
            inclusive = isinstance(condition, (ops.GreaterEqual, ops.LessEqual))
            if not truth:
                lower, upper, inclusive = upper, lower, not inclusive
            if lower:
                bound = point if before.low is None else max(point, before.low)
                result[left] = replace(
                    before,
                    low=bound,
                    low_open=(not inclusive if bound == point else before.low_open),
                    nullable=False,
                )
            elif upper:
                bound = point if before.high is None else min(point, before.high)
                result[left] = replace(
                    before,
                    high=bound,
                    high_open=(not inclusive if bound == point else before.high_open),
                    nullable=False,
                )
            elif truth == isinstance(condition, ops.Equals):
                result[left] = Domain(point, point)
            elif point == 0 and truth == isinstance(condition, ops.NotEquals):
                result[left] = replace(before, nullable=False, nonzero=True)
    return result


def _carrier_domain(node: ops.Value, value: Domain) -> Domain:
    """Require every demanded numeric intermediate to fit its original carrier."""
    if value.only_null or not value.finite or not node.dtype.is_numeric():
        return value
    if value.low is None or value.high is None:
        return replace(value, finite=False)
    dtype = node.dtype
    if dtype.is_integer():
        bits = dtype.nbytes * 8
        lower = -(2 ** (bits - 1)) if dtype.is_signed_integer() else 0
        upper = 2 ** (bits - int(dtype.is_signed_integer())) - 1
        return replace(value, finite=value.low >= lower and value.high <= upper)
    if dtype.is_decimal():
        quantum = Fraction(1, 10 ** (dtype.scale or 0))
        upper = (10 ** (dtype.precision or 38) - 1) * quantum
        return replace(value, finite=value.low >= -upper and value.high <= upper)
    if dtype.is_floating():
        limit = Fraction(sys.float_info.max if dtype.nbytes == 8 else 2**128 - 2**104)
        if value.low < -limit or value.high > limit:
            return replace(value, finite=False)
        if isinstance(node, (ops.Literal, ops.Alias, ops.Field, ops.Negate, ops.Abs)):
            return value
        if isinstance(node, ops.Cast) and dtype.nbytes == 8:
            return replace(value, low=Fraction(float(value.low)), high=Fraction(float(value.high)))
        # An outward interval encloses native rounding; it does not change any
        # evaluated value or introduce a wider execution carrier.
        low, high = float(value.low), float(value.high)
        if dtype.nbytes != 8:
            # Float32 rounding has a larger quantum. A relative one-ULP bound
            # plus its smallest subnormal is conservative at zero as well.
            padding = max(abs(value.low), abs(value.high)) / 2**23 + Fraction(1, 2**149)
            return replace(
                value, low=max(-limit, value.low - padding), high=min(limit, value.high + padding)
            )
        lower = math.nextafter(low, -math.inf)
        upper = math.nextafter(high, math.inf)
        return replace(
            value,
            low=max(-limit, Fraction(lower)) if math.isfinite(lower) else -limit,
            high=min(limit, Fraction(upper)) if math.isfinite(upper) else limit,
        )
    return value


def _nonzero_quantum(node: ops.Value) -> Fraction | None:
    if node.dtype.is_integer():
        return Fraction(1)
    if node.dtype.is_decimal():
        return Fraction(1, 10 ** (node.dtype.scale or 0))
    if isinstance(node, ops.Cast) and node.to.is_floating():
        quantum = _nonzero_quantum(node.arg)
        return None if quantum is None else quantum / 2
    return None


def evaluate_domain(
    node: ops.Value, fields: dict[str, Domain], assumptions: dict[ops.Value, Domain] | None = None
) -> Domain:
    return _carrier_domain(node, _evaluate_domain(node, fields, assumptions))


def _evaluate_domain(
    node: ops.Value, fields: dict[str, Domain], assumptions: dict[ops.Value, Domain] | None = None
) -> Domain:
    assumptions = {} if assumptions is None else assumptions
    if node in assumptions:
        return assumptions[node]

    def visit(child: ops.Value, state: dict[ops.Value, Domain] = assumptions) -> Domain:
        return evaluate_domain(child, fields, state)

    if isinstance(node, ops.Field):
        return fields[node.name]
    if isinstance(node, ops.Literal):
        return Domain.constant(node.value)
    if isinstance(node, ops.Alias):
        return visit(node.arg)
    if isinstance(node, (ops.Cast, ops.TryCast)):
        arg = visit(node.arg)
        if arg.only_null:
            return arg
        if node.to.is_integer() or node.to.is_decimal():
            scale = node.to.scale if node.to.is_decimal() else 0
            quantum = Fraction(1, 10 ** (scale or 0))
            if arg.low is not None and arg.high is not None and arg.low != arg.high:
                return replace(
                    arg,
                    low=arg.low - quantum,
                    high=arg.high + quantum,
                    low_open=False,
                    high_open=False,
                )
        return arg
    if isinstance(node, ops.IfElse):
        possibilities = _truth(visit(node.bool_expr))
        branches = []
        if True in possibilities:
            branches.append(
                visit(node.true_expr, _assume(node.bool_expr, True, assumptions, fields))
            )
        if False in possibilities:
            branches.append(
                visit(node.false_null_expr, _assume(node.bool_expr, False, assumptions, fields))
            )
        return branches[0] if len(branches) == 1 else _union(branches[0], branches[1])
    if isinstance(node, ops.SearchedCase):
        remaining = assumptions
        result: Domain | None = None
        for condition, branch in zip(node.cases, node.results, strict=True):
            possibilities = _truth(visit(condition, remaining))
            if True in possibilities:
                part = visit(branch, _assume(condition, True, remaining, fields))
                result = part if result is None else _union(result, part)
            if False not in possibilities:
                return result if result is not None else Domain.null()
            remaining = _assume(condition, False, remaining, fields)
        final = visit(node.default, remaining)
        return final if result is None else _union(result, final)
    if isinstance(node, ops.Coalesce):
        coalesced: Domain | None = None
        remaining = assumptions
        for child in node.arg:
            part = visit(child, remaining)
            if not part.only_null:
                nonnull = replace(part, nullable=False)
                coalesced = nonnull if coalesced is None else _union(coalesced, nonnull)
            if not part.nullable:
                assert coalesced is not None
                return coalesced
            remaining = _assume(ops.IsNull(child), True, remaining, fields)
        return Domain.null() if coalesced is None else replace(coalesced, nullable=True)
    if isinstance(node, (ops.IsNull, ops.NotNull, ops.IsNan, ops.IsInf)):
        arg = visit(node.arg)
        if not arg.finite:
            return Domain(Fraction(0), Fraction(1), finite=False)
        if isinstance(node, (ops.IsNan, ops.IsInf)):
            return Domain.constant(False)
        possibilities = (
            frozenset((True,))
            if arg.only_null
            else frozenset((False, True))
            if arg.nullable
            else frozenset((False,))
        )
        if isinstance(node, ops.NotNull):
            possibilities = frozenset(not item for item in possibilities)
        return _boolean(possibilities)
    if isinstance(node, ops.Not):
        return _boolean(frozenset(not item for item in _truth(visit(node.arg))))
    if isinstance(node, (ops.And, ops.Or)):
        combined_truths: set[bool] = set()
        for left in _truth(visit(node.left)):
            if (isinstance(node, ops.And) and not left) or (isinstance(node, ops.Or) and left):
                combined_truths.add(left)
            else:
                combined_truths.update(
                    _truth(visit(node.right, _assume(node.left, left, assumptions, fields)))
                )
        return _boolean(frozenset(combined_truths))
    if isinstance(
        node, (ops.Equals, ops.NotEquals, ops.Greater, ops.GreaterEqual, ops.Less, ops.LessEqual)
    ):
        a, b = visit(node.left), visit(node.right)
        if not a.finite or not b.finite:
            return Domain(Fraction(0), Fraction(1), a.nullable or b.nullable, finite=False)
        if a.only_null or b.only_null:
            return Domain.null()
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
                disjoint
                or (a.nonzero and b.low == b.high == 0)
                or (b.nonzero and a.low == a.high == 0)
            )
            equal = a.low is not None and a.low == a.high == b.low == b.high
            definitely, impossible = (
                (equal, disjoint) if isinstance(node, ops.Equals) else (disjoint, equal)
            )
        possibilities = (
            frozenset((True,))
            if definitely
            else frozenset((False,))
            if impossible
            else frozenset((False, True))
        )
        return replace(_boolean(possibilities), nullable=a.nullable or b.nullable)
    if isinstance(node, (ops.Add, ops.Subtract, ops.Multiply, ops.Divide)):
        a, b = visit(node.left), visit(node.right)
        if a.only_null or b.only_null:
            return Domain.null()
        nullable = a.nullable or b.nullable
        if not a.finite or not b.finite:
            return Domain(None, None, nullable, finite=False)
        if isinstance(node, ops.Divide) and not b.excludes_zero():
            raise DomainProofError("a demanded dynamic denominator interval includes zero")
        if None in (a.low, a.high, b.low, b.high):
            return Domain(None, None, nullable)
        assert a.low is not None and a.high is not None and b.low is not None and b.high is not None
        if isinstance(node, ops.Add):
            return Domain(
                a.low + b.low,
                a.high + b.high,
                nullable,
                a.low_open or b.low_open,
                a.high_open or b.high_open,
            )
        if isinstance(node, ops.Subtract):
            return Domain(
                a.low - b.high,
                a.high - b.low,
                nullable,
                a.low_open or b.high_open,
                a.high_open or b.low_open,
            )
        if isinstance(node, ops.Divide) and (b.low <= 0 <= b.high):
            nonzero_quantum = _nonzero_quantum(node.right)
            if nonzero_quantum is None:
                return Domain(None, None, nullable, finite=False)
            radius = max(abs(a.low), abs(a.high)) / nonzero_quantum
            return Domain(-radius, radius, nullable)
        cells = tuple(
            x * y if isinstance(node, ops.Multiply) else x / y
            for x in (a.low, a.high)
            for y in (b.low, b.high)
        )
        return Domain(min(cells), max(cells), nullable)
    if isinstance(node, (ops.Negate, ops.Abs)):
        a = visit(node.arg)
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
    if isinstance(node, (ops.Sqrt, ops.Ln, ops.Log, ops.Log2, ops.Log10, ops.Exp, ops.Round)):
        a = visit(node.arg)
        if a.only_null:
            return a
        if not a.finite:
            return Domain(None, None, a.nullable, finite=False)
        if isinstance(node, ops.Sqrt):
            if a.low is None or a.low < 0:
                raise DomainProofError("a demanded sqrt argument is not proven nonnegative")
        elif isinstance(node, (ops.Ln, ops.Log, ops.Log2, ops.Log10)) and (
            a.low is None or a.low < 0 or (a.low == 0 and not a.low_open)
        ):
            raise DomainProofError("a demanded log argument is not proven positive")
        if isinstance(node, ops.Round):
            return Domain(
                None if a.low is None else a.low - 1,
                None if a.high is None else a.high + 1,
                a.nullable,
            )
        if isinstance(node, ops.Exp):
            # exp(709) fits Float64. Larger or unbounded inputs need another
            # proof; checking only sampled coalitions would miss overflow.
            if a.high is None or a.high > 709:
                return Domain(None, None, a.nullable, finite=False)
            # e < 11/4 supplies a simple outward bound without evaluating or
            # changing the original numeric function.
            upper_exp = min(
                Fraction(11, 4) ** max(0, math.ceil(a.high)), Fraction(sys.float_info.max)
            )
            return Domain(Fraction(0), upper_exp, a.nullable)
        if isinstance(node, ops.Sqrt):
            if a.high is None or a.high < 0:
                return Domain(None, None, a.nullable, finite=False)
            return Domain(Fraction(0), Fraction(math.isqrt(math.ceil(a.high)) + 1), a.nullable)
        if isinstance(node, ops.Log) and node.base is not None:
            base = visit(node.base)
            if base.low is None or base.high is None:
                return Domain(None, None, a.nullable, finite=False)
            lower, upper = float(base.low), float(base.high)
            if lower <= 0 or lower <= 1 <= upper:
                return Domain(None, None, a.nullable, finite=False)
            return Domain(Fraction(-(2**66)), Fraction(2**66), a.nullable)
        # All positive finite Float64 values lie between 2**-1074 and 2**1024;
        # the supported natural/base-2/base-10 logarithms fit this outer bound.
        return Domain(Fraction(-2048), Fraction(2048), a.nullable)
    if isinstance(node, ops.NullIf):
        return replace(visit(node.arg), nullable=True)
    if isinstance(node, (ops.Least, ops.Greatest)):
        result = visit(node.arg[0])
        for child in node.arg[1:]:
            result = _union(result, visit(child))
        return result
    if isinstance(node, ops.Clip):
        result = visit(node.arg)
        if node.lower is not None:
            bound = visit(node.lower)
            if bound.low is not None:
                result = replace(
                    result, low=bound.low if result.low is None else max(result.low, bound.low)
                )
        if node.upper is not None:
            bound = visit(node.upper)
            if bound.high is not None:
                result = replace(
                    result, high=bound.high if result.high is None else min(result.high, bound.high)
                )
        return result
    raise DomainProofError(f"no fixed-state interval proof for {type(node).__name__}")


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
