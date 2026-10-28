"""Independent exact-rational witnesses for finite permutation sample statistics."""

import math
import sys
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from marivo.analysis.methods.frame_attribute_shapley import _sample_moments, _scaled_sum
from marivo.semantic.errors import SemanticExpressionError


def _oracle(values: list[float]) -> tuple[float, float]:
    exact = [Fraction(value) for value in values]
    mean = sum(exact) / len(exact)
    variance = sum((value - mean) ** 2 for value in exact) / (len(exact) * (len(exact) - 1))
    with localcontext() as context:
        context.prec = 1000
        error = (Decimal(variance.numerator) / Decimal(variance.denominator)).sqrt()
    return float(mean), float(error)


@pytest.mark.parametrize(
    "values",
    [
        [sys.float_info.max, sys.float_info.max],
        [sys.float_info.max, -sys.float_info.max],
        [sys.float_info.max, sys.float_info.max, -sys.float_info.max],
        [sys.float_info.max, 9.0, -sys.float_info.max],
        [sys.float_info.max, math.nextafter(sys.float_info.max, 0.0)],
        [math.ulp(0.0), math.ulp(0.0)],
        [math.ulp(0.0), 3 * math.ulp(0.0), 2 * math.ulp(0.0)],
        [math.exp(200) - 1, math.exp(400) - math.exp(200)] * 4,
    ],
)
def test_scaled_moments_match_independent_rational_oracle(values: list[float]) -> None:
    expected_mean, expected_error = _oracle(values)
    mean, error, budget = _sample_moments(values)
    assert all(math.isfinite(value) for value in (mean, error, budget))
    assert mean == pytest.approx(expected_mean, rel=5e-15, abs=math.ulp(0.0))
    assert error == pytest.approx(expected_error, rel=5e-15, abs=math.ulp(0.0))
    assert abs(Fraction(mean) - sum(map(Fraction, values)) / len(values)) <= Fraction(budget)


def test_scaled_closure_sum_cancels_before_overflow_and_retains_subnormal() -> None:
    maximum, tiny = sys.float_info.max, math.ulp(0.0)
    assert _scaled_sum((maximum, maximum, -maximum))[0] == maximum
    assert _scaled_sum((tiny, 0.0))[0] == tiny
    assert _scaled_sum((maximum, -maximum, 9.0))[0] == 9.0


def test_genuinely_unrepresentable_sum_keeps_numeric_failure_owner() -> None:
    with pytest.raises(SemanticExpressionError, match="finite Float64 sampled sum"):
        _scaled_sum((sys.float_info.max, sys.float_info.max))


def test_unrepresentable_statistic_retains_metric_and_parent_context() -> None:
    from marivo.analysis.core.attribute import AttributeFailureContext, AttributeParentFiber
    from marivo.analysis.methods.frame_attribute_shapley import _attribute_numeric_context

    parent = (("region", "West"),)
    with (
        pytest.raises(SemanticExpressionError) as caught,
        _attribute_numeric_context((), "revenue_growth", parent, ()),
    ):
        _scaled_sum((sys.float_info.max, sys.float_info.max))
    context = caught.value.details["attribute_context"]
    assert isinstance(context, AttributeFailureContext)
    assert isinstance(context.fiber, AttributeParentFiber)
    assert context.fiber.parent == parent
    assert "metric=revenue_growth" in str(caught.value.received)
