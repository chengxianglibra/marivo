"""Independent exact-vector and normative forecast equation oracles."""

from decimal import Decimal, localcontext
from fractions import Fraction
from math import nextafter

import pytest

from marivo.analysis.methods import association_numeric as association
from marivo.analysis.methods import forecast_numeric as forecast
from marivo.analysis.methods import statistical_numeric as certified
from marivo.analysis.methods.physical import DecimalType, ScalarType


@pytest.mark.parametrize("method", ("pearson", "spearman", "kendall"))
@pytest.mark.parametrize("offset", (0, 2**63 - 10))
def test_original_integer_order_and_ties(method: association.Method, offset: int) -> None:
    x = tuple(Fraction(offset + v) for v in (1, 1, 3, 5))
    y = tuple(Fraction(v) for v in (4, 4, 2, 1))
    numerator, radicand = {
        "pearson": (Fraction(-17, 2), Fraction(297, 4)),
        "spearman": (Fraction(-9, 2), Fraction(81, 4)),
        "kendall": (Fraction(-5), Fraction(25)),
    }[method]
    with localcontext() as ctx:
        ctx.prec = 180
        expected = float(
            (Decimal(numerator.numerator) / Decimal(numerator.denominator))
            / (Decimal(radicand.numerator) / Decimal(radicand.denominator)).sqrt()
        )
    result = association.score(x, y, method)
    assert result.status == "valid"
    assert result.coefficient == expected
    assert result.numerator.value() == numerator
    assert result.radicand.value() == radicand


@pytest.mark.parametrize(
    "x,y,status",
    (
        ((), (), "insufficient_pairs"),
        ((1,), (2,), "insufficient_pairs"),
        ((1, 1), (2, 2), "constant_both"),
        ((1, 1), (2, 3), "constant_a"),
        ((1, 2), (3, 3), "constant_b"),
    ),
)
@pytest.mark.parametrize("method", ("pearson", "spearman", "kendall"))
def test_closed_status_precedence(
    x: tuple[int, ...], y: tuple[int, ...], status: str, method: association.Method
) -> None:
    result = association.score(tuple(map(Fraction, x)), tuple(map(Fraction, y)), method)
    assert result.status == status and result.coefficient is None


@pytest.mark.parametrize(
    "model,season,innovations,df,sigma2,predictions,variances",
    (
        ("naive", None, (2, 3), 2, Fraction(13, 2), (6, 6), (Fraction(13, 2), Fraction(13))),
        (
            "drift",
            None,
            (Fraction(-1, 2), Fraction(1, 2)),
            1,
            Fraction(1, 2),
            (Fraction(17, 2), 11),
            (Fraction(3, 4), Fraction(2)),
        ),
        ("seasonal_naive", 2, (5,), 1, Fraction(25), (3, 6), (Fraction(25), Fraction(25))),
    ),
)
def test_normative_innovation_df_and_horizon_variance(
    model: forecast.Model,
    season: int | None,
    innovations: tuple[Fraction | int, ...],
    df: int,
    sigma2: Fraction,
    predictions: tuple[Fraction | int, ...],
    variances: tuple[Fraction, ...],
) -> None:
    vector = tuple(map(Fraction, (1, 3, 6)))
    training = forecast.train(vector, model, season)
    assert tuple(i.value() for i in training.innovations) == innovations
    assert (training.df, training.sigma2.value(), training.exact_zero) == (df, sigma2, False)
    assert tuple(forecast.point(vector, training, h) for h in (1, 2)) == tuple(
        zip(predictions, variances, strict=True)
    )


def test_constant_nonzero_innovations_are_not_demeaned() -> None:
    fitted = forecast.train(tuple(map(Fraction, (1, 3, 5))), "naive", None)
    assert fitted.sigma2.value() == 4 and not fitted.exact_zero
    drift = forecast.train(tuple(map(Fraction, (1, 3, 5))), "drift", None)
    assert drift.sigma2.value() == 0 and drift.exact_zero


@pytest.mark.parametrize("level", (0.95, nextafter(1.0, 0.0), 1e-30))
def test_certified_quantile_rounding_and_zero_interval(level: float) -> None:
    q = certified.quantile(level)
    assert certified.verify_quantile(q, level)
    if level == 0.95:
        assert float(q.lower) == float(q.upper) == 1.9599639845400538
    for typ in (ScalarType("float64"), DecimalType(38, 6)):
        lo, hi, certificate = certified.interval(Fraction(7), Fraction(), q, typ)
        assert lo == hi == 7 and certified.verify_root(certificate, Fraction())


def test_decimal_endpoint_single_final_rounding() -> None:
    q = certified.quantile(0.95)
    lo, hi, _ = certified.interval(Fraction(1, 3), Fraction(1, 9), q, DecimalType(38, 6))
    assert (lo, hi) == (Decimal("-0.319988"), Decimal("0.986655"))


def _oracle_cdf(z: Decimal) -> Decimal:
    """Evaluate the defining integral independently at 220 Decimal digits."""
    pi = Decimal(
        "3.141592653589793238462643383279502884197169399375105820974944592307816406286208998628034825342117067982148086513282306647093844609550582231725359408128481117450284102701938521105559644622948954930381964428810975665933446128475648233786783165271201909145648566923460"
    )
    with localcontext() as ctx:
        ctx.prec = 220
        integral, term = z, z
        n = 0
        while True:
            term *= -(z * z) * Decimal(2 * n + 1) / Decimal(2 * (n + 1) * (2 * n + 3))
            integral += term
            n += 1
            if abs(term) < Decimal("1e-210"):
                break
        return Decimal("0.5") + integral / (2 * pi).sqrt()


@pytest.mark.parametrize("level", (0.5, 0.95, nextafter(1.0, 0.0), 1e-30))
def test_normal_enclosure_against_independent_integral(level: float) -> None:
    q = certified.quantile(level)
    with localcontext() as ctx:
        ctx.prec = 220
        probability = (1 + Decimal.from_float(level)) / 2
        assert _oracle_cdf(Decimal(q.lower)) < probability < _oracle_cdf(Decimal(q.upper))


@pytest.mark.parametrize(
    "model,season,history",
    (("naive", None, (1,)), ("drift", None, (1, 2)), ("seasonal_naive", 2, (1, 2))),
)
def test_short_histories_reject_instead_of_zero_variance(
    model: forecast.Model, season: int | None, history: tuple[int, ...]
) -> None:
    with pytest.raises(ValueError):
        forecast.train(tuple(map(Fraction, history)), model, season)


@pytest.mark.parametrize("bad", (0, 1001, True))
def test_horizon_factory_boundaries(bad: int) -> None:
    import marivo.analysis as mv
    from marivo.analysis.errors import AnalysisError

    with pytest.raises(AnalysisError):
        mv.periods(bad)


def test_horizon_and_season_factory_endpoints() -> None:
    import marivo.analysis as mv

    assert (mv.periods(1).count, mv.periods(1000).count) == (1, 1000)
    assert mv.seasonal_naive(periods=2).season_length == 2
