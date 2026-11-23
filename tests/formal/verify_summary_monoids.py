"""Verify L6 summary laws and T1/T2 information boundaries against production anchors."""

import math
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal
from fractions import Fraction
from typing import Literal, get_args

import ibis
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pytest
import z3  # type: ignore[import-not-found, import-untyped, unused-ignore]  # Optional untyped solver.
from ibis.backends.duckdb import Backend
from ibis.common.deferred import Just

import marivo.analysis as mv
from marivo._definition.expressions import (
    BuiltinSampleCoordinate,
    MetricExpression,
    PerReduce,
    RowField,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.types import AxisBinding, ExactRef, SumAll
from marivo._kernel.refs import ref
from marivo.analysis.binding.attribute_game import build_game
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.sample_reductions import SampleLowerer, SampleRelation, _SampleInputs
from marivo.analysis.methods.frame_correlation import correlation
from marivo.analysis.methods.frame_numeric_reduction import reduce_numeric
from marivo.analysis.model.data_types import DataType, DecimalDataType, ScalarDataType
from marivo.analysis.model.summary_traits import L6Class, SummaryReducer, summary_trait
from marivo.semantic._numeric_lowering import finish_nullable_sum, lower_source_reduction
from tests.formal._summary_monoids import (
    Cell,
    Value,
    distinct_times,
    exact_sum,
    finish_default,
    merge_default,
    merge_strict,
    monoid_laws,
    plus,
    rank_correlation,
    result,
    same,
    translate_finisher,
    vectors,
)


def _finish(table: ir.Table) -> ir.Value:
    return finish_nullable_sum(table.v.sum(), table.count(), table.v.count())


_ROOT = ExactRef(ref.entity("proof.facts"), "v1")
_VALUE = RowField(ExactRef(ref.measure("proof.facts.value"), "v1"), _ROOT, "1", SumAll())
_WEIGHT = RowField(ExactRef(ref.measure("proof.facts.weight"), "v1"), _ROOT, "1", SumAll())
_BY = (AxisBinding(_ROOT, ExactRef(ref.dimension("proof.facts.group"), "v1"), (), "field"),)


@pytest.mark.parametrize("dtype", ["int64", "decimal(20,2)", "float64"])
def test_translates_production_sum_finisher_for_unbounded_counts(dtype: str) -> None:
    table = ibis.table({"v": dtype}, name="source_group")
    total, contribution_count, valid_count = table.v.sum(), table.count(), table.v.count()
    expression = _finish(table)
    n, h = z3.Ints("contributions valid_inputs")
    mass = z3.Real("native_sum")
    raw_missing, valid_nan = z3.Bools("native_sum_is_null native_sum_is_nan")
    raw = Cell(mass, raw_missing, valid_nan)
    actual = translate_finisher(
        expression.op(),
        {total.op(): raw, contribution_count.op(): Cell(n), valid_count.op(): Cell(h)},
    )
    expected = Cell(
        z3.If(n == 0, z3.RealVal(0), mass), z3.And(n != 0, h == 0), z3.And(h > 0, valid_nan)
    )
    premise = [h >= 0, n >= h, z3.Implies(h > 0, z3.Not(raw_missing))]
    if not expression.type().is_floating():
        premise.append(z3.Not(valid_nan))
    result(
        f"{dtype} production sum AST equals the three-state completion",
        same(actual, expected),
        *premise,
        logic="QF_LIRA",
    )


@pytest.fixture
def duckdb_backend() -> Iterator[Backend]:
    backend = ibis.duckdb.connect()
    try:
        yield backend
    finally:
        backend.disconnect()


def _same_observation(actual: object, expected: Value) -> bool:
    if isinstance(expected, float) and math.isnan(expected):
        return isinstance(actual, float) and math.isnan(actual)
    return actual == expected


@pytest.mark.parametrize(
    "kind, bag",
    [
        ("float", ()),
        ("float", (None,)),
        ("float", (0.0,)),
        ("float", (None, 0.0)),
        ("float", (2.0, -2.0)),
        ("float", (None, math.nan)),
        ("float", (2.0, math.nan, None)),
        ("decimal", ()),
        ("decimal", (None, None)),
        ("decimal", (Decimal("0.00"), None)),
        ("decimal", (Decimal("2.25"), Decimal("-1.00"))),
        ("integer", (0,)),
        ("integer", ()),
        ("integer", (None,)),
        ("integer", (None, 2, -5)),
    ],
)
def test_sql_arrow_and_direct_bags_distinguish_zero_null_and_valid_nan(
    duckdb_backend: Backend, kind: str, bag: tuple[Value, ...]
) -> None:
    carriers: dict[str, tuple[pa.DataType, DataType]] = {
        "float": (pa.float64(), ScalarDataType("float64")),
        "integer": (pa.int64(), ScalarDataType("int64")),
        "decimal": (pa.decimal128(20, 2), DecimalDataType(20, 2)),
    }
    physical, dtype = carriers[kind]
    array = pa.array(bag, type=physical, from_pandas=False)
    table = ibis.memtable(pa.table({"v": array}))
    output = duckdb_backend.to_pyarrow(table.aggregate(finished=_finish(table)))
    sql: object = output["finished"][0].as_py()
    arrow = reduce_numeric(
        pd.Series(pd.arrays.ArrowExtensionArray(array)), dtype, "sum", empty_group=not bag
    )
    expected = exact_sum(bag)
    assert _same_observation(sql, expected), (kind, bag, sql, expected)
    assert _same_observation(arrow, expected), (kind, bag, arrow, expected)
    assert output["finished"][0].is_valid == (expected is not None)


_TRAITS: tuple[tuple[SummaryReducer, L6Class], ...] = (
    ("count", "additive_count"),
    ("entity.count", "additive_count"),
    ("row_count", "additive_count"),
    ("pair_count", "additive_count"),
    ("sum", "three_state_sum"),
    ("min", "extremum"),
    ("max", "extremum"),
    ("mean", "mean_pair"),
    ("weighted_mean", "weighted_pair"),
    ("first", "ordered_extremum"),
    ("last", "ordered_extremum"),
    ("count_distinct", "exact_set"),
    ("median", "exact_multiset"),
    ("quantile", "exact_multiset"),
    ("pearson", "pearson_moments"),
    ("spearman", "non_compressible"),
    ("approx_nunique", "sketch_opaque"),
    ("approx_median", "sketch_opaque"),
    ("approx_quantile", "sketch_opaque"),
)


def test_production_summary_classes_match_the_proof_inventory() -> None:
    assert all(summary_trait(reducer).l6_class == expected for reducer, expected in _TRAITS)
    strict = summary_trait("sum", nulls_policy="error")
    assert strict.l6_class == "strict_sum" and not strict.implicit_single_value_collapse_pairs
    assert {expected for _, expected in _TRAITS} | {strict.l6_class} == set(get_args(L6Class))


@pytest.mark.parametrize(
    "label,width", [("count", 1), ("mean", 2), ("weighted mean", 2), ("Pearson", 6)]
)
def test_unbounded_additive_summary_product_monoids(label: str, width: int) -> None:
    integer = label == "count"
    a, b, c = vectors("abc", width, z3.Int if integer else z3.Real)
    unit = tuple((z3.IntVal if integer else z3.RealVal)(0) for _ in range(width))
    domain = tuple(state[0] >= 0 for state in (a, b, c)) if integer else ()
    result(
        f"{label} componentwise identity, associativity and commutativity",
        monoid_laws(plus, unit, a, b, c),
        *domain,
        logic="QF_LIA" if integer else "QF_LRA",
    )


def _default_state(name: str) -> tuple[z3.ArithRef, z3.ArithRef]:
    return z3.Int(f"{name}_kind"), z3.Real(f"{name}_mass")


def _valid_default(state: tuple[z3.ArithRef, z3.ArithRef]) -> z3.BoolRef:
    kind, mass = state
    return z3.And(kind >= 0, kind <= 2, z3.Implies(kind != 2, mass == 0))


def test_default_sum_monoid_and_two_continuations_distinguish_all_states() -> None:
    a, b, c = map(_default_state, "abc")
    empty, unknown = (z3.IntVal(0), z3.RealVal(0)), (z3.IntVal(1), z3.RealVal(0))
    result(
        "Default sum state monoid",
        monoid_laws(merge_default, empty, a, b, c),
        *(_valid_default(state) for state in (a, b, c)),
        logic="QF_LIRA",
    )
    same_now = same(finish_default(a), finish_default(b))
    same_after_null = same(
        finish_default(merge_default(a, unknown)), finish_default(merge_default(b, unknown))
    )
    result(
        "Default sum's empty and all-NA continuations separate every unequal state",
        z3.And(a[0] == b[0], a[1] == b[1]),
        _valid_default(a),
        _valid_default(b),
        same_now,
        same_after_null,
        logic="QF_LIRA",
    )
    result(
        "T1/T2 counterexample: displayed zero loses empty versus valid zero",
        same_after_null,
        _valid_default(a),
        _valid_default(b),
        same_now,
        a[0] == 0,
        b[0] == 2,
        b[1] == 0,
        logic="QF_LIRA",
        counterexample=True,
    )
    assert (exact_sum(()), exact_sum((None,))) == (exact_sum((0,)), exact_sum((None,)))
    assert exact_sum((None,)) is None and exact_sum((0, None)) == 0


def _strict_state(name: str) -> tuple[z3.BoolRef, z3.ArithRef]:
    return z3.Bool(f"{name}_failed"), z3.Real(f"{name}_value")


def test_strict_sum_absorbs_failure_and_its_final_states_are_a_congruence() -> None:
    a, b, suffix = map(_strict_state, ("a", "b", "suffix"))
    zero = (z3.BoolVal(False), z3.RealVal(0))
    failed = (z3.BoolVal(True), z3.RealVal(0))
    invariant = tuple(z3.Implies(state[0], state[1] == 0) for state in (a, b, suffix))
    result(
        "Strict sum state monoid",
        monoid_laws(merge_strict, zero, a, b, suffix),
        *invariant,
        logic="QF_LRA",
    )
    error, payload = merge_strict(a, failed)
    result(
        "Strict sum's failure absorbs every suffix",
        z3.And(error, payload == 0),
        *invariant,
        logic="QF_LRA",
    )
    a_after, b_after = merge_strict(a, suffix), merge_strict(b, suffix)
    equal_observation = z3.And(a[0] == b[0], z3.Or(a[0], a[1] == b[1]))
    result(
        "T2 strict sum preserves value and error equivalence under any continuation",
        z3.And(a_after[0] == b_after[0], a_after[1] == b_after[1]),
        *invariant,
        equal_observation,
        logic="QF_LRA",
    )
    # The strict emission for either the empty bag or the single valid zero is V(0).
    observed_empty, observed_valid_zero = (
        merge_strict(zero, suffix),
        merge_strict((z3.BoolVal(False), z3.RealVal(exact_sum((0,)))), suffix),
    )
    result(
        "Empty and valid zero are strict-sum right congruent",
        z3.And(*(x == y for x, y in zip(observed_empty, observed_valid_zero, strict=True))),
        *invariant,
        logic="QF_LRA",
    )


@pytest.mark.parametrize("weighted", [False, True], ids=["mean", "weighted-mean"])
def test_mean_finisher_preserves_covalid_partition_support(weighted: bool) -> None:
    x, y, z = z3.Reals("x y z")
    p, q, r = z3.Bools("x_present y_present z_present")
    u, v, w = z3.Reals("u v w")
    i, j, k = z3.Bools("u_present v_present w_present")
    if weighted:
        active = (z3.And(p, i), z3.And(q, j), z3.And(r, k))
        numbers, weights = (x * u, y * v, z * w), (u, v, w)
    else:
        active = (p, q, r)
        numbers, weights = (x, y, z), (z3.RealVal(1),) * 3
    states = tuple(
        (z3.If(valid, value, 0), z3.If(valid, weight, 0))
        for valid, value, weight in zip(active, numbers, weights, strict=True)
    )
    mass, denominator = plus(plus(states[0], states[2]), states[1])
    direct_mass = z3.Sum(
        *(z3.If(valid, number, 0) for valid, number in zip(active, numbers, strict=True))
    )
    direct_denominator = z3.Sum(
        *(z3.If(valid, weight, 0) for valid, weight in zip(active, weights, strict=True))
    )
    observed = Cell(mass / denominator, denominator == 0)
    expected = Cell(direct_mass / direct_denominator, direct_denominator == 0)
    result(
        f"{'Weighted mean' if weighted else 'Mean'} covalid support and zero-denominator completion",
        same(observed, expected),
        logic="QF_NRA",
    )


@pytest.mark.parametrize("least", [False, True], ids=["max", "min"])
def test_ordered_numeric_extremum_monoid(least: bool) -> None:
    absent, zero = z3.BoolVal(False), z3.RealVal(0)
    a, b, c = ((z3.Bool(f"{name}_present"), z3.Real(f"{name}_value")) for name in "abc")

    def merge(
        left: tuple[z3.ExprRef, ...], right: tuple[z3.ExprRef, ...]
    ) -> tuple[z3.ExprRef, ...]:
        take_left = left[1] <= right[1] if least else left[1] >= right[1]
        winning = z3.And(left[0], z3.Or(z3.Not(right[0]), take_left))
        return z3.Or(left[0], right[0]), z3.If(winning, left[1], right[1])

    result(
        f"{'Min' if least else 'Max'} uses NA identity on an exact total order",
        monoid_laws(merge, (absent, zero), a, b, c),
        *(z3.Implies(z3.Not(state[0]), state[1] == 0) for state in (a, b, c)),
        logic="QF_LRA",
    )


@pytest.mark.parametrize("reducer", ["first", "last"])
def test_different_times_make_ordered_summary_merge_commutative(reducer: str) -> None:
    absent, zero = z3.BoolVal(False), z3.IntVal(0)
    a, b, c = (
        (z3.Bool(f"{name}_p"), z3.Int(f"{name}_t"), z3.Int(f"{name}_v"), z3.Bool(f"{name}_na"))
        for name in "abc"
    )

    def merge(
        left: tuple[z3.ExprRef, ...], right: tuple[z3.ExprRef, ...]
    ) -> tuple[z3.ExprRef, ...]:
        ordered = left[1] < right[1] if reducer == "first" else left[1] > right[1]
        selected = z3.And(left[0], z3.Or(z3.Not(right[0]), ordered))
        return tuple(z3.If(selected, x, y) for x, y in zip(left, right, strict=True))

    disjoint = tuple(
        z3.Implies(z3.And(x[0], y[0]), x[1] != y[1]) for x, y in ((a, b), (a, c), (b, c))
    )
    canonical = tuple(
        z3.Implies(z3.Not(state[0]), z3.And(state[1] == 0, state[2] == 0, state[3]))
        for state in (a, b, c)
    )
    result(
        f"Ordered {reducer} conditional monoid with unique whole clocks",
        monoid_laws(merge, (absent, zero, zero, z3.BoolVal(True)), a, b, c),
        *disjoint,
        *canonical,
        logic="QF_LIA",
    )


def test_duplicate_time_can_be_invisible_at_either_ordered_endpoint() -> None:
    a_first, b_first, alternative, a_hidden, b_hidden, a_last, b_last = z3.Ints(
        "a_first b_first alternative a_hidden b_hidden a_last b_last"
    )
    # The hidden samples are interior (and can be NA). Moving A's hidden
    # sample leaves both shards' first/last summaries and sizes identical.
    order = (
        a_first < b_first,
        b_first < alternative,
        alternative < a_hidden,
        a_hidden == b_hidden,
        b_hidden < a_last,
        a_last < b_last,
    )
    a = (a_first, a_hidden, a_last)
    b = (b_first, b_hidden, b_last)
    result(
        "Whole-clock collision hidden from first/last after NA filtering",
        distinct_times((*a, *b)),
        *order,
        logic="QF_LIA",
        counterexample=True,
    )
    result(
        "Each shard is unique and moving one interior clock preserves valid extrema",
        z3.And(
            distinct_times(a), distinct_times(b), distinct_times((a_first, alternative, a_last, *b))
        ),
        *order,
        logic="QF_LIA",
    )


def _ordered_plan(
    table: ir.Table, reducer: Literal["first", "last"], include_null: bool
) -> SampleRelation:
    grain = mv.grain("day")
    parent = table.select("group_key").distinct()
    captured = _SampleInputs(
        bindings=(),
        axes=(BuiltinSampleCoordinate(grain, "UTC"),),
        sample_groups=(("clock",),),
        complete_keys=("group_key", "clock", "row_id"),
        parent=parent,
        table=table,
        operands=(SampleRelation(table, "v"),),
        implicit=False,
    )
    node = PerReduce(MetricExpression(Just(0), ()), grain, reducer, include_null=include_null)
    lowerer = SampleLowerer(
        FormulaProgram((), (), ()),
        {},
        ("group_key",),
        "duckdb",
        lambda *_: pytest.fail("unexpected Population read"),
    )
    return lowerer._ordered_sample(node, "answer", captured, ("group_key",), {}, [])


@pytest.mark.parametrize("reducer", ["first", "last"])
@pytest.mark.parametrize("include_null", [False, True])
def test_native_ordered_compiler_checks_complete_times_before_value_filter(
    duckdb_backend: Backend, reducer: Literal["first", "last"], include_null: bool
) -> None:
    # Group 1 duplicates an interior NULL-valued clock. Group 2 has no
    # duplicate, and equal endpoint times across different parents are legal.
    table = ibis.memtable(
        pa.table(
            {
                "group_key": [1] * 4 + [2] * 4,
                "clock": [1, 2, 2, 4, 1, 2, 3, 4],
                "row_id": range(8),
                "v": pa.array([10, None, None, 40, None, 20, 30, None], type=pa.int64()),
            }
        )
    )
    lowered = _ordered_plan(table, reducer, include_null)
    rows = duckdb_backend.to_pyarrow(lowered.table.order_by("group_key"))
    assert rows["group_key"].to_pylist() == [1, 2]
    expected = [
        10 if reducer == "first" else 40,
        None if include_null else 20 if reducer == "first" else 30,
    ]
    assert rows["answer"].to_pylist() == expected
    checks = tuple(
        name
        for name, requirement, _ in lowered.checks
        if requirement == "one internal sample per time key"
    )
    actual_errors = [False, False] if not checks else rows[checks[0]].to_pylist()
    assert actual_errors == [True, False], (
        "The demanded duplicate in group 1 must survive endpoint selection"
    )


def test_set_membership_and_multiset_multiplicity_merge_extensionally() -> None:
    # a, b and c are membership/counts of an arbitrary identity (or value).
    # Equality at this arbitrary point is extensional equality of the state.
    a, b, c = vectors("abc", 1, z3.Bool)

    def union(
        left: tuple[z3.ExprRef, ...], right: tuple[z3.ExprRef, ...]
    ) -> tuple[z3.ExprRef, ...]:
        return (z3.Or(left[0], right[0]),)

    result(
        "Exact set membership union",
        z3.And(monoid_laws(union, (z3.BoolVal(False),), a, b, c), union(a, a)[0] == a[0]),
        logic="QF_UF",
    )
    a, b, c = vectors("xyz", 1, z3.Int)
    result(
        "Exact multiset occurrence-count union",
        monoid_laws(plus, (z3.IntVal(0),), a, b, c),
        *(state[0] >= 0 for state in (a, b, c)),
        logic="QF_LIA",
    )
    left, right = {"a"}, {"b"}
    assert (len(left), len(right)) == (len(left), len(left))
    assert len(left | right) == 2 and len(left | left) == 1


def _histogram_median(counts: Counter[int]) -> Fraction:
    length = sum(counts.values())
    middle = ((length - 1) // 2, length // 2)
    values: list[int] = []
    for value, count in sorted(counts.items()):
        if any(0 <= index < count for index in middle):
            values.extend(value for index in middle if 0 <= index < count)
        middle = (middle[0] - count, middle[1] - count)
    return Fraction(sum(values), 2)


def test_exact_multiset_recovers_median_when_finished_groups_do_not() -> None:
    a, b = (0, 2), (2, 4)
    c, d = (1, 1), (0, 6)
    assert (len(a), len(b), Fraction(sum(a), 2), Fraction(sum(b), 2)) == (
        len(c),
        len(d),
        Fraction(sum(c), 2),
        Fraction(sum(d), 2),
    )
    assert _histogram_median(Counter(a) + Counter(b)) == 2
    assert _histogram_median(Counter(c) + Counter(d)) == 1


def test_pearson_six_moments_equal_centered_pairwise_products() -> None:
    xs, ys = z3.Reals("x0 x1 x2"), z3.Reals("y0 y1 y2")
    points = tuple((z3.RealVal(1), x, y, x * x, y * y, x * y) for x, y in zip(xs, ys, strict=True))
    count, sx, sy, sxx, syy, sxy = plus(points[0], plus(points[1], points[2]))

    def pair_products(left: list[z3.ArithRef], right: list[z3.ArithRef]) -> z3.ArithRef:
        output = z3.Sum(
            *((left[i] - left[j]) * (right[i] - right[j]) for i, j in ((0, 1), (0, 2), (1, 2)))
        )
        return output

    checks = z3.And(
        count == 3,
        count * sxy - sx * sy == pair_products(xs, ys),
        count * sxx - sx**2 == pair_products(xs, xs),
        count * syy - sy**2 == pair_products(ys, ys),
    )
    result(
        "Pearson moments equal complete-bag covariance and variance numerators",
        checks,
        logic="QF_NRA",
    )


def test_pearson_reconstructs_centered_sums_at_arbitrary_positive_count() -> None:
    n, sx, sy, sxx, syy, sxy, mx, my = z3.Reals("n sx sy sxx syy sxy mx my")
    # These are the coefficients obtained by distributing each centered
    # product over the complete bag. No finite row count is fixed here.
    covariance = sxy - mx * sy - my * sx + n * mx * my
    x_variance = sxx - 2 * mx * sx + n * mx**2
    y_variance = syy - 2 * my * sy + n * my**2
    result(
        "Pearson sufficient moments reconstruct all centered terms",
        z3.And(
            n * covariance == n * sxy - sx * sy,
            n * x_variance == n * sxx - sx**2,
            n * y_variance == n * syy - sy**2,
        ),
        n > 0,
        n * mx == sx,
        n * my == sy,
        logic="QF_NRA",
    )


def test_two_point_spearman_has_unbounded_continuation_signatures() -> None:
    i, j = z3.Ints("rank_source_i rank_source_j")
    context_x = 2 * i + 1
    # Both singleton bags have NULL correlation and count one. The same
    # continuation places its x value between their x values and its y value
    # above both. Their two-point Spearman values must have opposite signs.
    first = z3.If(2 * i < context_x, 1, -1)
    second = z3.If(2 * j < context_x, 1, -1)
    result(
        "Any two singleton rank inputs have a separating continuation",
        z3.And(first == 1, second == -1),
        i < j,
        logic="QF_LIA",
    )
    result(
        "T1 Spearman collision with two finished singleton summaries",
        first == second,
        i == 0,
        j == 1,
        logic="QF_LIA",
        counterexample=True,
    )
    assert rank_correlation(((0, 0), (1, 1))) == 1
    assert rank_correlation(((2, 0), (1, 1))) == -1


def test_sketch_route_uses_native_estimator_and_not_sums_of_partition_estimates(
    duckdb_backend: Backend,
) -> None:
    def estimate(table: ir.Table) -> tuple[int, float]:
        scalar = table.aggregate(distinct=table.v.approx_nunique(), middle=table.v.approx_median())
        output = duckdb_backend.to_pyarrow(scalar)
        a, b = output["distinct"][0].as_py(), output["middle"][0].as_py()
        assert isinstance(a, int) and isinstance(b, float)
        return a, b

    one = ibis.memtable(pa.table({"v": [7.0]}))
    whole = ibis.memtable(pa.table({"v": [7.0, 7.0]}))
    observed = estimate(one)
    assert observed == estimate(whole) == (1, 7.0)
    assert tuple(value + value for value in observed) == (2, 14.0)
    # Same-layout register join alone has a law. This model makes no claim
    # about the native bytes, layouts, or digest merging of any provider.
    a, b, c = vectors("abc", 1, z3.Int)

    def join(left: tuple[z3.ExprRef, ...], right: tuple[z3.ExprRef, ...]) -> tuple[z3.ExprRef, ...]:
        return (z3.If(left[0] >= right[0], left[0], right[0]),)

    result(
        "An ideal same-layout register has an idempotent max join",
        z3.And(monoid_laws(join, (z3.IntVal(0),), a, b, c), join(a, a)[0] == a[0]),
        *(state[0] >= 0 for state in (a, b, c)),
        logic="QF_LIA",
    )


@pytest.mark.parametrize("bag", [(), (0,), (None,), (0, None), (3, -1)])
def test_strict_source_sum_observes_failure_separately_from_payload(
    duckdb_backend: Backend, bag: tuple[int | None, ...]
) -> None:
    table = ibis.memtable(pa.table({"v": pa.array(bag, type=pa.int64())}))
    lowered = lower_source_reduction(
        SourceAggregate(_VALUE, "sum", None, nulls="error"), table, {_VALUE.ref.ref.path: table.v}
    )
    (check,) = lowered.checks
    output = duckdb_backend.to_pyarrow(table.aggregate(value=lowered.value, error=check.violation))
    failed = None in bag
    assert output["error"][0].as_py() is failed
    if not failed:
        assert output["value"][0].as_py() == exact_sum(bag)


@pytest.mark.parametrize(
    "pairs,expected",
    [
        (((9, 2), (99, None), (None, 100), (3, 1)), 7),
        (((9, 1), (3, -1)), None),
        (((None, 1), (3, None)), None),
        ((), None),
    ],
)
def test_native_weighted_mean_uses_the_same_support_for_both_summaries(
    duckdb_backend: Backend, pairs: tuple[tuple[int | None, int | None], ...], expected: int | None
) -> None:
    table = ibis.memtable(
        pa.table(
            {
                "v": pa.array([v for v, _ in pairs], type=pa.int64()),
                "w": pa.array([w for _, w in pairs], type=pa.int64()),
            }
        )
    )
    fields = {_VALUE.ref.ref.path: table.v, _WEIGHT.ref.ref.path: table.w}
    lowered = lower_source_reduction(SourceWeightedMean(_VALUE, _WEIGHT, None), table, fields)
    assert not lowered.checks
    output = duckdb_backend.to_pyarrow(table.aggregate(value=lowered.value))
    assert output["value"][0].as_py() == expected


def test_pearson_group_coefficients_and_counts_lose_the_whole_result() -> None:
    dtype = ScalarDataType("int64")

    def pearson(xs: tuple[int, ...], ys: tuple[int, ...]) -> float | None:
        return correlation(xs, ys, left_type=dtype, right_type=dtype, method="pearson")

    assert pearson((0, 1), (0, 1)) == pytest.approx(1)
    assert pearson((3, 4), (3, 4)) == pytest.approx(1)
    assert pearson((3, 4), (-3, -2)) == pytest.approx(1)
    assert pearson((0, 1, 3, 4), (0, 1, 3, 4)) == pytest.approx(1)
    # Centered products sum to -8, with centered squares summing to 10
    # independently in each column. The partition summaries still match.
    assert pearson((0, 1, 3, 4), (0, 1, -3, -2)) == pytest.approx(-0.8)
    assert pearson((0,), (1,)) is None
    assert pearson((0, 0), (1, 2)) is None


@pytest.mark.parametrize(
    "reducer,q", [("approx_nunique", None), ("approx_median", None), ("approx_quantile", 0.5)]
)
def test_attribute_never_exposes_native_estimate_as_mergeable_scalar_state(
    reducer: Literal["approx_nunique", "approx_median", "approx_quantile"], q: float | None
) -> None:
    approximate = build_game(SourceAggregate(_VALUE, reducer, None, q=q), {}, metric="x", by=_BY)
    assert not approximate.leaves[0].state_eligible
    exact = build_game(SourceAggregate(_VALUE, "count_distinct", None), {}, metric="x", by=_BY)
    assert exact.leaves[0].state_eligible


@pytest.mark.parametrize(
    "bag,expected",
    [
        ((), (0, None, None, None, 0, None)),
        ((None, None), (0, None, None, None, 0, None)),
        ((None, 1, 1, 4), (3, 2.0, 1, 4, 2, 1.0)),
    ],
)
def test_source_count_mean_extrema_set_and_multiset_finishers(
    duckdb_backend: Backend, bag: tuple[int | None, ...], expected: tuple[object, ...]
) -> None:
    array = pa.array(bag, type=pa.int64())
    table = ibis.memtable(pa.table({"v": array}))
    fields = {_VALUE.ref.ref.path: table.v}
    reducers: tuple[Literal["count", "mean", "min", "max", "count_distinct", "median"], ...] = (
        "count",
        "mean",
        "min",
        "max",
        "count_distinct",
        "median",
    )
    metrics = {
        name: lower_source_reduction(SourceAggregate(_VALUE, name, None), table, fields).value
        for name in reducers
    }
    output = duckdb_backend.to_pyarrow(table.aggregate(**metrics))
    assert tuple(output[name][0].as_py() for name in reducers) == expected
    arrow = reduce_numeric(
        pd.Series(pd.arrays.ArrowExtensionArray(array)),
        ScalarDataType("int64"),
        "mean",
        empty_group=not bag,
    )
    assert arrow == expected[1]
