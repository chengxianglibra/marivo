"""Independent numerical and common-basis counterexamples for R6.6."""

from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction

import pytest

from marivo.analysis.methods.attribution import (
    allocate,
    allocation_errors,
    mapping,
    numeric,
    reconciles,
)
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType


def test_common_asymmetric_top_k_and_typed_other() -> None:
    current = {("A",): (Fraction(10), Fraction(1)), ("Other",): (Fraction(7), Fraction(1))}
    baseline = {("B",): (Fraction(20), Fraction(1)), ("Other",): (Fraction(8), Fraction(1))}
    selected = mapping(current, baseline, 1, 1, False)
    assert selected[("B",)] == (("B", False),)
    assert selected[("A",)] == ((None, True),)
    assert selected[("Other",)] == ((None, True),)
    full = allocate(
        current,
        baseline,
        size=1,
        mode="joint",
        top_k=None,
        component_mix=False,
        physical=ScalarType("int64"),
        current_total=Fraction(1),
        baseline_total=Fraction(1),
    )
    assert (("Other", False),) in [r[1] for r in full]


@pytest.mark.parametrize(
    "physical, value",
    [
        (ScalarType("int64"), 2**53 + 1),
        (DecimalType(38, 12), Decimal("9007199254740993.123456789012")),
        (DurationType("ns"), 2**53 + 1),
    ],
)
def test_exact_carriers(physical, value) -> None:
    rows = allocate(
        {("a",): (numeric(value), Fraction(1))},
        {("a",): (Fraction(1), Fraction(1))},
        size=1,
        mode="joint",
        top_k=None,
        component_mix=False,
        physical=physical,
        current_total=Fraction(1),
        baseline_total=Fraction(1),
    )
    assert numeric(rows[0][4]) == numeric(value) - 1
    assert reconciles(numeric(value) - 1, [rows[0][4]], True)


def test_component_side_terms_and_hierarchy() -> None:
    current = {("a", "x"): (Fraction(100), Fraction(100)), ("b", "y"): (Fraction(100), Fraction(1))}
    baseline = {("a", "x"): (Fraction(50), Fraction(50)), ("b", "y"): (Fraction(80), Fraction(1))}
    rows = allocate(
        current,
        baseline,
        size=2,
        mode="hierarchy",
        top_k=1,
        component_mix=True,
        physical=ScalarType("float64"),
        current_total=Fraction(101),
        baseline_total=Fraction(51),
    )
    for level in (1, 2):
        assert sum(r[4] for r in rows if r[0] == level) == pytest.approx(
            float(Fraction(200, 101) - Fraction(130, 51))
        )
    assert rows[0][2] == float(Fraction(100, 101))
    assert rows[0][2] != 1


def test_contradictory_component_cannot_cancel_under_other() -> None:
    with pytest.raises(ValueError, match="contradictory"):
        allocate(
            {("a",): (Fraction(1), Fraction(0))},
            {},
            size=1,
            mode="joint",
            top_k=1,
            component_mix=True,
            physical=ScalarType("float64"),
            current_total=Fraction(1),
            baseline_total=Fraction(1),
        )


def test_many_small_decimal_partitions_reject_excess_rounding() -> None:
    count = 3000
    rows = allocate(
        {(str(i),): (Fraction(1), Fraction(1)) for i in range(count)},
        {(str(i),): (Fraction(0), Fraction(1)) for i in range(count)},
        size=1,
        mode="joint",
        top_k=None,
        component_mix=True,
        physical=DecimalType(38, 6),
        current_total=Fraction(count),
        baseline_total=Fraction(count),
    )
    assert sum(numeric(r[4]) for r in rows) == Fraction(999, 1000)
    assert not reconciles(Fraction(1), [r[4] for r in rows], False)
    assert reconciles(Fraction(1), [Decimal("0.999999999")], False)
    assert not reconciles(Fraction(1), [Decimal("0.9999999989")], False)


def test_checked_overflow_and_finite_finish() -> None:
    for physical, number in (
        (ScalarType("int64"), Fraction(2**63)),
        (ScalarType("float64"), Fraction(10**400)),
    ):
        with pytest.raises(OverflowError):
            allocate(
                {("a",): (number, Fraction(1))},
                {},
                size=1,
                mode="joint",
                top_k=None,
                component_mix=False,
                physical=physical,
                current_total=Fraction(1),
                baseline_total=Fraction(1),
            )
    with pytest.raises(ValueError):
        numeric(float("inf"))


def test_high_precision_component_side_difference_uses_exact_carrier() -> None:
    large = Fraction(Decimal("9007199254740993.123456789012"))
    rows = allocate(
        {("a",): (large, Fraction(3)), ("b",): (Fraction(2), Fraction(1))},
        {("a",): (large - 1, Fraction(2)), ("b",): (Fraction(2), Fraction(2))},
        size=1,
        mode="joint",
        top_k=None,
        component_mix=True,
        physical=DecimalType(38, 12),
        current_total=Fraction(4),
        baseline_total=Fraction(4),
    )
    with localcontext() as context:
        context.prec = 70
        expected = (Decimal(large.numerator) / Decimal(large.denominator * 4)).quantize(
            Decimal("0.000000000001"),
            rounding=ROUND_HALF_EVEN,
        )
    assert rows[0][2] == expected
    assert rows[0][4] == Decimal("0.250000000000")
    assert rows[1][4] == Decimal("0.000000000000")
    assert reconciles(Fraction(1, 4), [r[4] for r in rows], False)


def test_component_error_interval_cannot_span_zero() -> None:
    current = {("a",): (Fraction(1), Fraction(1, 10**13))}
    baseline = {("a",): (Fraction(1), Fraction(1))}
    rows = allocate(
        current,
        baseline,
        size=1,
        mode="joint",
        top_k=None,
        component_mix=True,
        physical=ScalarType("float64"),
        current_total=Fraction(1, 10**13),
        baseline_total=Fraction(1),
    )
    with pytest.raises(ValueError, match="interval spans zero"):
        allocation_errors(
            current,
            baseline,
            {("a",): 0.0},
            {("a",): 0.0},
            rows,
            size=1,
            top_k=None,
            component_mix=True,
            current_total=Fraction(1, 10**13),
            baseline_total=Fraction(1),
            current_denominator_error=1e-12,
            baseline_denominator_error=0.0,
        )
