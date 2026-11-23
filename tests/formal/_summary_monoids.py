"""Symbolic cells, finite bag oracles and Ibis-expression translation for L6."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import TypeAlias

import ibis.expr.operations as ops
import z3  # type: ignore[import-not-found, import-untyped, unused-ignore]  # Optional untyped solver.

Value: TypeAlias = int | float | Decimal | Fraction | None
_TRUE = z3.BoolVal(True)
_FALSE = z3.BoolVal(False)


@dataclass(frozen=True)
class Cell:
    """Separate SQL validity from an observable, valid IEEE NaN."""

    value: z3.ExprRef
    null: z3.BoolRef = _FALSE
    nan: z3.BoolRef = _FALSE


def same(left: Cell, right: Cell) -> z3.BoolRef:
    return z3.And(
        left.null == right.null,
        z3.Or(
            left.null,
            z3.And(left.nan == right.nan, z3.Or(left.nan, left.value == right.value)),
        ),
    )


def ite(guard: z3.BoolRef, yes: Cell, no: Cell) -> Cell:
    return Cell(
        z3.If(guard, yes.value, no.value),
        z3.If(guard, yes.null, no.null),
        z3.If(guard, yes.nan, no.nan),
    )


def _literal(value: object) -> Cell:
    if value is None:
        return Cell(z3.RealVal(0), _TRUE)
    if type(value) is bool:
        return Cell(z3.BoolVal(value))
    if isinstance(value, (int, Decimal, float)):
        return Cell(z3.RealVal(str(Fraction(value))))
    raise AssertionError(f"Unsupported Ibis literal {value!r}")


def translate_finisher(node: ops.Value, inputs: dict[ops.Value, Cell]) -> Cell:
    """Translate the emitted Ibis nodes, with the actual aggregate leaves bound independently."""
    if node in inputs:
        return inputs[node]
    if isinstance(node, ops.Literal):
        return _literal(node.value)
    if isinstance(node, ops.Cast):
        assert node.dtype.is_numeric()
        assert node.arg.dtype.copy(nullable=True) == node.dtype.copy(nullable=True) or (
            isinstance(node.arg, ops.Literal) and node.arg.value in (0, None)
        ), f"Out-of-fragment precision-changing cast {node!r}"
        return translate_finisher(node.arg, inputs)
    if isinstance(node, ops.Equals):
        left = translate_finisher(node.left, inputs)
        right = translate_finisher(node.right, inputs)
        assert z3.is_false(left.nan) and z3.is_false(right.nan), "Count comparisons exclude NaN"
        return Cell(left.value == right.value, z3.Or(left.null, right.null))
    if isinstance(node, ops.IfElse):
        condition = translate_finisher(node.bool_expr, inputs)
        assert z3.is_bool(condition.value)
        yes = translate_finisher(node.true_expr, inputs)
        no = translate_finisher(node.false_null_expr, inputs)
        return ite(z3.And(z3.Not(condition.null), condition.value), yes, no)
    raise AssertionError(f"Finisher contains unsupported production node {type(node).__name__}")


def result(
    label: str,
    proposition: z3.BoolRef,
    *premises: z3.BoolRef,
    logic: str,
    counterexample: bool = False,
) -> None:
    solver = z3.SolverFor(logic)
    solver.set(timeout=5_000)
    solver.add(*premises)
    assert solver.check() == z3.sat, f"{label}: no admissible input"
    solver.add(z3.Not(proposition))
    status = solver.check()
    if counterexample:
        assert status == z3.sat, f"{label}: expected a precise counterexample, got {status}"
        print(f"{label}: {status}\n{solver.model()}")
        return
    if status == z3.sat:
        raise AssertionError(f"{label}: counterexample\n{solver.model()}")
    assert status == z3.unsat, f"{label}: {status} ({solver.reason_unknown()})"
    print(f"{label}: {status}")


def monoid_laws(
    merge: Callable[[tuple[z3.ExprRef, ...], tuple[z3.ExprRef, ...]], tuple[z3.ExprRef, ...]],
    unit: tuple[z3.ExprRef, ...],
    a: tuple[z3.ExprRef, ...],
    b: tuple[z3.ExprRef, ...],
    c: tuple[z3.ExprRef, ...],
) -> z3.BoolRef:
    def equal(left: tuple[z3.ExprRef, ...], right: tuple[z3.ExprRef, ...]) -> z3.BoolRef:
        return z3.And(*(x == y for x, y in zip(left, right, strict=True)))

    return z3.And(
        equal(merge(a, unit), a),
        equal(merge(unit, a), a),
        equal(merge(a, b), merge(b, a)),
        equal(merge(merge(a, b), c), merge(a, merge(b, c))),
    )


def vectors(
    names: str, width: int, factory: Callable[[str], z3.ExprRef]
) -> tuple[tuple[z3.ExprRef, ...], ...]:
    return tuple(tuple(factory(f"{name}_{index}") for index in range(width)) for name in names)


def plus(left: tuple[z3.ExprRef, ...], right: tuple[z3.ExprRef, ...]) -> tuple[z3.ExprRef, ...]:
    return tuple(x + y for x, y in zip(left, right, strict=True))


def merge_default(
    left: tuple[z3.ExprRef, ...], right: tuple[z3.ExprRef, ...]
) -> tuple[z3.ExprRef, ...]:
    left_kind, left_sum = left
    right_kind, right_sum = right
    return z3.If(left_kind >= right_kind, left_kind, right_kind), left_sum + right_sum


def finish_default(state: tuple[z3.ExprRef, ...]) -> Cell:
    kind, mass = state
    return Cell(mass, kind == 1)


def merge_strict(
    left: tuple[z3.ExprRef, ...], right: tuple[z3.ExprRef, ...]
) -> tuple[z3.ExprRef, ...]:
    failure = z3.Or(left[0], right[0])
    return failure, z3.If(failure, z3.RealVal(0), left[1] + right[1])


def exact_sum(values: Sequence[Value]) -> Value:
    """Direct bag specification. NA participates in presence, not arithmetic."""
    if not values:
        return 0
    valid = tuple(value for value in values if value is not None)
    return sum(valid) if valid else None


def rank_correlation(pairs: Sequence[tuple[int, int]]) -> Fraction:
    """Exact no-tie rank correlation for the distinct small counterexamples."""
    count = len(pairs)
    xs, ys = zip(*pairs, strict=True)
    assert len(set(xs)) == len(set(ys)) == count and count > 1
    x_rank = {value: rank for rank, value in enumerate(sorted(xs), start=1)}
    y_rank = {value: rank for rank, value in enumerate(sorted(ys), start=1)}
    squared = sum((x_rank[x] - y_rank[y]) ** 2 for x, y in pairs)
    return 1 - Fraction(6 * squared, count * (count**2 - 1))


def distinct_times(times: Sequence[z3.ArithRef]) -> z3.BoolRef:
    """One coordinate per complete time key, independently of payload validity."""
    return z3.Distinct(*times)
