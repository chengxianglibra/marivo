"""Check production Attribute certificates against independent symbolic counterexamples.

Each case fixes the input abstract domains and checks every represented concrete
value symbolically. Carrier tests separately cover real-to-IEEE rounding and the
lattice bounds. These are bounded obligations, not a proof for arbitrary program
size, domain endpoints, backend kernels or transcendental algorithms.
"""

import math
import operator
import sys
from collections.abc import Callable, Mapping
from decimal import Decimal, Inexact, localcontext
from fractions import Fraction
from itertools import product

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pytest
import z3  # type: ignore[import-not-found, import-untyped, unused-ignore]  # Optional untyped solver.

from marivo.analysis.methods._ibis_numeric import evaluate_numeric
from marivo.analysis.methods.attribute_game_domain import (
    Domain,
    DomainProofError,
    _assume,
    _carrier_domain,
    evaluate_domain,
    prove_domain,
)
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.numeric_operations import NumericValue, numeric_binary
from tests.formal._attribute_domain import (
    Cell,
    boolean,
    check,
    choose,
    clip,
    coalesce,
    divide,
    extrema,
    gamma,
    is_false,
    is_true,
    literal,
    logic,
    negate,
    real,
    strict_result,
)

_NUMBERS = ibis.table({"a": "int64", "b": "int64", "c": "int64"}, name="numbers")
_FLAGS = ibis.table({"p": "boolean", "q": "boolean"}, name="flags")
_FLOATS = ibis.table({"x": "float64", "y": "float32", "i": "int64"}, name="floats")
_DECIMALS = ibis.table({"a": "decimal(38,2)", "b": "decimal(38,2)"}, name="decimals")
_A, _B, _C, _P, _Q, _X, _Y, _I = map(Cell.symbolic, ("a", "b", "c", "p", "q", "x", "y", "i"))
_CELLS = dict(zip("abcpqxyi", (_A, _B, _C, _P, _Q, _X, _Y, _I), strict=True))
_COMPARE = {
    "equals": operator.eq,
    "not-equals": operator.ne,
    "greater": operator.gt,
    "greater-equal": operator.ge,
    "less": operator.lt,
    "less-equal": operator.le,
}


def _interval(low: int | Fraction, high: int | Fraction, *, nullable: bool = False) -> Domain:
    return Domain(Fraction(low), Fraction(high), nullable=nullable)


def _input_constraints(fields: dict[str, Domain]) -> tuple[z3.BoolRef, ...]:
    return tuple(gamma(domain, _CELLS[name]) for name, domain in fields.items())


def _verify(
    label: str,
    expr: ir.Value,
    fields: dict[str, Domain],
    reference: Cell,
    *physical: z3.BoolRef,
) -> None:
    output = evaluate_domain(expr.op(), fields)
    assert output.finite, f"{label} lost a valid finite certificate: {output}"
    check(label, (*_input_constraints(fields), *physical), gamma(output, reference))


def _relation(
    op: Callable[[z3.ArithRef, z3.ArithRef], z3.BoolRef], left: Cell, right: Cell
) -> Cell:
    return strict_result(left, right, boolean(op(left.value, right.value), left.null).value)


@pytest.mark.parametrize("value", [None, -17, 0, 23, Fraction(3, 2)], ids=str)
def test_literals_and_aliases(value: int | Fraction | None) -> None:
    raw = float(value) if isinstance(value, Fraction) else value
    _verify(f"Literal {value}", ibis.literal(raw), {}, literal(value))
    _verify(f"Alias {value}", ibis.literal(raw).name("alias"), {}, literal(value))


@pytest.mark.parametrize(
    ("name", "op"), [("add", operator.add), ("subtract", operator.sub), ("multiply", operator.mul)]
)
@pytest.mark.parametrize("nullable", [False, True])
def test_binary_arithmetic(
    name: str, op: Callable[[ir.Value, ir.Value], ir.Value], nullable: bool
) -> None:
    for low, high in ((-7, -1), (-3, 5), (0, 0), (1, 5)):
        fields = {
            "a": _interval(low, high, nullable=nullable),
            "b": Domain(Fraction(-4), Fraction(2), nullable, low_open=True, high_open=True),
        }
        expected = strict_result(_A, _B, op(_A.value, _B.value))
        _verify(
            f"{name} [{low}, {high}] nullable={nullable}",
            op(_NUMBERS.a, _NUMBERS.b),
            fields,
            expected,
        )


def test_divide_rejects_a_physically_reachable_zero() -> None:
    fields = {"a": _interval(1, 7), "b": _interval(-2, 2)}
    reference = divide(_A, _B)
    try:
        output = evaluate_domain((_NUMBERS.a / _NUMBERS.b).op(), fields)
    except DomainProofError:
        accepted_value_is_sound = z3.BoolVal(True)
    else:
        accepted_value_is_sound = z3.And(z3.BoolVal(output.finite), gamma(output, reference))
    # If production accepts this interval, the concrete zero must still satisfy
    # its certificate. The negation surfaces the reachable denominator witness.
    check(
        "Divide zero guard", (*_input_constraints(fields), _B.value == 0), accepted_value_is_sound
    )


@pytest.mark.parametrize("dtype", ["int64", "decimal(12,2)", "cast-int64", "cast-decimal(12,2)"])
def test_divide_quantum_near_zero(dtype: str) -> None:
    physical_dtype = dtype.removeprefix("cast-")
    source = ibis.table({"a": "float64", "b": physical_dtype}, name="divisor")
    operand = source.b.cast("float64") if dtype.startswith("cast-") else source.b
    fields = {
        "a": _interval(-7, 4, nullable=True),
        "b": Domain(Fraction(-2), Fraction(2), True, nonzero=True),
    }
    minimum = real(Fraction(1, 100) if physical_dtype.startswith("decimal") else 1)
    if physical_dtype.startswith("decimal"):
        # Float64 conversion of a decimal quantum remains strictly above this
        # lower bound, even if conversion rounds toward zero.
        minimum /= 2
    _verify(
        f"Divide uses nonzero quantum {dtype}",
        source.a / operand,
        fields,
        divide(_A, _B),
        z3.Implies(z3.Not(_B.null), z3.Abs(_B.value) >= minimum),
    )


@pytest.mark.parametrize("lower,upper", [(1, 9), (-9, -1), (3, 3)])
def test_divide_on_one_side_of_zero(lower: int, upper: int) -> None:
    fields = {"a": _interval(-7, 4, nullable=True), "b": _interval(lower, upper, nullable=True)}
    _verify(f"Divide [{lower}, {upper}]", _NUMBERS.a / _NUMBERS.b, fields, divide(_A, _B))


def test_floating_nonzero_without_a_separation_bound_can_overflow() -> None:
    table = ibis.table({"a": "float64", "b": "float64"})
    fields = {"a": _interval(1, 1), "b": Domain(Fraction(-1), Fraction(1), nonzero=True)}
    value = evaluate_domain((table.a / table.b).op(), fields)
    # The smallest positive Float64 is nonzero but 1 / smallest is overflow.
    smallest = real(Fraction(1, 2**1074))
    physical = (*_input_constraints(fields), _B.value == smallest)
    failed_at_witness = z3.And(
        z3.BoolVal(not value.finite), _A.value / _B.value > real(Fraction(sys.float_info.max))
    )
    check("Divide nonzero float without a finite enclosure", physical, failed_at_witness)


@pytest.mark.parametrize("name", ["negate", "absolute"])
@pytest.mark.parametrize("bounds", [(-7, -1), (-3, 8), (0, 0), (3, 8)])
def test_signed_operations(name: str, bounds: tuple[int, int]) -> None:
    fields = {"a": _interval(*bounds, nullable=True)}
    expr = -_NUMBERS.a if name == "negate" else _NUMBERS.a.abs()
    value = -_A.value if name == "negate" else z3.Abs(_A.value)
    _verify(f"{name} {bounds}", expr, fields, Cell(value, _A.null))


@pytest.mark.parametrize("name", list(_COMPARE))
def test_comparison_domains(name: str) -> None:
    op = _COMPARE[name]
    families = [
        (_interval(-4, 2, nullable=True), _interval(-3, 4)),
        (_interval(2, 5), _interval(-1, 1, nullable=True)),
        (_interval(0, 1), _interval(1, 2)),
        (Domain(Fraction(0), Fraction(1), high_open=True), _interval(1, 2, nullable=True)),
        (Domain(Fraction(1), Fraction(3), low_open=True), _interval(-1, 1)),
        (Domain(Fraction(-2), Fraction(3), nonzero=True), _interval(0, 0)),
        (_interval(2, 2), _interval(2, 2)),
        (Domain.null(), _interval(0, 2)),
    ]
    for index, (a, b) in enumerate(families):
        _verify(
            f"{name} {index}", op(_NUMBERS.a, _NUMBERS.b), {"a": a, "b": b}, _relation(op, _A, _B)
        )


@pytest.mark.parametrize("name", list(_COMPARE))
@pytest.mark.parametrize(
    "outcomes",
    [frozenset([True]), frozenset([False]), frozenset([None]), frozenset([False, None])],
    ids=["true", "false", "null", "not-true"],
)
def test_assume_comparison_refines_only_feasible_sql_outcomes(
    name: str, outcomes: frozenset[bool | None]
) -> None:
    op = _COMPARE[name]
    condition = op(_NUMBERS.a, ibis.literal(0))
    concrete = _relation(op, _A, literal(0))
    _verify_assumption(
        name,
        condition,
        outcomes,
        {"a": _interval(-3, 2, nullable=True)},
        {condition.op(): concrete, _NUMBERS.a.op(): _A},
    )


def _verify_assumption(
    label: str,
    condition: ir.Value,
    outcomes: frozenset[bool | None],
    fields: dict[str, Domain],
    concrete: dict[ops.Value, Cell],
) -> None:
    actual = _assume(condition.op(), outcomes, {}, fields)
    result = concrete[condition.op()]
    branch = z3.Or(
        *(
            result.null if outcome is None else is_true(result) if outcome else is_false(result)
            for outcome in outcomes
        )
    )
    check(
        f"Assume {label} {sorted(map(str, outcomes))}",
        (*_input_constraints(fields), branch),
        z3.And(*(gamma(domain, concrete[node]) for node, domain in actual.items())),
    )


@pytest.mark.parametrize("conjunction", [False, True], ids=["or", "and"])
@pytest.mark.parametrize("left,right", product((False, True, None), repeat=2))
def test_logical_truth_table(conjunction: bool, left: bool | None, right: bool | None) -> None:
    fields = {"p": Domain.constant(left), "q": Domain.constant(right)}
    expr = _FLAGS.p & _FLAGS.q if conjunction else _FLAGS.p | _FLAGS.q
    reference = logic(_P, _Q, conjunction=conjunction)
    _verify(f"{type(expr.op()).__name__} {left}, {right}", expr, fields, reference)


def test_not_and_comparison_results_can_remain_null() -> None:
    condition = _NUMBERS.a > 0
    concrete = _relation(operator.gt, _A, literal(0))
    fields = {"a": _interval(-2, 1, nullable=True)}
    _verify("Not SQL NULL", ~condition, fields, negate(concrete))
    _verify_assumption(
        "Not comparison",
        ~condition,
        frozenset([False, None]),
        fields,
        {(~condition).op(): negate(concrete), condition.op(): concrete, _NUMBERS.a.op(): _A},
    )


@pytest.mark.parametrize(
    "case", ["and-true", "or-not-true", "coalesce-false", "coalesce-true", "is-null", "not-null"]
)
def test_assume_propagates_compound_predicates(case: str) -> None:
    p, q = _NUMBERS.a > 0, _NUMBERS.b == 0
    left, right = _relation(operator.gt, _A, literal(0)), _relation(operator.eq, _B, literal(0))
    fields = {"a": _interval(-2, 2, nullable=True), "b": _interval(-2, 2, nullable=True)}
    outcomes: frozenset[bool | None]
    if case == "and-true":
        expr, concrete, outcomes = p & q, logic(left, right, conjunction=True), frozenset([True])
    elif case == "or-not-true":
        expr, concrete, outcomes = (
            p | q,
            logic(left, right, conjunction=False),
            frozenset([False, None]),
        )
    elif case == "coalesce-false":
        expr, concrete, outcomes = (
            p.fill_null(False),
            coalesce(left, literal(0)),
            frozenset([False]),
        )
    elif case == "coalesce-true":
        expr, concrete, outcomes = p.fill_null(False), coalesce(left, literal(0)), frozenset([True])
    else:
        expr = _NUMBERS.a.isnull() if case == "is-null" else _NUMBERS.a.notnull()
        concrete = boolean(_A.null if case == "is-null" else z3.Not(_A.null), z3.BoolVal(False))
        outcomes = frozenset([True])
    _verify_assumption(
        case,
        expr,
        outcomes,
        fields,
        {
            expr.op(): concrete,
            p.op(): left,
            q.op(): right,
            _NUMBERS.a.op(): _A,
            _NUMBERS.b.op(): _B,
        },
    )


@pytest.mark.parametrize("shape", ["equal-zero", "positive", "searched", "coalesced", "nested-not"])
def test_conditional_false_and_null_have_the_correct_demand(shape: str) -> None:
    a = _NUMBERS.a
    equal = _relation(operator.eq, _A, literal(0))
    positive = _relation(operator.gt, _A, literal(0))
    fields = {"a": _interval(0, 2, nullable=True)}
    if shape == "equal-zero":
        expr, reference = (
            (a == 0).ifelse(1, 10 / a),
            choose(equal, literal(1), divide(literal(10), _A)),
        )
        fields = {"a": _interval(1, 2, nullable=True)}
    elif shape == "positive":
        expr, reference = (a > 0).ifelse(1, a), choose(positive, literal(1), _A)
    elif shape == "searched":
        expr = ibis.cases((a > 0, 1), (a < 0, 2), else_=a)
        reference = choose(
            positive, literal(1), choose(_relation(operator.lt, _A, literal(0)), literal(2), _A)
        )
        fields = {"a": _interval(-2, 2, nullable=True)}
    elif shape == "coalesced":
        expr, reference = ibis.coalesce(a, 3), coalesce(_A, literal(3))
    else:
        expr, reference = (~(a > 0)).ifelse(2, a), choose(negate(positive), literal(2), _A)
    _verify(shape, expr, fields, reference)


def test_conditional_proof_keeps_demand_on_every_nested_path() -> None:
    a, b = _NUMBERS.a, _NUMBERS.b
    must_not_visit = 10 / b
    fields = {"a": _interval(-3, 2, nullable=True), "b": _interval(0, 0)}
    # Both the CASE arm selection and inference of the first Coalesce arm
    # must short circuit its unreachable third arm.
    first = a > 0
    condition = ibis.coalesce(first, False, must_not_visit > 0)
    expression = condition.ifelse(a, 0)
    expected = choose(coalesce(_relation(operator.gt, _A, literal(0)), literal(0)), _A, literal(0))
    _verify("Coalesce guard short circuit", expression, fields, expected)
    assert prove_domain(expression.op(), (), fields)
    for expression in (
        ibis.literal(False) & (must_not_visit > 0),
        ibis.literal(True) | (must_not_visit > 0),
    ):
        _verify(
            "Undemanded logical right arm",
            expression,
            fields,
            literal(isinstance(expression.op(), ops.Or)),
        )
    for expression in (
        ibis.null().cast("boolean") & (must_not_visit > 0),
        ibis.null().cast("boolean") | (must_not_visit > 0),
    ):
        with pytest.raises(DomainProofError, match="denominator"):
            evaluate_domain(expression.op(), fields)


@pytest.mark.parametrize("name", ["is-null", "not-null", "is-nan", "is-inf"])
def test_null_and_finite_tests(name: str) -> None:
    fields = {"x": _interval(-7, 2, nullable=True)}
    x = _FLOATS.x
    if name == "is-null":
        expr, expected = x.isnull(), boolean(_X.null, z3.BoolVal(False))
    elif name == "not-null":
        expr, expected = x.notnull(), boolean(z3.Not(_X.null), z3.BoolVal(False))
    else:
        expr, expected = (x.isnan() if name == "is-nan" else x.isinf()), literal(0)
    _verify(name, expr, fields, expected)


@pytest.mark.parametrize("name", ["nullif", "least", "greatest", "clip"])
@pytest.mark.parametrize("shape", ["nullable-bound", "reversed", "disjoint", "null-argument"])
def test_null_and_extrema_transfers(name: str, shape: str) -> None:
    a, b, c = _NUMBERS.a, _NUMBERS.b, _NUMBERS.c
    fields = {
        "a": _interval(-10, 1, nullable=True),
        "b": _interval(-2, 5, nullable=True),
        "c": _interval(1, 5),
    }
    if shape == "reversed":
        fields.update(b=_interval(5, 6), c=_interval(-5, -3))
    elif shape == "disjoint":
        fields.update(b=_interval(5, 6), c=_interval(10, 15))
    elif shape == "null-argument":
        fields.update(a=Domain.null())
    if name == "nullif":
        expr = a.nullif(b)
        reference = choose(_relation(operator.eq, _A, _B), literal(None), _A)
    elif name == "least":
        expr, reference = (
            ibis.least(a, b, c),
            extrema(extrema(_A, _B, greatest=False), _C, greatest=False),
        )
    elif name == "greatest":
        expr, reference = (
            ibis.greatest(a, b, c),
            extrema(extrema(_A, _B, greatest=True), _C, greatest=True),
        )
    else:
        expr, reference = a.clip(b, c), clip(_A, _B, _C)
    _verify(f"{name} {shape}", expr, fields, reference)


def test_strict_right_input_is_not_lost_when_the_left_is_null() -> None:
    x, y = _FLOATS.x, _FLOATS.y
    fields = {"x": Domain.null(), "y": _interval(710, 712)}
    for expression in (x + y.exp(), x.nullif(y.exp()), x.clip(y.exp(), None), x.log(y.exp())):
        assert not evaluate_domain(expression.op(), fields).finite, type(expression.op()).__name__


def test_clip_bound_must_fit_the_selected_native_carrier() -> None:
    x, i = _FLOATS.x, _FLOATS.i
    bound = Fraction(5, 2)
    fields = {"x": _interval(bound, bound), "i": _interval(1, 1)}
    expression = i.clip(x, None)
    assert not evaluate_domain(expression.op(), fields).finite
    fields["i"] = _interval(4, 4)
    _verify("Clip does not cast an unused bound", expression, fields, _I)


@pytest.mark.parametrize("digits", [-3, -2, 0, 2])
def test_round_decimal_place_enclosure(digits: int) -> None:
    fields = {"x": _interval(-203, 178, nullable=True)}
    concrete = Cell(z3.Real("rounded"), _X.null)
    half_step = Fraction(10) ** -digits / 2
    # Python round uses nearest with ties-to-even. For the positive precision
    # fixture, a small binary representation error may follow decimal rounding.
    error = half_step + (Fraction(1, 2**40) if digits > 0 else 0)
    _verify(
        f"Round digits={digits}",
        _FLOATS.x.round(digits),
        fields,
        concrete,
        z3.Abs(concrete.value - _X.value) <= real(error),
    )


def test_square_root_uses_a_nonnegative_domain() -> None:
    fields = {"x": _interval(0, 123, nullable=True)}
    root = z3.Real("root")
    _verify(
        "Sqrt", _FLOATS.x.sqrt(), fields, Cell(root, _X.null), root * root == _X.value, root >= 0
    )
    with pytest.raises(DomainProofError, match="nonnegative"):
        evaluate_domain(_FLOATS.x.sqrt().op(), {"x": _interval(-1, 4)})
    inexact_int = 2**53 + 1
    assert not evaluate_domain(
        _NUMBERS.a.sqrt().op(), {"a": _interval(inexact_int, inexact_int)}
    ).finite


@pytest.mark.parametrize("high", [-2, 0, 5, 709])
def test_exp_uses_an_independent_rational_upper_bound(high: int) -> None:
    exponential = z3.Real("exponential")
    fields = {"x": _interval(high - 1, high, nullable=True)}
    # exp(x) <= 1 for x <= 0. For positive x use e < 2.719 and
    # an integral upper endpoint. This assumes monotone, finite libm exp.
    limit = real(Fraction(2719, 1000) ** max(0, high))
    _verify(
        "Exp",
        _FLOATS.x.exp(),
        fields,
        Cell(exponential, _X.null),
        exponential >= 0,
        exponential <= limit,
    )


@pytest.mark.parametrize(
    "operator_name,max_abs", [("ln", 750), ("log2", 1074), ("log10", 324), ("log", 750)]
)
def test_log_family_uses_bounds_for_positive_finite_float64(
    operator_name: str, max_abs: int
) -> None:
    x = _FLOATS.x
    expr = {"ln": x.ln(), "log2": x.log2(), "log10": x.log10(), "log": x.log()}[operator_name]
    fields = {"x": _interval(Fraction(1, 2**1074), Fraction(sys.float_info.max), nullable=True)}
    result = z3.Real("log_result")
    # Independent analytic bounds for the positive Float64 range are
    # -750 < ln < 710, -1074 <= log2 <= 1024, and |log10| < 324.
    _verify(operator_name, expr, fields, Cell(result, _X.null), z3.Abs(result) <= max_abs)


def test_log_with_a_nullable_base() -> None:
    fields = {"x": _interval(1, 7, nullable=True), "y": _interval(2, 4, nullable=True)}
    ln_argument, ln_base = z3.Reals("ln_argument ln_base")
    reference = strict_result(_X, _Y, ln_argument / ln_base)
    _verify(
        "Log with explicit base",
        _FLOATS.x.log(_FLOATS.y),
        fields,
        reference,
        ln_argument >= 0,
        ln_argument <= 2,
        ln_base >= real(Fraction(2, 3)),
        ln_base <= real(Fraction(3, 2)),
    )
    assert not evaluate_domain(
        _FLOATS.x.log(_FLOATS.y).op(), {**fields, "y": _interval(0, 2)}
    ).finite


@pytest.mark.parametrize(
    "source,target",
    [
        ("int64", "int8"),
        ("int64", "decimal(10,2)"),
        ("decimal(8,2)", "decimal(12,4)"),
        ("int64", "float64"),
        ("float32", "float64"),
    ],
)
def test_exact_casts_preserve_the_concrete_value(source: str, target: str) -> None:
    table = ibis.table({"a": source})
    fields = {"a": _interval(-21, 43, nullable=True)}
    for expr in (table.a.cast(target), table.a.try_cast(target)):
        _verify(f"{type(expr.op()).__name__} {source}→{target}", expr, fields, _A)


def test_fractional_cast_may_be_undefined() -> None:
    fields = {"x": _interval(Fraction(3, 2), Fraction(3, 2))}
    _verify(
        "TryCast rejected fraction returns NULL", _FLOATS.x.try_cast("int32"), fields, literal(None)
    )
    assert not evaluate_domain(_FLOATS.x.cast("int32").op(), fields).finite


@pytest.mark.parametrize(
    "operation", [operator.add, operator.sub, operator.mul], ids=["add", "subtract", "multiply"]
)
def test_decimal_arithmetic_reads_the_active_precision(
    operation: Callable[[ir.Value, ir.Value], ir.Value],
) -> None:
    # At three significant digits: 12.31 + 1.00 == 13.3,
    # 12.31 - 1.00 == 11.3, and 12.31 * 1.00 == 12.3.
    a, b = Fraction(1231, 100), Fraction(1)
    expected = {
        operator.add: Fraction(133, 10),
        operator.sub: Fraction(113, 10),
        operator.mul: Fraction(123, 10),
    }[operation]
    fields = {"a": _interval(a, a), "b": _interval(b, b, nullable=True)}
    with localcontext() as context:
        context.prec = 3
        _verify(
            "Decimal context rounding",
            operation(_DECIMALS.a, _DECIMALS.b),
            fields,
            strict_result(_A, _B, real(expected)),
        )


def test_float64_binary_promotion_precedes_subtraction() -> None:
    value = 2**53
    fields = {"x": _interval(value, value), "i": _interval(value + 1, value + 1)}
    promoted = _ieee_round(Fraction(value + 1), z3.Float64())
    _verify(
        "Float64 converts the integer operand first",
        _FLOATS.x - _FLOATS.i,
        fields,
        Cell(_X.value - real(promoted), _X.null),
    )
    assert not evaluate_domain((_FLOATS.x != _FLOATS.i).op(), fields).finite


@pytest.mark.parametrize("nested", [False, True])
def test_integer_arithmetic_keeps_the_native_checked_kernel_width(nested: bool) -> None:
    table = ibis.table({"a": "int8", "b": "int8"})
    fields = {"a": _interval(117, 120), "b": _interval(3, 6) if nested else _interval(6, 10)}
    expr = (table.a + table.b) + table.b if nested else table.a + table.b
    exact = _A.value + (2 if nested else 1) * _B.value
    assert expr.type().nbytes > 1  # Ibis metadata is wider than the Arrow kernel.
    output = evaluate_domain(expr.op(), fields)
    check(
        "Int8 arithmetic admits a native overflow witness",
        (*_input_constraints(fields), exact > 127),
        z3.BoolVal(not output.finite),
    )


def test_mixed_integer_kernels_reject_unrepresentable_operand_promotions() -> None:
    table = ibis.table({"a": "uint64", "b": "int64"})
    fields = {"a": _interval(2**63, 2**63), "b": _interval(-1, -1)}
    assert not evaluate_domain((table.a + table.b).op(), fields).finite
    assert not evaluate_domain((table.a == table.b).op(), fields).finite
    signed_mix = ibis.table({"a": "uint8", "b": "int8"})
    fields = {"a": _interval(240, 250), "b": _interval(5, 10)}
    _verify(
        "Mixed UInt8 plus Int8 uses Int16",
        signed_mix.a + signed_mix.b,
        fields,
        strict_result(_A, _B, _A.value + _B.value),
    )


def test_decimal_round_requires_the_native_context_to_fit_the_coefficient() -> None:
    decimal = _DECIMALS.a
    with localcontext() as context:
        context.prec = 3
        point = Fraction(24, 100)
        _verify(
            "Decimal round fits coefficient",
            decimal.round(2),
            {"a": _interval(point, point)},
            literal(point),
        )
        point = Fraction(12212, 100)
        assert not evaluate_domain(decimal.round(2).op(), {"a": _interval(point, point)}).finite


@pytest.mark.parametrize(
    "source",
    [
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "decimal(3,0)",
        "decimal(6,2)",
        "decimal(38,38)",
    ],
)
def test_exact_physical_carriers(source: str) -> None:
    field = ibis.table({"a": source}).a.op()
    if source.startswith("decimal"):
        precision, scale = map(int, source.removeprefix("decimal(").removesuffix(")").split(","))
        step = Fraction(1, 10**scale)
        low, high = -(10**precision - 1) * step, (10**precision - 1) * step
    else:
        signed = not source.startswith("u")
        width = int(source.removeprefix("u").removeprefix("int"))
        step = Fraction(1)
        low = Fraction(-(1 << (width - 1)) if signed else 0)
        high = Fraction((1 << (width - int(signed))) - 1)
    for bounds in ((low, high), (low - step, high), (low, high + step)):
        domain = _interval(*bounds, nullable=True)
        actual = _carrier_domain(field, domain)
        out_of_carrier = z3.And(z3.Not(_A.null), z3.Or(_A.value < real(low), _A.value > real(high)))
        if bounds == (low, high):
            assert actual.finite
            check(
                source,
                (gamma(domain, _A),),
                z3.And(gamma(actual, _A), z3.Not(out_of_carrier)),
                logic_name="QF_LRA",
            )
        else:
            check(
                f"{source} overflow {bounds}",
                (gamma(domain, _A), out_of_carrier),
                z3.BoolVal(not actual.finite),
                logic_name="QF_LRA",
            )


def _ieee_round(value: Fraction, sort: z3.FPSortRef) -> Fraction:
    rounded = z3.simplify(z3.fpToReal(z3.fpRealToFP(z3.RNE(), real(value), sort)))
    assert isinstance(rounded, z3.RatNumRef), f"Expected finite round-to-nearest result for {value}"
    result = rounded.as_fraction()
    assert isinstance(result, Fraction)
    return result


@pytest.mark.parametrize("width,exponent,significand", [(32, 127, 24), (64, 1023, 53)])
def test_ieee_carrier_outward_rounding(width: int, exponent: int, significand: int) -> None:
    field = ibis.table({"a": f"float{width}"}).a
    operation = (field + field).op()
    sort = z3.Float32() if width == 32 else z3.Float64()
    tiny = Fraction(1, 2 ** (exponent + significand - 2))
    ulp_at_one = Fraction(1, 2 ** (significand - 1))
    maximum = Fraction(2 ** (exponent + 1) - 2 ** (exponent - significand + 1))
    for label, bounds in (
        ("zero", (-tiny / 2, tiny / 3)),
        ("subnormal", (-3 * tiny / 4, 3 * tiny / 2)),
        ("halfway", (1 + ulp_at_one / 3, 1 + ulp_at_one / 2)),
        ("binade", (2 - ulp_at_one / 8, 2 + ulp_at_one)),
        ("minimum", (-maximum, -maximum / 2)),
        ("maximum", (maximum / 2, maximum)),
    ):
        # The exact values vary continuously across these intervals. IEEE
        # RNE is monotone, so its independently rounded endpoints determine
        # an enclosing floating-point interval for every interior value.
        domain = _interval(*bounds, nullable=True)
        rounded_lo, rounded_hi = (_ieee_round(value, sort) for value in bounds)
        actual = _carrier_domain(operation, domain)
        assert actual.finite
        check(
            f"Float{width} outward rounding {label}",
            (gamma(_interval(rounded_lo, rounded_hi, nullable=True), _A),),
            gamma(actual, _A),
            logic_name="QF_LRA",
        )
    overflow = _carrier_domain(operation, _interval(maximum, 2 * maximum))
    check(
        f"Float{width} overflow",
        (_A.value > real(maximum), _A.value <= real(2 * maximum)),
        z3.BoolVal(not overflow.finite),
        logic_name="QF_LRA",
    )


def test_rounding_closes_an_open_boundary_that_can_round_to_equality() -> None:
    field = _FLOATS.i
    value = 2**53
    open_lower = Domain(Fraction(value), Fraction(value + 4), low_open=True, nonzero=True)
    cast = field.cast("float64")
    actual = evaluate_domain(cast.op(), {"i": open_lower})
    # The integer just above the open endpoint rounds back onto that endpoint.
    rounded = _ieee_round(Fraction(value + 1), z3.Float64())
    check(
        "Cast closes rounded open endpoint",
        (gamma(open_lower, _I), _I.value == value + 1),
        gamma(actual, literal(rounded)),
    )


def test_arrow_decimal_float64_rounding_occupies_its_own_bound() -> None:
    # Arrow Decimal128-to-Float64 produces the lower neighbor here, instead
    # of the independently computed round-to-nearest conversion.
    point = Fraction(Decimal("-396215078510101368564026337288053238.24"))
    native = Fraction(-3.962150785101013e35)
    nearest = _ieee_round(point, z3.Float64())
    assert native != nearest
    _verify(
        "Decimal Arrow Float64 conversion",
        _DECIMALS.a.cast("float64"),
        {"a": _interval(point, point)},
        literal(native),
    )


@pytest.mark.parametrize("width,significand", [(32, 24), (64, 53)])
def test_decimal_float_scaling_encloses_composed_rounding(width: int, significand: int) -> None:
    # Rounding factors for the unsigned-limb conversions, scaled assembly,
    # and final addition compose at most five ordinary relative errors.
    step = Fraction(1, 2**38)
    fields = {"a": _interval(-step, step, nullable=True)}
    source = ibis.table({"a": "decimal(38,38)"}).a
    result = z3.Real("converted_decimal")
    unit = Fraction(1, 2**significand)
    relative = (1 + unit) ** 5 - 1
    subnormal = Fraction(1, 2**148) if width == 32 else Fraction(0)
    _verify(
        f"Decimal to Float{width} compounded rounding",
        source.cast(f"float{width}"),
        fields,
        Cell(result, _A.null),
        z3.Abs(result - _A.value) <= z3.Abs(_A.value) * real(relative) + real(subnormal),
    )


def test_assumed_rounded_values_are_reused_as_physical_bounds() -> None:
    table = ibis.table({"a": "float64"})
    computed = table.a + 1.0
    expression = (computed > 0).ifelse(computed.ln(), 0)
    fields = {"a": _interval(-5, 7, nullable=True)}
    assert prove_domain(expression.op(), (), fields)


def _native(
    expr: ir.Value, inputs: Mapping[str, tuple[object, pa.DataType]] | None = None
) -> object:
    columns = {
        name: pd.Series(pd.arrays.ArrowExtensionArray(pa.array([value], type=dtype)))
        for name, (value, dtype) in (inputs or {}).items()
    }
    value: object = evaluate_numeric(expr.op(), columns, pd.RangeIndex(1)).iloc[0]
    return value


def test_float32_literal_rounds_before_comparison_and_branch_demand() -> None:
    single = ibis.literal(0.1, type="float32")
    double = ibis.literal(0.1, type="float64")
    observed = _native(single)
    assert isinstance(observed, float)
    rounded = _ieee_round(Fraction(0.1), z3.Float32())
    assert Fraction(observed) == rounded
    _verify("Float32 literal physical payload", single, {}, literal(rounded))
    condition = single == double
    assert _native(condition) is False
    safe_divide = numeric_binary(
        "divide", NumericValue(ibis.literal(1)), NumericValue(ibis.literal(0))
    )
    expression = condition.ifelse(1, safe_divide.value)
    assert _native(expression) is pd.NA
    exact_comparison = boolean(real(rounded) == real(Fraction(0.1)), z3.BoolVal(False))
    _verify(
        "Float32 literal determines divide-guard demand",
        expression,
        {},
        choose(exact_comparison, literal(1), literal(None)),
    )
    with pytest.raises(DomainProofError, match="undefined"):
        prove_domain(expression.op(), (), {})


@pytest.mark.parametrize("nested", [False, True])
def test_exp_refuses_actual_float32_overflow_under_float64_ibis_metadata(nested: bool) -> None:
    argument = (
        ibis.literal(8100.0, type="float32").sqrt()
        if nested
        else ibis.literal(90.0, type="float32")
    )
    expression = argument.exp()
    assert expression.type().is_float64()
    observed = _native(expression)
    assert isinstance(observed, float) and math.isinf(observed)
    # e > 2.71 implies exp(90) > Float32's finite limit.
    finite_limit = Fraction(2**128 - 2**104)
    check(
        "Float32 Exp native overflow despite Float64 metadata",
        (real(Fraction(271, 100) ** 90) > real(finite_limit),),
        z3.BoolVal(not evaluate_domain(expression.op(), {}).finite),
    )
    safe = ibis.literal(88.0, type="float32").exp()
    safe_value = _native(safe)
    assert isinstance(safe_value, float) and math.isfinite(safe_value)
    _verify(
        "Float32 Exp still accepts a finite actual carrier result",
        safe,
        {},
        literal(Fraction(safe_value)),
    )


def _conditional_pair(name: str, left: ir.Value, right: ir.Value) -> tuple[ir.Value, ir.Value]:
    if name == "ifelse":
        return ibis.literal(True).ifelse(left, right), ibis.literal(False).ifelse(left, right)
    if name == "searched":
        return ibis.cases((ibis.literal(True), left), else_=right), ibis.cases(
            (ibis.literal(False), left), else_=right
        )
    return ibis.coalesce(left, right), ibis.coalesce(right, left)


@pytest.mark.parametrize("name", ["ifelse", "searched", "coalesce"])
@pytest.mark.parametrize("bits,precision", [(32, 24), (64, 53)])
def test_conditional_checks_the_selected_arm_conversion(
    name: str, bits: int, precision: int
) -> None:
    expression, skipped = _conditional_pair(name, _FLOATS.i, ibis.literal(0.5, type=f"float{bits}"))
    unrepresentable = 2**precision + 1
    fields = {"i": _interval(unrepresentable, unrepresentable)}
    physical = {"i": (unrepresentable, pa.int64())}
    with pytest.raises(SemanticExpressionError) as error:
        _native(expression, physical)
    assert error.value.received is not None
    assert type(expression.op()).__name__ in error.value.received
    assert _native(skipped, physical) == 0.5
    rejected = not evaluate_domain(expression.op(), fields).finite
    check(
        "Conditional native integer conversion range",
        (*_input_constraints(fields), _I.value > 2**precision),
        z3.BoolVal(rejected),
    )
    _verify(
        "Conditional does not convert an unselected integer",
        skipped,
        fields,
        literal(Fraction(1, 2)),
    )


@pytest.mark.parametrize("name", ["coalesce", "ifelse", "searched", "least", "greatest"])
def test_all_null_demanded_expression_cannot_erase_its_native_failure(name: str) -> None:
    table = ibis.table({"x": "int8", "p": "bool"})
    failed = ibis.null().cast("int8").nullif(table.x.abs())
    fallback = ibis.literal(3, type="int8")
    expression = {
        "coalesce": ibis.coalesce(failed, fallback),
        "ifelse": table.p.ifelse(failed, fallback),
        "searched": ibis.cases((table.p, failed), else_=fallback),
        "least": ibis.least(failed, fallback),
        "greatest": ibis.greatest(fallback, failed),
    }[name]
    fields = {"x": _interval(-128, -128), "p": _interval(0, 1)}
    with pytest.raises(SemanticExpressionError) as error:
        _native(expression, {"x": (-128, pa.int8()), "p": (True, pa.bool_())})
    assert error.value.received is not None
    assert "expression=Abs" in error.value.received
    left = Cell(real(0), z3.BoolVal(True), z3.Abs(_X.value) <= 127)
    reference = coalesce(left, literal(3))
    actual = evaluate_domain(expression.op(), fields)
    check(
        f"{name} retains all-NULL arm failure",
        (*_input_constraints(fields), _P.value == 1),
        z3.And(z3.Not(reference.finite), z3.BoolVal(not actual.finite)),
    )


def test_decimal_endpoint_success_cannot_discharge_an_interior_inexact_trap() -> None:
    table = ibis.table({"a": "decimal(8,0)"})
    expression = table.a + ibis.literal(Decimal(90), type="decimal(8,0)")
    fields = {"a": _interval(10, 20)}
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        assert _native(expression, {"a": (Decimal(10), pa.decimal128(8, 0))}) == Decimal(100)
        assert _native(expression, {"a": (Decimal(20), pa.decimal128(8, 0))}) == Decimal(110)
        with pytest.raises(SemanticExpressionError) as error:
            _native(expression, {"a": (Decimal(11), pa.decimal128(8, 0))})
        assert error.value.received is not None
        assert "decimal.Inexact" in error.value.received
        rejected = not evaluate_domain(expression.op(), fields).finite
    check(
        "Decimal interior inexact trap despite fitting endpoints",
        (*_input_constraints(fields), _A.value + 90 == 101),
        z3.BoolVal(rejected),
    )


def test_float_try_cast_to_decimal_rounds_a_successful_scalar() -> None:
    expression = _FLOATS.x.try_cast("decimal(8,2)")
    point = Fraction(1.234)
    fields = {"x": _interval(point, point)}
    assert _native(expression, {"x": (1.234, pa.float64())}) == Decimal("1.23")
    output = evaluate_domain(expression.op(), fields)
    check(
        "TryCast Float64 to Decimal has a different successful payload",
        _input_constraints(fields),
        z3.Or(z3.BoolVal(not output.finite), gamma(output, literal(Fraction(123, 100)))),
    )


@pytest.mark.parametrize("function", ["sqrt", "ln"])
@pytest.mark.parametrize("integer_first", [False, True])
def test_comparison_uses_native_float32_math_carrier(function: str, integer_first: bool) -> None:
    term = _FLOATS.y.sqrt() if function == "sqrt" else _FLOATS.y.ln()
    assert term.type().is_float64()
    expression = _FLOATS.i > term if integer_first else term < _FLOATS.i
    maximum_integer = 2**24
    fields = {"y": _interval(4, 4), "i": _interval(maximum_integer + 1, maximum_integer + 1)}
    inputs = {"y": (4.0, pa.float32()), "i": (maximum_integer + 1, pa.int64())}
    with pytest.raises(SemanticExpressionError) as error:
        _native(expression, inputs)
    assert error.value.received is not None
    assert str(maximum_integer + 1) in error.value.received
    refused = not evaluate_domain(expression.op(), fields).finite
    check(
        "Comparison checks the actual Float32 integer conversion range",
        (*_input_constraints(fields), _I.value > maximum_integer),
        z3.BoolVal(refused),
    )
    fields["i"] = _interval(maximum_integer, maximum_integer)
    inputs["i"] = (maximum_integer, pa.int64())
    assert _native(expression, inputs) is True
    _verify(
        "Comparison accepts an integer at the Float32 exact bound", expression, fields, literal(1)
    )


@pytest.mark.parametrize("point", [2.0, 0.25])
def test_float_literal_decimal_cast_uses_the_arrow_array_constructor(point: float) -> None:
    expression = ibis.literal(point, type="float64").cast("decimal(8,2)")
    with pytest.raises(SemanticExpressionError) as error:
        _native(expression)
    assert error.value.received is not None
    assert "expression=Cast" in error.value.received
    coefficient = real(Fraction(point)) * 100
    check(
        "Float literal Decimal cast fails even on the target lattice",
        (coefficient == int(point * 100),),
        z3.BoolVal(not evaluate_domain(expression.op(), {}).finite),
    )
    field = _FLOATS.x.cast("decimal(8,2)")
    concrete = Fraction(point)
    assert _native(field, {"x": (point, pa.float64())}) == Decimal(str(point))
    _verify(
        "The ordinary field conversion remains admissible",
        field,
        {"x": _interval(concrete, concrete)},
        literal(concrete),
    )
    integer = ibis.literal(2, type="int64").cast("decimal(8,2)")
    assert _native(integer) == Decimal(2)
    _verify("An integer literal constructs a Decimal", integer, {}, literal(2))


def test_decimal_round_cannot_exceed_the_active_maximum_adjusted_exponent() -> None:
    source = ibis.table({"a": "decimal(8,2)"}).a
    expression = source.round(0)
    with localcontext() as context:
        context.Emax = 1
        fields = {"a": _interval(123, 123)}
        with pytest.raises(SemanticExpressionError) as error:
            _native(expression, {"a": (Decimal("123.00"), pa.decimal128(8, 2))})
        assert error.value.received is not None
        assert "decimal.InvalidOperation" in error.value.received
        refused = not evaluate_domain(expression.op(), fields).finite
        check(
            "Decimal quantize checks the active maximum adjusted exponent",
            (*_input_constraints(fields), _A.value >= 10 ** (context.Emax + 1)),
            z3.BoolVal(refused),
        )
        assert _native(expression, {"a": (Decimal("12.00"), pa.decimal128(8, 2))}) == Decimal(12)
        _verify(
            "Decimal quantize within the active exponent",
            expression,
            {"a": _interval(12, 12)},
            literal(12),
        )
