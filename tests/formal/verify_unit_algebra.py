"""Prove the production cancellation step and check its unit algebra callers."""

import ast
import inspect
from itertools import product
from textwrap import dedent

from marivo.semantic.unit_algebra import (
    _cancel_factor_counts,
    divide_unit_states,
    linear_unit,
    linear_units_conflict,
    multiply_unit_states,
    render_unit,
    unit_state,
)
from tests.formal._smt import prove_unsat


def _integer_expression(node: ast.expr, locals_: dict[str, str]) -> str:
    match node:
        case ast.Name(id=name) if name in locals_:
            return locals_[name]
        case ast.Constant(value=value) if type(value) is int and value >= 0:
            return str(value)
        case ast.BinOp(left=left, op=ast.Sub(), right=right):
            return f"(- {_integer_expression(left, locals_)} {_integer_expression(right, locals_)})"
        case ast.Call(func=ast.Name(id="min"), args=[left, right], keywords=[]):
            lhs = _integer_expression(left, locals_)
            rhs = _integer_expression(right, locals_)
            return f"(ite (<= {lhs} {rhs}) {lhs} {rhs})"
        case _:
            raise AssertionError(f"Unsupported cancellation expression: {ast.dump(node)}")


def _cancel_expressions() -> tuple[str, str]:
    """Translate the imported Python function, rejecting anything outside the proof fragment."""
    module = ast.parse(dedent(inspect.getsource(_cancel_factor_counts)))
    (function,) = module.body
    assert isinstance(function, ast.FunctionDef)
    args = function.args
    assert not (
        function.decorator_list
        or args.posonlyargs
        or args.defaults
        or args.kwonlyargs
        or args.vararg
        or args.kwarg
    )
    numerator, denominator = args.args
    assert inspect.getclosurevars(_cancel_factor_counts).globals.get("min", min) is min
    locals_ = {numerator.arg: "n", denominator.arg: "d"}
    assert "min" not in locals_, "Cancellation parameters must not shadow min"
    *assignments, final = function.body
    for statement in assignments:
        match statement:
            case ast.Assign(targets=[ast.Name(id=name)], value=value) if name != "min":
                locals_[name] = _integer_expression(value, locals_)
            case _:
                raise AssertionError(f"Unsupported cancellation statement: {ast.dump(statement)}")
    match final:
        case ast.Return(value=ast.Tuple(elts=[left, right])):
            return _integer_expression(left, locals_), _integer_expression(right, locals_)
        case _:
            raise AssertionError(f"Expected two cancellation results: {ast.dump(final)}")


def test_production_factor_cancellation_for_all_nonnegative_integer_counts() -> None:
    numerator, denominator = _cancel_expressions()
    query = f"""(set-logic QF_LIA)
(declare-const n Int)
(declare-const d Int)
(define-fun reduced_n () Int {numerator})
(define-fun reduced_d () Int {denominator})
(assert (and (>= n 0) (>= d 0)))
(assert (or (< reduced_n 0)
            (< reduced_d 0)
            (and (> reduced_n 0) (> reduced_d 0))
            (distinct (- reduced_n reduced_d) (- n d))))
"""
    prove_unsat(
        query, claim="Production cancellation", witness=("n", "d", "reduced_n", "reduced_d")
    )


_ATOMS = ("USD", "s")


def _render_exponents(first: int, second: int) -> str:
    """Render the independent signed-exponent oracle in ASCII atom order."""
    exponents = (first, second)
    top = ".".join(atom for atom, exp in zip(_ATOMS, exponents, strict=True) for _ in range(exp))
    bottom = ".".join(
        atom for atom, exp in zip(_ATOMS, exponents, strict=True) for _ in range(-exp)
    )
    return f"{top or '1'}/{bottom}" if bottom else top or "1"


def _raw_product(first: int, second: int) -> str:
    return ".".join(("s",) * second + ("USD",) * first) or "1"


def test_factorized_unit_operations_against_signed_exponents() -> None:
    cases = []
    for n0, n1, d0, d1 in product(range(3), repeat=4):
        raw = f"{_raw_product(n0, n1)}/{_raw_product(d0, d1)}"
        exponents = (n0 - d0, n1 - d1)
        expected = _render_exponents(*exponents)
        state = unit_state(raw)
        assert render_unit(state) == expected, (raw, expected, state)
        assert unit_state(expected) == state, (raw, expected, state)
        cases.append((raw, exponents, state))

    for (left, (a, b), lhs), (right, (c, d), rhs) in product(cases, repeat=2):
        operands = (left, right)
        assert render_unit(multiply_unit_states(lhs, rhs)) == _render_exponents(a + c, b + d), (
            "multiply",
            operands,
        )
        assert render_unit(divide_unit_states(lhs, rhs)) == _render_exponents(a - c, b - d), (
            "divide",
            operands,
        )
        compatible = (a, b) == (c, d)
        assert linear_unit(operands) == (_render_exponents(a, b) if compatible else None), operands
        assert linear_units_conflict(operands) == (not compatible), operands


def test_opaque_and_unknown_unit_states() -> None:
    units = ("1", "USD/(s)", "USD/(s.s)", None)
    for left, right in product(units, repeat=2):
        lhs, rhs = unit_state(left), unit_state(right)
        assert render_unit(lhs) == left
        assert render_unit(rhs) == right
        expected_product = "1" if left == right == "1" else None
        assert render_unit(multiply_unit_states(lhs, rhs)) == expected_product, (left, right)
        assert render_unit(divide_unit_states(lhs, rhs)) == expected_product, (left, right)
        assert linear_unit((left, right)) == (left if left == right else None), (left, right)
        assert linear_units_conflict((left, right)) == (
            left is not None and right is not None and left != right
        ), (left, right)
