"""Singleton eliminations quantify validity, key support, and carrier changes."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

import ibis
import pyarrow as pa
import pytest
import z3  # type: ignore[import-not-found, import-untyped, unused-ignore]  # Optional formal solver.

from marivo._definition.expressions import EntityCount, PerReduce
from marivo._kernel.refs import ExactRef, ref
from marivo.analysis import grain
from marivo.analysis.compiler.sample_values import coincident_sample_keys, reduce_sample_value
from marivo.analysis.compiler.unique_keys import relation_unique_keys
from marivo.analysis.model.summary_traits import SummaryReducer, summary_trait
from marivo.semantic.spi import finish_nullable_sum
from tests.formal._summary_monoids import Cell, result, same, translate_finisher


def test_source_sum_finisher_preserves_a_present_singleton_cell() -> None:
    table = ibis.table({"x": "float64"})
    child_sum, rows, valid = table.x.sum(), table.count(), table.x.count()
    finished = finish_nullable_sum(child_sum, rows, valid)
    payload = z3.Real("payload")
    null, nan = z3.Bools("singleton_null singleton_nan")
    cell = Cell(payload, null, nan)
    compiled = translate_finisher(
        finished.op(),
        {
            child_sum.op(): cell,
            rows.op(): Cell(z3.RealVal(1)),
            valid.op(): Cell(z3.If(null, z3.RealVal(0), z3.RealVal(1))),
        },
    )
    assert summary_trait("sum").singleton_identity
    result(
        "A represented singleton SUM preserves the sole completed value, SQL NULL and valid NaN",
        same(compiled, cell),
        z3.Not(z3.And(null, nan)),
        logic="QF_LRA",
    )
    empty = translate_finisher(
        finished.op(),
        {
            child_sum.op(): Cell(z3.RealVal(0), z3.BoolVal(True)),
            rows.op(): Cell(z3.RealVal(0)),
            valid.op(): Cell(z3.RealVal(0)),
        },
    )
    result(
        "An empty SUM differs from a missing child with a NULL empty finish",
        same(empty, Cell(z3.RealVal(0), z3.BoolVal(True))),
        logic="QF_LRA",
        counterexample=True,
    )


@pytest.mark.parametrize("reducer", ["min", "max"])
def test_native_extremum_is_singleton_identity_in_the_exact_carrier(
    reducer: Literal["min", "max"],
) -> None:
    assert summary_trait(reducer).singleton_identity
    assert not summary_trait(reducer, nulls_policy="error").singleton_identity
    values = [None, Decimal("0.00"), Decimal("-9007199254740993.25")]
    array = pa.array(values, type=pa.decimal128(20, 2))
    table = ibis.memtable(pa.table({"key": list(range(len(values))), "x": array}))
    child = EntityCount(ExactRef(ref.entity("proof.facts"), "fixture"), None)
    node = PerReduce(child, grain("day"), reducer)
    finished, zero = reduce_sample_value(node, table, ("x",))
    assert finished.type().copy(nullable=True) == table.x.type().copy(nullable=True)
    assert not zero
    backend = ibis.duckdb.connect()
    try:
        output = backend.to_pyarrow(
            table.group_by("key").aggregate(result=finished).order_by("key")
        )
        assert output["result"].to_pylist() == values
        assert output["result"].is_null().to_pylist() == [True, False, False]
    finally:
        backend.disconnect()


@pytest.mark.parametrize("reducer", ["count", "count_distinct", "pearson", "spearman"])
def test_count_and_correlation_counterexamples_refute_singleton_identity(
    reducer: SummaryReducer,
) -> None:
    assert not summary_trait(reducer).singleton_identity
    null = z3.Bool("child_null")
    value = Cell(z3.Real("child_value"), null)
    finished = (
        Cell(z3.If(null, z3.RealVal(0), z3.RealVal(1)))
        if reducer in ("count", "count_distinct")
        else Cell(z3.RealVal(0), z3.BoolVal(True))
    )
    result(
        f"{reducer} does not preserve an arbitrary completed singleton value",
        same(value, finished),
        logic="QF_LRA",
        counterexample=True,
    )


def test_mean_needs_carrier_and_native_finisher_proofs() -> None:
    assert not summary_trait("mean", singleton_finishing_preserved=True).singleton_identity
    assert not summary_trait("mean", carrier_compatible=True).singleton_identity
    assert summary_trait(
        "mean", carrier_compatible=True, singleton_finishing_preserved=True
    ).singleton_identity
    int_value = 2**53 + 1
    rounded = z3.fpSignedToFP(z3.RNE(), z3.BitVecVal(int_value, 64), z3.Float64())
    assert z3.simplify(z3.fpEQ(rounded, z3.FPVal(float(2**53), z3.Float64()))) == z3.BoolVal(True)
    assert int(2.0**53) != int_value


def test_exact_second_grouping_alias_cannot_distinguish_more_samples() -> None:
    table = ibis.table({"outer": "string", "inner": "string"})
    same_field = table.aggregate(n=table.count(), by={"p": table.outer, "s": table.outer})
    different_field = table.aggregate(n=table.count(), by={"p": table.outer, "s": table.inner})
    for relation, claimed in ((same_field, True), (different_field, False)):
        facts = relation_unique_keys(relation)
        assert facts.proves(("p", "s")) and not facts.proves(("p",))
        assert coincident_sample_keys(relation, facts).proves(("p",)) is claimed

    outer_a, outer_b, sample_a, sample_b, row_a, row_b = z3.Ints(
        "outer_a outer_b sample_a sample_b row_a row_b"
    )
    admissible_key = z3.Implies(z3.And(outer_a == outer_b, sample_a == sample_b), row_a == row_b)
    result(
        "Exact duplicate grouping aliases preserve complete key injectivity",
        z3.Implies(outer_a == outer_b, row_a == row_b),
        admissible_key,
        sample_a == outer_a,
        sample_b == outer_b,
        logic="QF_LIA",
    )
    result(
        "Distinct internal samples can share the same parent coordinate",
        z3.Implies(outer_a == outer_b, row_a == row_b),
        admissible_key,
        logic="QF_LIA",
        counterexample=True,
    )


def test_exact_coverage_support_reuses_one_witness_per_key() -> None:
    first, second, third, supplied = z3.Bools(
        "first_required second_required third_required supplied"
    )
    independent = z3.And(z3.Or(first, second, third), z3.Not(supplied))
    shared = z3.And(first, z3.Not(supplied))
    result(
        "Identical required domains have identical missing coordinates",
        independent == shared,
        first == second,
        first == third,
        logic="QF_UF",
    )
    result(
        "A later distinct predicate can require an uncovered coordinate",
        independent == shared,
        z3.And(z3.Not(first), second, z3.Not(supplied)),
        logic="QF_UF",
        counterexample=True,
    )
