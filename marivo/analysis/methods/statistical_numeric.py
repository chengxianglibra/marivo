"""Certified statistical roots and normal quantiles over exact captured facts."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal, localcontext
from fractions import Fraction
from functools import lru_cache

from marivo.analysis.methods.deviation_numeric import RationalFact, RootCertificate, finish
from marivo.analysis.methods.physical import ValueType


def unbounded() -> None:
    """Pure arithmetic has no Runtime-owned deadline."""


def root(value: Fraction, precision: int) -> RootCertificate:
    """Enclose the exact nonnegative radicand with independently checked endpoints."""
    if value < 0 or precision < 120:
        raise ValueError("nonnegative radicand and at least 120 digits required")
    if value == 0:
        return RootCertificate(RationalFact.capture(value), "0", "0", precision)
    ctx = Context(prec=precision, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)
    lower, upper = ctx.copy(), ctx.copy()
    lower.rounding, upper.rounding = ROUND_FLOOR, ROUND_CEILING
    lo = lower.divide(Decimal(value.numerator), Decimal(value.denominator)).sqrt(context=ctx)
    hi = upper.divide(Decimal(value.numerator), Decimal(value.denominator)).sqrt(context=ctx)
    lo, hi = max(Decimal(0), lo.next_minus(context=ctx)), hi.next_plus(context=ctx)
    if Fraction(lo) ** 2 > value or Fraction(hi) ** 2 < value:
        raise ArithmeticError("root enclosure failed")
    return RootCertificate(RationalFact.capture(value), str(lo), str(hi), precision)


def verify_root(certificate: RootCertificate, value: Fraction) -> bool:
    lo, hi = Decimal(certificate.lower), Decimal(certificate.upper)
    return (
        certificate.radicand.value() == value
        and certificate.precision >= 120
        and lo.is_finite()
        and hi.is_finite()
        and 0 <= lo <= hi
        and Fraction(lo) ** 2 <= value <= Fraction(hi) ** 2
    )


def coefficient(
    numerator: Fraction, radicand: Fraction, *, checkpoint: Callable[[], None] = unbounded
) -> tuple[float, RootCertificate | None]:
    """Finish a bounded correlation without casting either original vector."""
    if radicand <= 0 or numerator**2 > radicand:
        raise ValueError("invalid correlation centered facts")
    if numerator == 0:
        return 0.0, None
    a, b = math.isqrt(radicand.numerator), math.isqrt(radicand.denominator)
    if a * a == radicand.numerator and b * b == radicand.denominator:
        return float(numerator / Fraction(a, b)), None
    precision = 120
    while True:
        checkpoint()
        certificate = root(radicand, precision)
        bounds = sorted(
            (
                numerator / Fraction(Decimal(certificate.lower)),
                numerator / Fraction(Decimal(certificate.upper)),
            )
        )
        if float(bounds[0]) == float(bounds[1]):
            return float(bounds[0]), certificate
        precision += 40


@lru_cache(maxsize=8)
def _pi(precision: int) -> tuple[Fraction, Fraction]:
    """Machin's identity with exact alternating-series remainder bounds."""

    def atan(q: int) -> tuple[Fraction, Fraction]:
        total = Fraction()
        n = 0
        while True:
            term = Fraction((-1) ** n, (2 * n + 1) * q ** (2 * n + 1))
            total += term
            tail = Fraction((-1) ** (n + 1), (2 * n + 3) * q ** (2 * n + 3))
            if abs(tail) < Fraction(1, 10 ** (precision + 10)):
                return min(total, total + tail), max(total, total + tail)
            n += 1

    a, b = atan(5), atan(239)
    return 16 * a[0] - 4 * b[1], 16 * a[1] - 4 * b[0]


def normal_cdf_bounds(z: Decimal, precision: int) -> tuple[Fraction, Fraction]:
    """Directed integrated exponential series on the admitted positive quantile range."""
    if not z.is_finite() or not 0 <= z <= 10:
        raise ValueError("normal quantile certificate requires z in [0,10]")
    ctx = Context(prec=precision, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)
    low, high = ctx.copy(), ctx.copy()
    low.rounding, high.rounding = ROUND_FLOOR, ROUND_CEILING
    with localcontext(ctx):
        square_lo, square_hi = low.multiply(z, z), high.multiply(z, z)
        term_lo = term_hi = z
        sum_lo = sum_hi = z
        n = 0
        while True:
            factor_lo = low.divide(
                low.multiply(square_lo, Decimal(2 * n + 1)), Decimal(2 * (n + 1) * (2 * n + 3))
            )
            factor_hi = high.divide(
                high.multiply(square_hi, Decimal(2 * n + 1)), Decimal(2 * (n + 1) * (2 * n + 3))
            )
            next_lo, next_hi = low.multiply(term_lo, factor_lo), high.multiply(term_hi, factor_hi)
            if factor_hi < 1 and next_hi < Decimal(1).scaleb(-precision + 10):
                if (n + 1) % 2:
                    sum_lo = low.subtract(sum_lo, next_hi)
                else:
                    sum_hi = high.add(sum_hi, next_hi)
                break
            n += 1
            if n % 2:
                sum_lo, sum_hi = low.subtract(sum_lo, next_hi), high.subtract(sum_hi, next_lo)
            else:
                sum_lo, sum_hi = low.add(sum_lo, next_lo), high.add(sum_hi, next_hi)
            term_lo, term_hi = next_lo, next_hi
        pi_lo, pi_hi = _pi(precision)
        denominator_lo = root(2 * pi_lo, precision)
        denominator_hi = root(2 * pi_hi, precision)
        return (
            Fraction(1, 2) + Fraction(sum_lo) / Fraction(Decimal(denominator_hi.upper)),
            Fraction(1, 2) + Fraction(sum_hi) / Fraction(Decimal(denominator_lo.lower)),
        )


@dataclass(frozen=True, slots=True)
class QuantileCertificate:
    probability: RationalFact
    lower: str
    upper: str
    precision: int


def quantile(
    level: float, *, checkpoint: Callable[[], None] = unbounded, extra_precision: int = 0
) -> QuantileCertificate:
    """Invert the exact binary64 level without rounding its probability to an endpoint."""
    if type(level) is not float or not math.isfinite(level) or not 0 < level < 1:
        raise ValueError("finite float interval level in (0,1) required")
    small = min(Fraction(level), 1 - Fraction(level))
    exponent = len(str(small.denominator)) - len(str(abs(small.numerator)))
    while small * 10**exponent < 1:
        exponent += 1
    while exponent > 0 and small * 10 ** (exponent - 1) >= 1:
        exponent -= 1
    precision = 120 + max(0, exponent) + extra_precision
    probability = (1 + Fraction(level)) / 2
    while True:
        ctx = Context(prec=precision, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)
        lo, hi = Decimal(0), Decimal(10)
        with localcontext(ctx):
            while hi - lo > Decimal(1).scaleb(-precision + 20):
                checkpoint()
                mid = (lo + hi) / 2
                lower, upper = normal_cdf_bounds(mid, precision)
                if upper < probability:
                    lo = mid
                elif lower > probability:
                    hi = mid
                else:
                    return QuantileCertificate(
                        RationalFact.capture(probability), str(lo), str(hi), precision
                    )
            else:
                return QuantileCertificate(
                    RationalFact.capture(probability), str(lo), str(hi), precision
                )
        precision += 40


def verify_quantile(value: QuantileCertificate, level: float) -> bool:
    if value.probability.value() != (1 + Fraction(level)) / 2 or value.precision < 120:
        return False
    lo, hi = Decimal(value.lower), Decimal(value.upper)
    p = value.probability.value()
    return (
        0 <= lo <= hi <= 10
        and normal_cdf_bounds(lo, value.precision)[1]
        <= p
        <= normal_cdf_bounds(hi, value.precision)[0]
    )


def interval(
    point: Fraction, variance: Fraction, q: QuantileCertificate, typ: ValueType
) -> tuple[int | float | Decimal, int | float | Decimal, RootCertificate]:
    """Certify both published endpoints using the captured normal/root enclosures."""
    certificate = root(variance, q.precision)
    lo = Fraction(Decimal(q.lower)) * Fraction(Decimal(certificate.lower))
    hi = Fraction(Decimal(q.upper)) * Fraction(Decimal(certificate.upper))
    lower_a, lower_b = finish(point - hi, typ), finish(point - lo, typ)
    upper_a, upper_b = finish(point + lo, typ), finish(point + hi, typ)
    if lower_a != lower_b or upper_a != upper_b:
        raise ArithmeticError("forecast endpoint rounding requires higher precision")
    return lower_a, upper_a, certificate
