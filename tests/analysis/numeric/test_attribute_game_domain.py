"""Every demanded coalition intermediate must fit its original numeric carrier."""

import sys
from decimal import Decimal
from fractions import Fraction

import ibis
import pytest

from marivo.analysis.methods.attribute_game_domain import Domain, DomainProofError, prove_domain


@pytest.mark.parametrize(
    ("dtype", "low", "high", "operation"),
    [
        ("float64", 0.0, sys.float_info.max, "double"),
        ("int64", 0, 2**63 - 1, "increment"),
        ("decimal(3,1)", Decimal("0.0"), Decimal("99.9"), "increment"),
        ("float64", 0.0, 1000.0, "exp"),
    ],
)
def test_domain_refuses_possible_overflow_even_when_only_a_condition_demands_it(
    dtype: str, low: int | float | Decimal, high: int | float | Decimal, operation: str
) -> None:
    table = ibis.table({"x": dtype}, name="leaves")
    value = (
        table.x * 2
        if operation == "double"
        else table.x.exp()
        if operation == "exp"
        else table.x + 1
    )
    expression = (value > 0).ifelse(1, 0)
    with pytest.raises(DomainProofError, match=r"nonfinite|finite"):
        prove_domain(expression.op(), (), {"x": Domain(Fraction(low), Fraction(high))})


def test_domain_preserves_demand_for_unreachable_overflow() -> None:
    table = ibis.table({"x": "float64"}, name="leaves")
    expression = (table.x < 0).ifelse(table.x.exp(), 0)
    assert prove_domain(expression.op(), (), {"x": Domain(Fraction(0), Fraction(1000))})


def test_domain_accepts_proven_safe_log_and_exp() -> None:
    table = ibis.table({"x": "float64"}, name="leaves")
    fields = {"x": Domain(Fraction(1), Fraction(10))}
    assert prove_domain(table.x.ln().op(), (), fields)
    assert prove_domain(table.x.exp().op(), (), fields)
