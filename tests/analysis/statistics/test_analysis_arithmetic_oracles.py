"""Independent arithmetic counterexamples for current methods."""

import math
from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import combinations
from statistics import NormalDist, correlation, median

import pyarrow as pa
import pytest

from marivo.analysis.core.model import DerivedQuantity, PairInputsPart, Quantity
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.deviation_execution import SavedPart, load, save
from marivo.analysis.materialization.statistical_execution import (
    Input,
    PairCapture,
    association_state,
)
from marivo.analysis.methods import association_numeric as association
from marivo.analysis.methods import deviation_numeric as deviation
from marivo.analysis.methods import forecast_numeric as forecast
from marivo.analysis.methods import statistical_numeric as certified
from marivo.analysis.methods.attribution import Basis, allocate, numeric, reconciles
from marivo.analysis.methods.physical import DecimalType, ScalarType
from tests.analysis.statistics.runs_fixtures import capture


def reference(x: tuple[int, ...], y: tuple[int, ...], method: association.Method) -> float:
    """Use stdlib correlation and pair enumeration, independently of Marivo witnesses."""
    if method == "pearson":
        return correlation(x, y)
    if method == "spearman":

        def ranks(values: tuple[int, ...]) -> list[float]:
            ordered = sorted(values)
            return [
                sum(i + 1 for i, v in enumerate(ordered) if v == value) / ordered.count(value)
                for value in values
            ]

        return correlation(ranks(x), ranks(y))
    c = d = tx = ty = 0
    for i, j in combinations(range(len(x)), 2):
        dx, dy = (x[i] > x[j]) - (x[i] < x[j]), (y[i] > y[j]) - (y[i] < y[j])
        c += int(dx * dy > 0)
        d += int(dx * dy < 0)
        tx += int(dx == 0 and dy != 0)
        ty += int(dy == 0 and dx != 0)
    return (c - d) / math.sqrt((c + d + tx) * (c + d + ty))


@pytest.mark.parametrize(
    "values",
    [
        (-1, 0, 1),
        (1, 2, 3, 7, 8, 9),
        (2**60, 2**60 + 1, 2**60 + 2),
        (1e308, -1e308, 1e308),
        (1e-300, 2e-300, 3e-300),
        (Decimal("1.00000000000000001"), Decimal(2), Decimal(3)),
    ],
)
def test_signed_population_zscore_preserves_raw_counterexamples(
    values: tuple[int | float | Decimal, ...],
) -> None:
    exact = tuple(Fraction(v) for v in values)
    center = sum(exact, Fraction()) / len(exact)
    variance = sum(((v - center) ** 2 for v in exact), Fraction()) / len(exact)
    fitted = deviation.fit(exact, "zscore")
    assert fitted.center is not None and fitted.center.value() == center
    with localcontext() as ctx:
        ctx.prec = 800
        scale = (Decimal(variance.numerator) / Decimal(variance.denominator)).sqrt()
        wanted = tuple(
            float((Decimal((v - center).numerator) / Decimal((v - center).denominator)) / scale)
            for v in exact
        )
    assert tuple(deviation.score(v - center, fitted) for v in exact) == wanted


@pytest.mark.parametrize(
    "values",
    [
        (1, 1, 1, 10),
        (1, 2, 3, 4, 12),
        (-7, -1, 0, 1, 9),
        (-10, -1, -1, -1),
        (-9, -2, 2, 9),
        (0, 0, 0),
        (1,),
        (),
    ],
)
def test_mad_fallback_and_degenerate_state(values: tuple[int, ...]) -> None:
    fitted = deviation.fit(tuple(Fraction(v) for v in values), "mad")
    if len(values) < 2:
        assert fitted.n == len(values)
        assert (
            fitted.raw_scale is None
            if not values
            else fitted.raw_scale is not None and fitted.raw_scale.value() == 0
        )
    elif len(set(values)) == 1:
        assert fitted.raw_scale is not None and fitted.raw_scale.value() == 0
    else:
        center = median(tuple(Fraction(v) for v in values))
        deviations = tuple(abs(Fraction(v) - center) for v in values)
        mad = median(deviations)
        scale = mad * Fraction(7413, 5000) if mad else sum(deviations, Fraction()) / len(values)
        assert fitted.center is not None and fitted.center.value() == center
        assert fitted.raw_scale is not None and fitted.raw_scale.value() == scale
        assert fitted.branch == ("scaled_mad" if mad else "mean_absolute_deviation")
        scores = tuple(deviation.score(Fraction(v) - center, fitted) for v in values)
        assert scores == tuple(float((Fraction(v) - center) / scale) for v in values)


@pytest.mark.parametrize("method", ("pearson", "spearman", "kendall"))
@pytest.mark.parametrize(
    "x,y",
    [
        ((1, 2, 3, 4), (8, 6, 4, 2)),
        ((1, 1, 3, 5), (4, 4, 2, 1)),
        ((1, 2, 2, 4, 5), (2, 1, 1, 3, 4)),
        ((1, 3, 2, 5, 4), (5, 1, 4, 2, 3)),
        ((1, 2, 2, 5, 8), (3, 1, 1, 4, 9)),
    ],
)
def test_independent_pair_order_and_ties(
    method: association.Method, x: tuple[int, ...], y: tuple[int, ...]
) -> None:
    expected = reference(x, y, method)
    for offset in (0, 2**63 - 10):
        actual = association.score(
            tuple(Fraction(v + offset) for v in x), tuple(Fraction(v + offset) for v in y), method
        )
        assert actual.status == "valid"
        assert actual.coefficient == pytest.approx(expected, abs=1e-15)


@pytest.mark.parametrize(
    "model,season,values,points,variances",
    [
        (
            "naive",
            None,
            (1, 2, 3),
            (3, 3, 3, 3),
            (Fraction(1), Fraction(2), Fraction(3), Fraction(4)),
        ),
        (
            "drift",
            None,
            (1, 2, 4, 4),
            (5, 6, 7, 8),
            (Fraction(4, 3), Fraction(10, 3), Fraction(6), Fraction(28, 3)),
        ),
        (
            "seasonal_naive",
            2,
            (1, 4, 2, 6, 4),
            (6, 4, 6, 4),
            (Fraction(3), Fraction(3), Fraction(6), Fraction(6)),
        ),
    ],
)
def test_normative_forecast_point_and_variance(
    model: forecast.Model,
    season: int | None,
    values: tuple[int, ...],
    points: tuple[int, ...],
    variances: tuple[Fraction, ...],
) -> None:
    raw = tuple(Fraction(v) for v in values)
    trained = forecast.train(raw, model, season)
    assert trained.df == {"naive": 2, "drift": 2, "seasonal_naive": 3}[model]
    assert not trained.exact_zero
    assert tuple(forecast.point(raw, trained, h) for h in range(1, 5)) == tuple(
        zip(tuple(Fraction(p) for p in points), variances, strict=True)
    )
    z = NormalDist().inv_cdf(0.975)
    q = certified.quantile(0.95)
    for point, variance in zip(points, variances, strict=True):
        lower, upper, _ = certified.interval(Fraction(point), variance, q, ScalarType("float64"))
        assert lower == pytest.approx(point - z * math.sqrt(variance))
        assert upper == pytest.approx(point + z * math.sqrt(variance))


@pytest.mark.parametrize(
    "values",
    [
        (10, -10, 0),
        (2**60, -(2**60), 1),
        (Decimal("1234567890123456789012345678.1"), Decimal("-1234567890123456789012345678.0")),
        (1e308, -1e308, 1.0),
        (float.fromhex("0x0.0000000000001p-1022"),),
        (1e16, 1.0, -1e16),
        (0.1, 0.2, 0.3),
        (5e-324, 5e-324),
        (1e308, -1e308, 0.75),
        (1e16, 1.0, 1.0, -1e16, 0.75),
    ],
)
def test_explicit_attribution_signed_fold_and_order(
    values: tuple[int | float | Decimal, ...],
) -> None:
    physical = (
        DecimalType(38, 1)
        if isinstance(values[0], Decimal)
        else ScalarType("float64")
        if isinstance(values[0], float)
        else ScalarType("int64")
    )
    target = sum((Fraction(v) for v in values), Fraction())
    rows = allocate(
        {(str(i),): (numeric(v), Fraction(1)) for i, v in enumerate(values)},
        {},
        size=1,
        mode="joint",
        top_k=None,
        component_mix=False,
        physical=physical,
        current_total=Fraction(1),
        baseline_total=Fraction(1),
    )
    assert reconciles(target, [r[4] for r in rows], True)
    reverse = allocate(
        {(str(i),): (numeric(v), Fraction(1)) for i, v in reversed(tuple(enumerate(values)))},
        {},
        size=1,
        mode="joint",
        top_k=None,
        component_mix=False,
        physical=physical,
        current_total=Fraction(1),
        baseline_total=Fraction(1),
    )
    assert rows == reverse


@pytest.mark.parametrize("values", [(1e308, 1e308, -1e308), (-1e308, 1e308, 1e308)])
def test_explicit_attribution_fold_survives_intermediate_overflow(
    values: tuple[float, ...],
) -> None:
    basis: Basis = {("a", str(i)): (numeric(v), Fraction(1)) for i, v in enumerate(values)}
    expected = float(sum((Fraction(v) for v in values), Fraction()))
    for current in (basis, dict(reversed(tuple(basis.items())))):
        rows = allocate(
            current,
            {},
            size=2,
            mode="hierarchy",
            top_k=None,
            component_mix=False,
            physical=ScalarType("float64"),
            current_total=Fraction(1),
            baseline_total=Fraction(1),
        )
        assert [row[4] for row in rows if row[0] == 1] == [expected]


@pytest.mark.parametrize("values", [(1, 2, 3), (2, 4, 6), (2**60, 2**60 + 1, 2**60 + 2)])
def test_original_forecast_innovations_preserve_uncertainty(values: tuple[int, ...]) -> None:
    raw = tuple(map(Fraction, values))
    trained = forecast.train(raw, "naive", None)
    assert trained.sigma2.value() == (4 if values[0] == 2 else 1)
    q = certified.quantile(0.95)
    # Decimal output makes the nonzero interval visible even at a large common level.
    point, variance = forecast.point(raw, trained, 1)
    lower, upper, _ = certified.interval(point, variance, q, DecimalType(38, 6))
    assert lower < point < upper


@pytest.mark.parametrize("values", [(1e308, -1e308, 1e308), (1e-300, 2e-300, 3e-300)])
def test_forecast_extreme_innovations_cannot_become_exact_zero(values: tuple[float, ...]) -> None:
    raw = tuple(map(Fraction, values))
    trained = forecast.train(raw, "naive", None)
    assert trained.sigma2.value() > 0 and not trained.exact_zero
    point, variance = forecast.point(raw, trained, 1)
    q = certified.quantile(0.95)
    if values[0] == 1e308:
        with pytest.raises(OverflowError):
            certified.interval(point, variance, q, ScalarType("float64"))
    else:
        lower, upper, _ = certified.interval(point, variance, q, ScalarType("float64"))
        assert lower < point < upper


def test_association_checkpoint_prevents_return() -> None:
    def stop() -> None:
        raise TimeoutError("independent cancellation")

    with pytest.raises(TimeoutError):
        association.score(
            tuple(map(Fraction, range(40))),
            tuple(map(Fraction, range(40))),
            "kendall",
            checkpoint=stop,
        )


def pair_capture(method: association.Method, lags: tuple[int, ...]) -> PairCapture:
    """Bind the old sparse calendar facts to a complete, explicitly Null grid."""
    original = capture((1,) * 7, start="2026-02-01", end="2026-02-08")
    template = load(original.inputs[0])
    parts = (
        SavedPart(
            "coverage",
            ("key_0",),
            save(load(original.coverage).rename_columns(["key_0", "coverage__complete"])),
        ),
    )
    inputs = []
    quantities: list[Quantity] = []
    for label, vector in (
        ("left", (1, None, 5, 3, 9, None, 4)),
        ("right", (3, None, 4, 7, 2, None, 6)),
    ):
        quantity = DerivedQuantity(label, "input@v1", (label,), None, "time", "strict")
        quantities.append(quantity)
        signature = replace(original.signature, quantity=quantity)
        table = pa.table(
            {
                "key_0": template["key_0"],
                "value": pa.array(vector, type=pa.int64()),
                "cell_tag": ["defined" if v is not None else "null" for v in vector],
                "cell_reason": pa.array(
                    [None if v is not None else "source_null" for v in vector], type=pa.string()
                ),
            }
        )
        inputs.append(
            Input(
                signature,
                "int64",
                label,
                ("key_0",),
                save(table),
                parts,
                (("null", ("source_null",)),),
            )
        )
    signature = original.signature
    return PairCapture(
        PairInputsPart(
            signature.domain.binding,
            signature.domain,
            tuple(quantities),
            ("int64", "int64"),
            "r85-sparse-facts",
            method,
            lags,
            True,
            (signature.domain, signature.domain),
        ),
        tuple(inputs),
    )


@pytest.mark.parametrize("method", ("pearson", "spearman", "kendall"))
def test_calendar_lags_null_counts_and_minimum_signed_offset(method: association.Method) -> None:
    saved = pair_capture(method, (-(2**63), -2, -1, 0, 1, 2))
    state = association_state(saved)
    plus_one = next(c for c in state.candidates if c.lag == 1)
    assert (
        plus_one.input_count,
        plus_one.matched,
        plus_one.null_pairs,
        plus_one.complete_pairs,
    ) == (7, 6, 4, 2)
    assert plus_one.score.coefficient == reference((5, 3), (7, 2), method)
    minimum = state.candidates[0]
    assert minimum.lag == -(2**63)
    assert (minimum.matched, minimum.boundary_drop, minimum.complete_pairs) == (0, 7, 0)
    assert minimum.score.status == "insufficient_pairs" and not minimum.selected
    assert sum(c.selected for c in state.candidates) == 1
    # Removing rows is a broken captured grid, never a positional shift.
    first = saved.inputs[0]
    broken = replace(first, primary=save(load(first.primary).slice(1)))
    with pytest.raises(AnalysisError):
        association_state(replace(saved, inputs=(broken, saved.inputs[1])))


@pytest.mark.parametrize("bad", (float("nan"), float("inf"), -float("inf")))
def test_nonfinite_input_cannot_be_deleted_as_null(bad: float) -> None:
    with pytest.raises(ValueError):
        deviation.exact(bad, ScalarType("float64"))
    from marivo.analysis.materialization.statistical_execution import _input

    saved = pair_capture("pearson", (0,))
    first = saved.inputs[0]
    table = load(first.primary).set_column(
        1, "value", pa.array([1, bad, 5, 3, 9, 0, 4], type=pa.float64())
    )
    table = table.set_column(2, "cell_tag", pa.array(["defined"] * 7))
    table = table.set_column(3, "cell_reason", pa.nulls(7, type=pa.string()))
    poisoned = replace(first, value_type="float64", primary=save(table))
    for forecast_input in (False, True):
        with pytest.raises(AnalysisError):
            _input(poisoned, method="independent-admission", forecast_input=forecast_input)
