"""Prove the production cancellation step and check its unit algebra callers."""

import ast
import inspect
from collections.abc import Callable
from decimal import Decimal
from itertools import product
from textwrap import dedent

import pytest

import marivo.semantic as ms
from marivo._definition.types import ExactRef, SumAll, SumAllExcept, SumAxes, SumNone, SumPermission
from marivo._kernel.refs import ref
from marivo.semantic._deferred_semantics import consume_row
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.expressions.operations import _intersect
from marivo.semantic.reader import SemanticProject
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
from tests.shared_fixtures import catalog_value


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


_AXES = tuple(
    ExactRef(ref.dimension(f"proof.facts.{name}"), "v1")
    for name in ("region", "product", "customer")
)
# An axis absent from all finite lists distinguishes explicit permission from
# its complement, including the complement excluding every named axis.
_UNIVERSE = (*_AXES, ExactRef(ref.dimension("proof.facts.other"), "v1"))


def _extension(permission: SumPermission, axes: tuple[ExactRef, ...] = _UNIVERSE) -> frozenset[int]:
    if isinstance(permission, SumNone):
        return frozenset()
    if isinstance(permission, SumAll):
        return frozenset(range(len(axes)))
    members = {axis.ref for axis in permission.axes}
    return frozenset(
        index
        for index, axis in enumerate(axes)
        if (axis.ref in members) == isinstance(permission, SumAxes)
    )


def test_production_sum_permission_meet_for_all_finite_axis_representations() -> None:
    subsets = [
        tuple(axis for axis, present in zip(_AXES, bits, strict=True) if present)
        for bits in product((False, True), repeat=len(_AXES))
    ]
    permissions: list[SumPermission] = [SumNone(), SumAll()]
    permissions.extend(SumAxes(items) for items in subsets)
    permissions.extend(SumAllExcept(items) for items in subsets if items)
    same_axis_new_revision = ExactRef(_AXES[0].ref, "v2")
    permissions += [SumAxes((same_axis_new_revision,)), SumAllExcept((same_axis_new_revision,))]
    for left, right in product(permissions, repeat=2):
        a, b = _extension(left), _extension(right)
        lr = _extension(_intersect(left, right))
        assert lr == a & b, (left, right, lr, a & b)
        assert lr == _extension(_intersect(right, left))
        assert _extension(_intersect(left, left)) == a
        assert _extension(_intersect(left, SumAll())) == a
        assert not _extension(_intersect(left, SumNone()))
    for left, right, third in product(permissions, repeat=3):
        assert _extension(_intersect(_intersect(left, right), third)) == _extension(
            _intersect(left, _intersect(right, third))
        ), (left, right, third)


def test_permission_meet_is_greatest_lower_bound_at_an_arbitrary_axis() -> None:
    query = """(set-logic QF_UF)
(declare-const include_a Bool)
(declare-const include_b Bool)
(declare-const eligible_lower_bound Bool)
(declare-const exclude_a Bool)
(declare-const exclude_b Bool)
(define-fun merged () Bool (and include_a include_b))
(assert (or
  (and merged (not include_a))
  (and merged (not include_b))
  (and eligible_lower_bound
       (=> eligible_lower_bound include_a)
       (=> eligible_lower_bound include_b)
       (not merged))
  (distinct (not (or exclude_a exclude_b)) (and (not exclude_a) (not exclude_b)))))
"""
    prove_unsat(
        query,
        claim="Membership-wise meet including two exclusion lists",
        witness=("include_a", "include_b", "eligible_lower_bound", "exclude_a", "exclude_b"),
    )
    print("Sum-permission meet: unsat")


def test_exact_rescale_distributes_over_additive_mass() -> None:
    query = """(set-logic QF_NRA)
(declare-const a Real)
(declare-const b Real)
(declare-const d Real)
(assert (> d 0.0))
(assert (distinct (/ (+ a b) d) (+ (/ a d) (/ b d))))
"""
    prove_unsat(query, claim="Exact rescale preserves addition", witness=("a", "b", "d"))
    print("Exact rescale additive morphism: unsat")


_PERMISSION_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['id'])
region = ms.dimension_column(name='region', entity=facts, column='region')
product = ms.dimension_column(name='product', entity=facts, column='product')
amount = ms.measure_column(name='amount', entity=facts, column='amount', unit='USD', additivity=ms.additive_except(region))
charge = ms.measure_column(name='charge', entity=facts, column='charge', unit='USD', additivity=ms.additive_except(product))
other = ms.measure_column(name='other', entity=facts, column='charge', unit='EUR', additivity=ms.additive_all())
"""


def test_bound_row_arithmetic_and_rescale_preserve_exact_permission_contracts(
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    project = semantic_project_factory(
        {
            "proof/_domain.py": "import marivo.semantic as ms\nms.domain(name='proof', owner='Analytics')\n",
            "proof/model.py": _PERMISSION_MODEL,
        }
    )
    catalog = project.catalog()
    amount = catalog_value(catalog, "proof.facts.amount", kind="measure")
    charge = catalog_value(catalog, "proof.facts.charge", kind="measure")
    other = catalog_value(catalog, "proof.facts.other", kind="measure")
    source = consume_row(amount)
    for expression in (amount + charge, amount - charge):
        facts = consume_row(expression)
        assert facts.unit == "USD"
        assert _extension(facts.permission) == frozenset({2, 3})
    for expression in (amount * 2, 2 * amount, amount / Decimal(8), -amount):
        facts = consume_row(expression)
        assert facts.unit == "USD" and facts.permission == source.permission
    for expression in (amount * charge, amount / charge, 2 / amount):
        assert consume_row(expression).permission == SumNone()
    with pytest.raises(SemanticExpressionError) as error:
        consume_row(amount + other)
    assert error.value.kind == ErrorKind.INCOMMENSURABLE_LINEAR_UNITS.value
    d = Decimal(8)
    whole = consume_row(ms.rescale(amount + charge, divisor=d, unit="kUSD"))
    a, b = ms.rescale(amount, divisor=d, unit="kUSD"), ms.rescale(charge, divisor=d, unit="kUSD")
    separate = consume_row(a + b)
    assert whole.unit == separate.unit == "kUSD"
    assert whole.root == separate.root == source.root
    assert whole.coordinates == separate.coordinates == source.coordinates
    assert whole.permission == separate.permission == consume_row(amount + charge).permission
