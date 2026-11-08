"""Every demanded coalition intermediate must fit its original numeric carrier."""

from fractions import Fraction

import ibis

from marivo.analysis.methods.attribute_game_domain import Domain, prove_domain


def test_domain_preserves_demand_for_unreachable_overflow() -> None:
    table = ibis.table({"x": "float64"}, name="leaves")
    expression = (table.x < 0).ifelse(table.x.exp(), 0)
    assert prove_domain(expression.op(), (), {"x": Domain(Fraction(0), Fraction(1000))})


def test_domain_accepts_proven_safe_log_and_exp() -> None:
    table = ibis.table({"x": "float64"}, name="leaves")
    fields = {"x": Domain(Fraction(1), Fraction(10))}
    assert prove_domain(table.x.ln().op(), (), fields)
    assert prove_domain(table.x.exp().op(), (), fields)
