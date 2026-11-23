"""Independent symbolic values and their interpretation as production Domains."""

from dataclasses import dataclass, replace
from fractions import Fraction

import z3  # type: ignore[import-not-found, import-untyped, unused-ignore]  # Optional untyped solver.

from marivo.analysis.methods.attribute_game_domain import Domain

_FINITE = z3.BoolVal(True)


@dataclass(frozen=True)
class Cell:
    value: z3.ArithRef
    null: z3.BoolRef
    finite: z3.BoolRef = _FINITE

    @classmethod
    def symbolic(cls, name: str) -> "Cell":
        return cls(z3.Real(name), z3.Bool(f"{name}_null"), z3.Bool(f"{name}_finite"))


def real(value: Fraction | int) -> z3.RatNumRef:
    return z3.RealVal(str(Fraction(value)))


def literal(value: Fraction | int | None) -> Cell:
    return Cell(real(0 if value is None else value), z3.BoolVal(value is None))


def gamma(domain: Domain, cell: Cell) -> z3.BoolRef:
    """Interpret bounds over non-NULL cells and successful native evaluation."""
    if domain.only_null:
        return z3.And(cell.null, cell.finite) if domain.finite else cell.null
    null_allowed = z3.Or(z3.BoolVal(domain.nullable), z3.Not(cell.null))
    if not domain.finite:
        return null_allowed
    bounds = []
    if domain.low is not None:
        endpoint = real(domain.low)
        bounds.append(cell.value > endpoint if domain.low_open else cell.value >= endpoint)
    if domain.high is not None:
        endpoint = real(domain.high)
        bounds.append(cell.value < endpoint if domain.high_open else cell.value <= endpoint)
    if domain.nonzero:
        bounds.append(cell.value != 0)
    return z3.And(cell.finite, null_allowed, z3.Implies(z3.Not(cell.null), z3.And(*bounds)))


def is_true(cell: Cell) -> z3.BoolRef:
    return z3.And(z3.Not(cell.null), cell.value == 1)


def is_false(cell: Cell) -> z3.BoolRef:
    return z3.And(z3.Not(cell.null), cell.value == 0)


def boolean(value: z3.BoolRef, null: z3.BoolRef, finite: z3.BoolRef = _FINITE) -> Cell:
    return Cell(z3.If(value, real(1), real(0)), null, finite)


def negate(arg: Cell) -> Cell:
    return boolean(is_false(arg), arg.null, arg.finite)


def logic(left: Cell, right: Cell, *, conjunction: bool) -> Cell:
    """SQL 3VL plus the owning evaluator's left-to-right RHS demand."""
    if conjunction:
        yes, no = z3.And(is_true(left), is_true(right)), z3.Or(is_false(left), is_false(right))
        short_circuit = is_false(left)
    else:
        yes, no = z3.Or(is_true(left), is_true(right)), z3.And(is_false(left), is_false(right))
        short_circuit = is_true(left)
    return boolean(
        yes, z3.Not(z3.Or(yes, no)), z3.And(left.finite, z3.Or(short_circuit, right.finite))
    )


def choose(condition: Cell, yes: Cell, no: Cell) -> Cell:
    selected = is_true(condition)
    return Cell(
        z3.If(selected, yes.value, no.value),
        z3.If(selected, yes.null, no.null),
        z3.And(condition.finite, z3.If(selected, yes.finite, no.finite)),
    )


def coalesce(left: Cell, right: Cell) -> Cell:
    return choose(boolean(left.null, z3.BoolVal(False), left.finite), right, left)


def extrema(left: Cell, right: Cell, *, greatest: bool) -> Cell:
    useful = left.value >= right.value if greatest else left.value <= right.value
    take_left = z3.Or(right.null, z3.And(z3.Not(left.null), useful))
    return Cell(
        z3.If(take_left, left.value, right.value),
        z3.And(left.null, right.null),
        z3.And(left.finite, right.finite),
    )


def clip(arg: Cell, lower: Cell, upper: Cell) -> Cell:
    low = z3.If(z3.And(z3.Not(lower.null), arg.value < lower.value), lower.value, arg.value)
    high = z3.If(z3.And(z3.Not(upper.null), low > upper.value), upper.value, low)
    return Cell(high, arg.null, z3.And(arg.finite, lower.finite, upper.finite))


def strict_result(left: Cell, right: Cell, value: z3.ArithRef) -> Cell:
    return Cell(value, z3.Or(left.null, right.null), z3.And(left.finite, right.finite))


def divide(left: Cell, right: Cell) -> Cell:
    result = strict_result(left, right, left.value / right.value)
    return replace(result, finite=z3.And(result.finite, z3.Or(result.null, right.value != 0)))


def check(
    claim: str,
    inputs: tuple[z3.BoolRef, ...],
    certificate: z3.BoolRef,
    *,
    logic_name: str = "QF_NRA",
) -> None:
    solver = z3.SolverFor(logic_name)
    solver.set(timeout=5_000)
    solver.add(*inputs)
    assert solver.check() == z3.sat, f"{claim}: input contract has no witness"
    solver.add(z3.Not(certificate))
    status = solver.check()
    if status == z3.sat:
        raise AssertionError(f"{claim} counterexample:\n{solver.model()}")
    assert status == z3.unsat, f"{claim}: {status} ({solver.reason_unknown()})"
    print(f"{claim}: {status}")
