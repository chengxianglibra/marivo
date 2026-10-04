"""Independent rational equations and Decimal root oracle for R8.2."""

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from fractions import Fraction
from typing import Literal

PROFILES = (
    "int64",
    "float64",
    "decimal(9,2)",
    "decimal(18,6)",
    "decimal(38,0)",
    "decimal(38,6)",
    "decimal(38,18)",
    "decimal(38,38)",
)


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("offline deviation touched current Semantic or a source")


def expected(
    values: tuple[int | float | Decimal, ...], method: Literal["zscore", "mad"]
) -> tuple[Fraction, Fraction, str, tuple[float, ...]]:
    """Compute from original carriers without importing the implementation."""
    xs = tuple(Fraction(x) for x in values)
    ordered = sorted(xs)
    n = len(xs)
    center = (
        sum(xs, Fraction()) / n
        if method == "zscore"
        else (ordered[(n - 1) // 2] + ordered[n // 2]) / 2
    )
    if method == "zscore":
        scale = sum(((x - center) ** 2 for x in xs), Fraction()) / n
        branch = "population_stddev"
    else:
        deviations = sorted(abs(x - center) for x in xs)
        mad = (deviations[(n - 1) // 2] + deviations[n // 2]) / 2
        scale = mad * Fraction(7413, 5000) if mad else sum(deviations, Fraction()) / n
        branch = "scaled_mad" if mad else "mean_absolute_deviation"
    with localcontext(Context(prec=240, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)):
        divisor = Decimal(scale.numerator) / Decimal(scale.denominator)
        if method == "zscore":
            divisor = divisor.sqrt()
        scores = (
            tuple(
                float(
                    (Decimal((x - center).numerator) / Decimal((x - center).denominator)) / divisor
                )
                for x in xs
            )
            if scale
            else ()
        )
    return center, scale, branch, scores


def decimal_finish(value: Fraction, scale: int) -> Decimal:
    """Independent quantize oracle with enough precision for an exact tie."""
    with localcontext(Context(prec=240, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)):
        return (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
            Decimal((0, (1,), -scale))
        )
