"""Widened deviation arithmetic with a single certified public finish."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal, localcontext
from fractions import Fraction
from typing import Literal

from marivo.analysis.methods.physical import DecimalType, ScalarType, ValueType

DeviationMethod = Literal["zscore", "mad"]
ScaleBranch = Literal["population_stddev", "scaled_mad", "mean_absolute_deviation"]


def _unbounded() -> None:
    """Pure numerical callers have no Runtime deadline."""


@dataclass(frozen=True, slots=True)
class RationalFact:
    numerator: str
    denominator: str

    @classmethod
    def capture(cls, value: Fraction) -> RationalFact:
        return cls(str(value.numerator), str(value.denominator))

    def value(self) -> Fraction:
        numerator, denominator = int(self.numerator), int(self.denominator)
        value = Fraction(numerator, denominator)
        if denominator <= 0 or self.capture(value) != self:
            raise ValueError("noncanonical rational fact")
        return value


@dataclass(frozen=True, slots=True)
class Fit:
    method: DeviationMethod
    n: int
    center: RationalFact | None
    raw_scale: RationalFact | None
    branch: ScaleBranch
    lower_middle: RationalFact | None
    upper_middle: RationalFact | None
    numeric_policy: Literal["r8_numeric_v1"] = "r8_numeric_v1"
    version: Literal["v1"] = "v1"


@dataclass(frozen=True, slots=True)
class RootCertificate:
    radicand: RationalFact
    lower: str
    upper: str
    precision: int


@dataclass(frozen=True, slots=True)
class Scored:
    value: float
    root: RootCertificate | None


def exact(value: int | float | Decimal, typ: ValueType) -> Fraction:
    """Validate the original exact carrier before any widened arithmetic."""
    if isinstance(typ, DecimalType):
        if type(value) is not Decimal or not value.is_finite():
            raise ValueError("expected finite original Decimal")
        result = Fraction(value)
        scaled = result * 10**typ.scale
        if scaled.denominator != 1 or abs(scaled.numerator) >= 10**typ.precision:
            raise ValueError("Decimal differs from its captured precision/scale")
        return result
    if typ == ScalarType("int64"):
        if type(value) is not int or not -(2**63) <= value < 2**63:
            raise ValueError("expected original int64")
        return Fraction(value)
    if typ == ScalarType("float64"):
        if type(value) is not float or not math.isfinite(value):
            raise ValueError("expected finite original binary64")
        return Fraction(value)
    raise ValueError("expected int64, float64 or exact Decimal")


def _median(values: tuple[Fraction, ...]) -> tuple[Fraction, Fraction, Fraction]:
    ordered = sorted(values)
    lower, upper = ordered[(len(ordered) - 1) // 2], ordered[len(ordered) // 2]
    return (lower + upper) / 2, lower, upper


def fit(values: tuple[Fraction, ...], method: DeviationMethod) -> Fit:
    if method not in ("zscore", "mad") or len(values) >= 2**63:
        raise ValueError("invalid method or overflowing sample count")
    if not values:
        return Fit(
            method,
            0,
            None,
            None,
            "population_stddev" if method == "zscore" else "scaled_mad",
            None,
            None,
        )
    n = len(values)
    if method == "zscore":
        center = sum(values, Fraction()) / n
        scale = sum(((value - center) ** 2 for value in values), Fraction()) / n
        branch: ScaleBranch = "population_stddev"
        lower = upper = None
    else:
        center, low, high = _median(values)
        lower, upper = RationalFact.capture(low), RationalFact.capture(high)
        raw, _, _ = _median(tuple(abs(value - center) for value in values))
        scale = raw * Fraction(7413, 5000)
        branch = "scaled_mad"
        if raw == 0:
            scale = sum((abs(value - center) for value in values), Fraction()) / n
            branch = "mean_absolute_deviation"
    return Fit(
        method, n, RationalFact.capture(center), RationalFact.capture(scale), branch, lower, upper
    )


def finish(value: Fraction, typ: ValueType) -> int | float | Decimal:
    """Finish a rational exactly once, without the ambient Decimal context."""
    if isinstance(typ, DecimalType):
        scaled = value * 10**typ.scale
        integer, remainder = divmod(abs(scaled.numerator), scaled.denominator)
        twice = remainder * 2
        if twice > scaled.denominator or (twice == scaled.denominator and integer % 2):
            integer += 1
        if integer >= 10**typ.precision:
            raise OverflowError("final Decimal exceeds its declared precision")
        return Decimal((int(value < 0), tuple(int(c) for c in str(integer)), -typ.scale))
    if typ == ScalarType("int64"):
        if value.denominator != 1 or not -(2**63) <= value < 2**63:
            raise OverflowError("final value exceeds int64")
        return value.numerator
    result = float(value)
    if not math.isfinite(result):
        raise OverflowError("final value exceeds finite float64")
    return result


def certified_score(
    delta: Fraction, fitted: Fit, *, checkpoint: Callable[[], None] = _unbounded
) -> Scored:
    """Certify the nearest binary64 signed score from unrounded fit facts."""
    if fitted.raw_scale is None or fitted.n < 2:
        raise ValueError("score has no usable fitted scale")
    raw = fitted.raw_scale.value()
    if raw <= 0:
        raise ValueError("score has zero scale")
    if fitted.method == "mad":
        result = finish(delta / raw, ScalarType("float64"))
        assert isinstance(result, float)
        return Scored(result, None)
    if delta == 0:
        return Scored(0.0, None)
    numerator, denominator = math.isqrt(raw.numerator), math.isqrt(raw.denominator)
    if numerator**2 == raw.numerator and denominator**2 == raw.denominator:
        result = finish(delta / Fraction(numerator, denominator), ScalarType("float64"))
        assert isinstance(result, float)
        return Scored(result, None)
    precision = 120
    while True:
        checkpoint()
        ctx = Context(prec=precision, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)
        with localcontext(ctx):
            lower_ctx, upper_ctx = ctx.copy(), ctx.copy()
            lower_ctx.rounding, upper_ctx.rounding = ROUND_FLOOR, ROUND_CEILING
            lo = (
                lower_ctx.divide(Decimal(raw.numerator), Decimal(raw.denominator))
                .sqrt(context=ctx)
                .next_minus(context=ctx)
            )
            hi = (
                upper_ctx.divide(Decimal(raw.numerator), Decimal(raw.denominator))
                .sqrt(context=ctx)
                .next_plus(context=ctx)
            )
            if Fraction(lo) ** 2 > raw or Fraction(hi) ** 2 < raw:
                precision += 40
                continue
            bounds = sorted((delta / Fraction(lo), delta / Fraction(hi)))
            left, right = float(bounds[0]), float(bounds[1])
            if left == right and math.isfinite(left):
                return Scored(
                    left, RootCertificate(RationalFact.capture(raw), str(lo), str(hi), precision)
                )
        precision += 40


def score(delta: Fraction, fitted: Fit) -> float:
    return certified_score(delta, fitted).value


def verify_score(
    delta: Fraction, fitted: Fit, value: float, certificate: RootCertificate | None
) -> bool:
    """Check the nearest finish from retained equations, without a new root fit."""
    if type(value) is not float or not math.isfinite(value) or fitted.raw_scale is None:
        return False
    raw = fitted.raw_scale.value()
    if fitted.method == "mad":
        return certificate is None and value == float(delta / raw)
    if delta == 0:
        return certificate is None and value == 0
    if certificate is not None:
        if (
            certificate.precision < 120
            or (certificate.precision - 120) % 40
            or certificate.radicand != fitted.raw_scale
        ):
            return False
        lo, hi = Decimal(certificate.lower), Decimal(certificate.upper)
        if (
            not lo.is_finite()
            or not hi.is_finite()
            or not 0 < lo <= hi
            or len(lo.as_tuple().digits) != certificate.precision
            or len(hi.as_tuple().digits) != certificate.precision
        ):
            return False
        lower, upper = Fraction(lo), Fraction(hi)
        if not lower**2 <= raw <= upper**2:
            return False
        # Directed division, nearest sqrt and outward neighbors stay within eight decimal ulps.
        quantum = Fraction(10) ** (max(lo.adjusted(), hi.adjusted()) - certificate.precision + 1)
        if upper - lower > 8 * quantum:
            return False
        return float(delta / lower) == value == float(delta / upper)
    numerator, denominator = math.isqrt(raw.numerator), math.isqrt(raw.denominator)
    return (
        numerator**2 == raw.numerator
        and denominator**2 == raw.denominator
        and value == float(delta / Fraction(numerator, denominator))
    )


def unit_type(typ: ValueType) -> ValueType:
    return (
        DecimalType(38, max(typ.scale, 6))
        if isinstance(typ, DecimalType)
        else ScalarType("float64")
    )
