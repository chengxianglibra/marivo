"""Prove support, nullable sum, and failure obligations for a shared conditional scan."""

from dataclasses import dataclass

import pytest
import z3  # type: ignore[import-not-found, import-untyped, unused-ignore]  # Optional untyped solver.

from tests.formal._summary_monoids import Cell, result, same


@dataclass(frozen=True, slots=True)
class _Summary:
    rows: z3.ArithRef
    valid: z3.ArithRef
    total: z3.ArithRef
    numeric_failure: z3.BoolRef
    predicate_failure: z3.BoolRef

    def observation(self) -> Cell:
        return Cell(
            z3.If(self.rows == 0, z3.RealVal(0), self.total),
            z3.And(self.rows > 0, self.valid == 0),
        )

    def fails(self, demand: z3.BoolRef, scalar: bool) -> z3.BoolRef:
        # A scalar aggregate always has a numeric coordinate. A grouped
        # predicate failure outside its support is checked before any formula.
        present = z3.Or(scalar, self.rows > 0)
        orphan = z3.And(self.predicate_failure, z3.Not(present))
        return z3.Or(orphan, z3.And(demand, z3.Or(self.numeric_failure, self.predicate_failure)))


def _symbolic_summary() -> _Summary:
    rows, valid = z3.Ints("prior_rows prior_valid")
    total = z3.Real("prior_total")
    numeric_failure, predicate_failure = z3.Bools("prior_numeric_failure prior_predicate_failure")
    return _Summary(rows, valid, total, numeric_failure, predicate_failure)


def _independent_scan(
    prior: _Summary,
    selected: z3.BoolRef,
    missing: z3.BoolRef,
    cell: z3.ArithRef,
    predicate_check: z3.BoolRef,
    strict: bool,
) -> _Summary:
    return _Summary(
        z3.If(selected, prior.rows + 1, prior.rows),
        z3.If(selected, z3.If(missing, prior.valid, prior.valid + 1), prior.valid),
        z3.If(selected, z3.If(missing, prior.total, prior.total + cell), prior.total),
        z3.If(z3.And(selected, strict, missing), True, prior.numeric_failure),
        z3.If(predicate_check, True, prior.predicate_failure),
    )


def _shared_scan(
    prior: _Summary,
    in_union: z3.BoolRef,
    selected: z3.BoolRef,
    missing: z3.BoolRef,
    cell: z3.ArithRef,
    predicate_check: z3.BoolRef,
    strict: bool,
) -> _Summary:
    counted = z3.And(in_union, selected)
    valid = z3.And(counted, z3.Not(missing))
    retained = z3.Or(in_union, predicate_check)
    return _Summary(
        prior.rows + z3.If(counted, 1, 0),
        prior.valid + z3.If(valid, 1, 0),
        prior.total + z3.If(valid, cell, z3.RealVal(0)),
        z3.Or(prior.numeric_failure, z3.And(counted, strict, missing)),
        z3.Or(prior.predicate_failure, z3.And(retained, predicate_check)),
    )


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("scalar", [False, True])
def test_shared_scan_preserves_each_prefix_and_its_demand(strict: bool, scalar: bool) -> None:
    prior = _symbolic_summary()
    truth, unknown, sibling, missing, check, demand = z3.Bools(
        "predicate_truth predicate_null sibling_selected payload_null predicate_check demanded"
    )
    cell = z3.Real("payload")
    selected = z3.And(z3.Not(unknown), truth)
    shared = _shared_scan(prior, z3.Or(selected, sibling), selected, missing, cell, check, strict)
    independent = _independent_scan(prior, selected, missing, cell, check, strict)
    result(
        "one-row induction from every admitted state, including the empty base",
        z3.And(
            shared.rows == independent.rows,
            shared.valid == independent.valid,
            shared.total == independent.total,
            (shared.rows > 0) == (independent.rows > 0),
            same(shared.observation(), independent.observation()),
            shared.numeric_failure == independent.numeric_failure,
            shared.predicate_failure == independent.predicate_failure,
            shared.fails(demand, scalar) == independent.fails(demand, scalar),
        ),
        prior.valid >= 0,
        prior.rows >= prior.valid,
        logic="QF_LIRA",
    )


def test_sibling_contribution_and_excluded_check_are_independent() -> None:
    zero = _Summary(z3.IntVal(0), z3.IntVal(0), z3.RealVal(0), z3.BoolVal(False), z3.BoolVal(False))
    selected, sibling, missing, check = z3.Bools("selected sibling payload_null predicate_check")
    in_union = z3.Or(selected, sibling)
    actual = _shared_scan(zero, in_union, selected, missing, z3.RealVal(10), check, True)
    result(
        "using union row count changes the empty branch into an all-NA branch",
        (actual.rows > 0) == in_union,
        z3.Not(selected),
        sibling,
        missing,
        logic="QF_LIRA",
        counterexample=True,
    )
    result(
        "omitting selection from the strict check fails an empty branch",
        actual.numeric_failure == missing,
        z3.Not(selected),
        sibling,
        missing,
        logic="QF_LIRA",
        counterexample=True,
    )
    result(
        "checking a field predicate after the union filter loses the offending row",
        actual.predicate_failure == z3.And(in_union, check),
        z3.Not(selected),
        z3.Not(sibling),
        check,
        logic="QF_LIRA",
        counterexample=True,
    )
    result(
        "the absent group fails before formula demand, while the scalar unit has a value row",
        z3.And(
            actual.fails(z3.BoolVal(False), False), z3.Not(actual.fails(z3.BoolVal(False), True))
        ),
        z3.Not(selected),
        z3.Not(sibling),
        check,
        logic="QF_LIRA",
    )
