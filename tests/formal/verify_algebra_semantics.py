"""Independent reference examples and differential checks for analysis algebra."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date
from decimal import Decimal
from fractions import Fraction
from math import inf
from pathlib import Path

import ibis
import pytest
from hypothesis import example, given
from hypothesis import strategies as st

import marivo.analysis as mv
from marivo.analysis.compiler.frame_filter_fusion import frame_filter_steps
from marivo.analysis.compiler.query_stages import FrameCompute
from marivo.analysis.materialization import graph_source_execution
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_population_membership import _session as membership_session
from tests.analysis.materialization.test_query_optimization import (
    optimization_session as optimization_session,
)
from tests.analysis.materialization.test_query_optimization import (
    test_common_read_cse_projects_each_logical_value_and_check as test_common_read_cse_projects_each_logical_value_and_check,
)
from tests.formal.reference.bridge import (
    observe_case,
    observe_ordered_samples,
    observe_samples,
    open_source,
    read_scores,
    replace_facts,
    source_metric,
)
from tests.formal.reference.equivalence import (
    _witnesses,
    assert_equivalent,
    assert_policies_agree,
    run_frame,
)
from tests.formal.reference.evaluator import (
    align,
    compare,
    fix,
    observe,
    over,
    push_forward,
    reduce_bag,
    select,
    top,
    where,
)
from tests.formal.reference.generated_relations import (
    generated_membership_source as generated_membership_source,
)
from tests.formal.reference.generated_relations import (
    test_generated_fixed_repeated_partition_expression as test_generated_fixed_repeated_partition_expression,
)
from tests.formal.reference.generated_relations import (
    test_generated_joint_product_membership as test_generated_joint_product_membership,
)
from tests.formal.reference.generated_relations import (
    test_generated_partial_time_comparison as test_generated_partial_time_comparison,
)
from tests.formal.reference.generated_relations import (
    test_generated_upstream_strict_failure_survives_top_zero as test_generated_upstream_strict_failure_survives_top_zero,
)
from tests.formal.reference.generators import (
    BranchSpec,
    Fact,
    ObservationCase,
    SampleCase,
    branch_inputs,
    calendar_day,
    demand_cases,
    observations,
    sample_inputs,
    samples,
)
from tests.formal.reference.model import (
    Bag,
    Binary,
    Branch,
    Coalesce,
    Column,
    IfElse,
    Key,
    LiteralValue,
    Obligation,
    Occurrence,
    Read,
    Reducer,
    Result,
    Scope,
    Unary,
    Value,
    valid_nan,
)
from tests.formal.reference.relations import (
    classify,
    compose,
    in_population,
    membership,
    per,
    preimage,
    window_slices,
)
from tests.shared_fixtures import catalog_value
from tests.support.optimization import compute_with_policy
from tests.support.source_trace import SourceTrace, capture_source


def _bag(*values: Value) -> Bag:
    return tuple(Occurrence((index,), value) for index, value in enumerate(values))


def _column(result: Result, name: str = "value") -> tuple[Value, ...]:
    return tuple(result.columns[name].at(key) for key in result.keys)


def test_reference_four_mean_questions() -> None:
    customers: dict[Key, Mapping[Key, Bag]] = {(): {("A",): _bag(10, 30), ("B",): _bag(100)}}
    roster: dict[Key, tuple[Key, ...]] = {(): (("A",), ("B",), ("C",))}
    mean, total = Reducer("mean"), Reducer("sum")
    assert reduce_bag(mean, _bag(10, 30, 100)) == (Fraction(140, 3), None)
    assert _column(per(customers, total, mean)) == (70,)
    assert _column(per(customers, mean, mean)) == (60,)
    assert _column(per(customers, total, mean, roster)) == (Fraction(140, 3),)
    with_order: dict[Key, Mapping[Key, Bag]] = {(): {**customers[()], ("A",): _bag(10, 30, 20)}}
    assert reduce_bag(mean, _bag(10, 30, 20, 100)) == (40, None)
    assert _column(per(with_order, total, mean, roster)) == (Fraction(160, 3),)
    with_null: dict[Key, Mapping[Key, Bag]] = {(): {**customers[()], ("C",): _bag(None)}}
    assert _column(per(with_null, total, mean, roster)) == (70,)


def test_reference_sum_keeps_empty_wholly_missing_and_zero_separate() -> None:
    total, mean = Reducer("sum"), Reducer("mean")
    for inputs, expected in (
        ((), (0, None)),
        ((None, None), (None, None)),
        ((0,), (0, 0)),
        ((10, None, 30), (40, 20)),
    ):
        assert (reduce_bag(total, _bag(*inputs))[0], reduce_bag(mean, _bag(*inputs))[0]) == expected
    fixed = Scope("explicit", (("A",), ("B",)))
    rows: tuple[Mapping[Key, Bag], ...] = (
        {("B",): _bag(None)},
        {("A",): _bag(0), ("B",): _bag(None)},
    )
    for data in rows:
        result = observe({"branch": Branch(total, data)}, {"value": Read("branch")}, fixed)
        assert _column(result) == (0, None)
        assert _column(push_forward(result, (), {"value": (total, "value")})) == (0,)
    assert reduce_bag(total, _bag(None))[0] is None
    assert reduce_bag(total, _bag(0, None))[0] == 0


def test_reference_sparse_support_and_independent_calendar_broadcast() -> None:
    total = Reducer("sum")
    a = Branch(total, {(1,): _bag(40)})
    b = Branch(total, {(3,): _bag(100)})
    expr = Binary("add", Read("a"), Binary("multiply", LiteralValue(0), Read("b")))
    observed = observe({"a": a, "b": b}, {"value": expr}, Scope("grouped"))
    assert observed.keys == ((1,), (3,))
    assert _column(observed) == (40, 0)
    assert _column(push_forward(observed, (), {"value": (Reducer("mean"), "value")})) == (20,)
    timed = Branch(total, {("A", 1): _bag(2), ("A", 3): _bag(8)}, timed=True)
    independent = Branch(total, {("A",): _bag(11), ("B",): _bag(13)})
    report = observe(
        {"measured": timed, "constant": independent},
        {"value": Binary("add", Read("measured"), Read("constant"))},
        Scope("calendar", calendar_position=1),
    )
    assert report.keys == (("A", 1), ("A", 3))
    assert _column(report) == (13, 19)
    assert observe({}, {}, Scope("scalar")).keys == ((),)
    assert observe({}, {}, Scope("explicit")).keys == ()


def test_reference_demand_and_upstream_obligations() -> None:
    branches = {
        "bad": Branch(Reducer("sum", strict=True), {(2,): _bag(None)}),
        "good": Branch(Reducer("sum"), {(1,): _bag(5)}),
    }
    safe = Coalesce(
        IfElse(Binary("eq", Read("good"), LiteralValue(5)), Read("good"), LiteralValue(0)),
        Read("bad"),
    )
    guarded = observe(branches, {"value": safe}, Scope("grouped"))
    assert _column(guarded) == (5, 0)
    assert not guarded.obligations
    forced = Binary("multiply", LiteralValue(0), Read("bad"))
    invalid = observe(branches, {"value": forced, "kept": Read("good")}, Scope("grouped"))
    assert invalid.obligations == {Obligation("strict_null", (2,))}
    assert (
        top(select(invalid, {"kept": Read("kept")}), 0, "kept").obligations == invalid.obligations
    )
    assert observe(branches, {"value": safe}, Scope("explicit", ((1,),))).witness("coverage") == (
        2,
    )


def test_reference_nan_keeps_validity_and_sort_class() -> None:
    nan = float("nan")
    assert valid_nan(reduce_bag(Reducer("sum"), _bag(nan))[0])
    assert reduce_bag(Reducer("count"), _bag(nan)) == (1, None)
    result = observe(
        {"x": Branch(Reducer("sum"), {(1,): _bag(nan), (2,): _bag(None)})},
        {"value": Read("x")},
        Scope("grouped"),
    )
    predicate = Unary("not", Binary("lt", Read("value"), LiteralValue(0)))
    assert where(result, predicate).keys == ((1,),)
    scores = Column.from_values({(1,): nan, (2,): None, (3,): -inf, (4,): inf, (5,): inf})
    frame = Result(((2,), (5,), (1,), (4,), (3,)), {}, {"score": scores})
    assert top(frame, 5, "score").keys == ((4,), (5,), (3,), (1,), (2,))
    assert top(frame, 5, "score", descending=False).keys == ((3,), (4,), (5,), (1,), (2,))
    assert top(top(frame, 3, "score"), 2, "score").keys == top(frame, 2, "score").keys


def test_reference_population_positions_and_shared_fanout() -> None:
    fact = Occurrence(("order",), 100)
    tags = {fact.identity: (("sale",), ("first",), ("sale",))}
    grouped = membership((fact,), (tags,))
    assert {key: reduce_bag(Reducer("sum"), rows)[0] for key, rows in grouped.items()} == {
        ("sale",): 100,
        ("first",): 100,
    }
    assert in_population((fact,), (("sale",), ("first",)), (tags,)) == (fact,)
    joint = {fact.identity: (("A", "X"), ("B", "Y"))}
    requested = (("A", "Y"),)
    assert in_population((fact,), requested, (joint,)) == ()
    independent = ({fact.identity: (("A",), ("B",))}, {fact.identity: (("X",), ("Y",))})
    assert in_population((fact,), requested, independent) == (fact,)
    assert in_population((fact,), (), independent) == ()


def test_reference_classification_and_left_totality() -> None:
    source = (("A",), ("B",))
    relation: dict[Key, tuple[Key, ...]] = {("A",): ((1,),)}
    image = tuple(value for values in relation.values() for value in values)
    assert preimage(source, relation, image) == (("A",),)
    assert classify(source, relation, width=1, identity=False) == (
        (("A", 1), ("B", None)),
        frozenset(),
    )
    assert classify(source, relation, width=1, identity=True) == (
        (("A", 1),),
        frozenset({Obligation("missing_identity", ("B",))}),
    )
    assert compose(relation, {(1,): (("X",), ("X",))}) == {("A",): (("X",),)}
    total = {**relation, ("B",): ((1,),)}
    assert preimage(source, total, image) == source


def test_reference_ordered_pair_and_window_boundaries() -> None:
    duplicated = (Occurrence((1,), None, time=2), Occurrence((2,), 5, time=2))
    assert reduce_bag(Reducer("first"), duplicated) == (None, "duplicate_order_key")
    entries = (Occurrence((1,), None, time=1), Occurrence((2,), 5, time=2))
    assert reduce_bag(Reducer("first", include_null=True), entries) == (None, None)
    assert reduce_bag(Reducer("first"), entries) == (5, None)
    assert reduce_bag(Reducer("last"), entries) == (5, None)
    paired = (Occurrence((1,), 2, 3), Occurrence((2,), None, 6), Occurrence((3,), 8, -1))
    assert reduce_bag(Reducer("weighted_mean"), paired) == (-1, None)
    cancelling = (Occurrence((1,), 2, 1), Occurrence((2,), 4, -1))
    assert reduce_bag(Reducer("weighted_mean"), cancelling) == (None, None)
    facts = (Occurrence((1,), 1, time=1), Occurrence((2,), 3, time=2))
    all_history = window_slices(facts, {(1,): (None, 2), (2,): (None, 3)})
    history_average = per({(): all_history}, Reducer("sum"), Reducer("mean"))
    actual_days = window_slices(facts, {(1,): (1, 2), (2,): (2, 3)})
    daily_average = per({(): actual_days}, Reducer("sum"), Reducer("mean"))
    assert _column(history_average) == (Fraction(5, 2),)
    assert _column(daily_average) == (2,)


def test_reference_frame_presence_and_noncommuting_partitions() -> None:
    left = Result(
        ((1,), (2,)),
        {},
        {"value": Column.from_values({(1,): 1, (2,): 3})},
        presence={"previous": frozenset({(1,)})},
    )
    right = Result(((2,), (3,)), {}, {"value": Column.from_values({(2,): None, (3,): 0})})
    paired = align(left, right)
    assert paired.presence["left.previous"] == {(1,)}
    assert paired.presence["right_present"] == {(2,), (3,)}
    assert _column(paired, "right.value") == (None, None, 0)
    assert fix(paired, 0, 2).presence["right_present"] == {()}
    assert not push_forward(paired, (), {"value": (Reducer("sum"), "left.value")}).presence
    empty = top(left, 0, "value")
    assert over(empty, (), Reducer("sum"), "value").keys == ()
    assert push_forward(empty, (), {"value": (Reducer("row_count"), "value")}).keys == ((),)
    total = over(left, (), Reducer("sum"), "value")
    head = top(left, 1, "value", descending=False)
    assert _column(top(total, 1, "value", descending=False)) == (4,)
    assert _column(over(head, (), Reducer("sum"), "value")) == (1,)


def test_reference_comparison_requires_complete_unambiguous_time_mapping() -> None:
    left = Result(((2,),), {}, {"value": Column.from_values({(2,): 3})})
    prior = Result(((1,), (3,)), {}, {"value": Column.from_values({(1,): 2, (3,): 0})})
    times = frozenset({2, 4})
    valid = compare(left, prior, {1: 2, 3: 4}, 0, current_times=times, mode="relative")
    assert valid.keys == ((2,), (4,))
    assert _column(valid, "relative.value") == (Fraction(1, 2), None)
    assert valid.presence["right_present"] == {(2,), (4,)}
    missing = compare(left, prior, {1: 2}, 0, current_times=times, mode="absolute")
    assert missing.witness("invalid_correspondence") == (3,)
    outside = compare(left, prior, {1: 2, 3: 99}, 0, current_times=times, mode="absolute")
    assert outside.witness("invalid_correspondence") == (3,)

    current = Result((("a", 2), ("b", 2)), {}, {})
    baseline = Result((("a", 1), ("b", 3)), {}, {})
    collided = compare(current, baseline, {1: 2, 3: 2}, 1, current_times=times, mode="absolute")
    assert collided.obligations == {
        Obligation("invalid_correspondence", (1,)),
        Obligation("invalid_correspondence", (3,)),
    }
    unoccupied = Result((("a", 1),), {}, {})
    invalid_mappings: tuple[Mapping[Value, Value], ...] = ({1: 2, 3: 2}, {1: 2, 3: 99})
    for mapping in invalid_mappings:
        rejected = compare(current, unoccupied, mapping, 1, current_times=times, mode="absolute")
        assert Obligation("invalid_correspondence", (3,)) in rejected.obligations


def test_reference_finite_correlation_survives_extreme_input_scales() -> None:
    line = (
        Occurrence((1,), 1e308, weight=1),
        Occurrence((2,), -1e308, weight=-1),
        Occurrence((3,), 0, weight=0),
    )
    assert reduce_bag(Reducer("corr"), line) == (1.0, None)


@pytest.mark.parametrize(
    "left, right, coefficient",
    (
        ((float("nan"), 2), (1, 1), None),
        ((inf, inf), (1, 2), None),
        ((float("nan"), 1, 2), (7, 1, 2), 1.0),
        ((float("nan"), 1, 2), (7, 2, 1), -1.0),
    ),
)
def test_reference_correlation_retains_native_pair_validity(
    left: tuple[float, ...], right: tuple[float, ...], coefficient: float | None
) -> None:
    pairs = tuple(
        Occurrence((index,), x, weight=y)
        for index, (x, y) in enumerate(zip(left, right, strict=True))
    )
    assert reduce_bag(Reducer("corr"), pairs) == (coefficient, None)


def test_full_witness_decode_keeps_date_and_decimal_coordinates() -> None:
    received = "coordinates=[{'key.0': datetime.date(2026, 1, 1), 'key.1': Decimal('2.50')}]"
    assert _witnesses(received) == ((date(2026, 1, 1), Decimal("2.50")),)


def _check_three_paths(expected: Result, query: mv.AnalysisFrame) -> None:
    control = run_frame(query, OptimizationPolicy.disabled())
    optimized = run_frame(query, OptimizationPolicy.default())
    assert_equivalent(expected, control)
    assert_equivalent(expected, optimized)
    assert_policies_agree(control, optimized)


def _trace_three_paths(
    expected: Result, query: mv.AnalysisFrame, monkeypatch: pytest.MonkeyPatch
) -> tuple[SourceTrace, SourceTrace]:
    with monkeypatch.context() as patch:
        optimized = capture_source(patch)
        actual = run_frame(query, OptimizationPolicy.default())
    with monkeypatch.context() as patch:
        control = capture_source(patch)
        plain = run_frame(query, OptimizationPolicy.disabled())
    assert_equivalent(expected, actual)
    assert_equivalent(expected, plain)
    assert_policies_agree(actual, plain)
    return optimized, control


@pytest.fixture(scope="module")
def observation_source(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, mv.Session]:
    root = tmp_path_factory.mktemp("reference-source")
    return root, open_source(root, ())


def _check_observation(case: ObservationCase, source: tuple[Path, mv.Session]) -> None:
    branches, scope = branch_inputs(case)
    expected = observe(branches, dict(case.outputs), scope)
    root, session = source
    replace_facts(root, case.facts)
    _check_three_paths(expected, observe_case(session, case))


@given(case=observations("grouped"))
@example(
    case=ObservationCase(
        (
            Fact(1, None, 0, 0, None, None),
            Fact(2, None, 1, 0, None, None),
            Fact(3, 0, 0, 0, None, None),
        ),
        (BranchSpec("left", "amount", lane=1), BranchSpec("right", "extra")),
        (("result", Binary("add", Read("left"), Read("right"))),),
    )
)
def test_grouped_observation_grammar(
    observation_source: tuple[Path, mv.Session], case: ObservationCase
) -> None:
    _check_observation(case, observation_source)


@given(case=observations("calendar"))
def test_sparse_calendar_observation_grammar(
    observation_source: tuple[Path, mv.Session], case: ObservationCase
) -> None:
    _check_observation(case, observation_source)


@given(case=observations("explicit"))
@example(
    case=ObservationCase(
        (Fact(1, None, 1, 0, 0, None), Fact(2, 2, 2, 0, None, None)),
        (BranchSpec("left", "amount", lane=1), BranchSpec("right", "amount", lane=0)),
        (("total", Binary("add", Read("left"), Read("right"))),),
        mode="explicit",
        population=(None,),
    )
)
def test_explicit_population_coverage_after_fact_predicates(
    observation_source: tuple[Path, mv.Session], case: ObservationCase
) -> None:
    _check_observation(case, observation_source)


@given(case=demand_cases())
def test_strict_metric_branch_demand(
    observation_source: tuple[Path, mv.Session], case: ObservationCase
) -> None:
    _check_observation(case, observation_source)


def test_strict_calendar_failure_keeps_complete_timestamp_witness(
    observation_source: tuple[Path, mv.Session],
) -> None:
    case = ObservationCase(
        (Fact(1, 2, 0, 0, None, None), Fact(2, 2, 0, 1, None, None)),
        (BranchSpec("strict", "amount", strict=True, timed=True),),
        (("value", Read("strict")),),
        mode="calendar",
    )
    _check_observation(case, observation_source)


def _check_sample(case: SampleCase, source: tuple[Path, mv.Session]) -> None:
    inputs, domain = sample_inputs(case)
    expected = per(inputs, Reducer("sum"), Reducer(case.outer), domain)
    root, session = source
    replace_facts(root, case.facts)
    _check_three_paths(expected, observe_samples(session, case))


@given(case=samples())
@example(case=SampleCase((Fact(1, None, 0, 0, 7, None),), "mean"))
def test_implicit_sample_completion(
    observation_source: tuple[Path, mv.Session], case: SampleCase
) -> None:
    _check_sample(case, observation_source)


@given(case=samples(explicit=True))
def test_explicit_sample_completion(
    observation_source: tuple[Path, mv.Session], case: SampleCase
) -> None:
    _check_sample(case, observation_source)


@pytest.mark.parametrize("retention", [False, True])
def test_ordered_native_samples_three_paths(tmp_path: Path, retention: bool) -> None:
    facts = (
        Fact(1, 1, 0, 0, None, None),
        Fact(2, 1, 0, 1, 2, None),
        Fact(3, 1, 0, 1, 3, None),
        Fact(4, 1, 0, 3, 8, None),
    )
    session = open_source(tmp_path, facts)
    # Complete duplicate facts within the same actual day before first/last.
    daily = tuple(Occurrence((day,), value, time=day) for day, value in ((0, None), (1, 5), (3, 8)))
    first = Reducer("first", include_null=retention)
    expected = observe(
        {"samples": Branch(first, {(1,): daily})}, {"value": Read("samples")}, Scope("grouped")
    )
    _check_three_paths(expected, observe_ordered_samples(session, "first", include_null=retention))
    last = Reducer("last", include_null=retention)
    expected = observe(
        {"samples": Branch(last, {(1,): daily})}, {"value": Read("samples")}, Scope("grouped")
    )
    _check_three_paths(expected, observe_ordered_samples(session, "last", include_null=retention))


def _fixed_score_result(facts: tuple[Fact, ...]) -> Result:
    return Result(
        tuple((fact.identity,) for fact in facts),
        {},
        {"score": Column.from_values({(fact.identity,): fact.score for fact in facts})},
    )


def test_fixed_frame_where_and_top_keep_nan_and_na_distinct(tmp_path: Path) -> None:
    facts = tuple(
        Fact(index, None, 0, None, None, None, value)
        for index, value in enumerate((None, float("nan"), -inf, 10.0, inf, 10.0), start=1)
    )
    session = open_source(tmp_path, facts)
    fixed = read_scores(session).compute()
    reference = _fixed_score_result(facts)
    _check_three_paths(top(reference, 6, "score"), fixed.top(6, by=fixed["score"]))
    predicate = Unary("not", Binary("lt", Read("score"), LiteralValue(0)))
    filtered = fixed.where(~(fixed["score"] < 0))
    _check_three_paths(
        top(where(reference, predicate), 4, "score"),
        filtered.top(4, by=filtered["score"]),
    )


def test_fixed_outer_alignment_keeps_value_validity_and_presence(tmp_path: Path) -> None:
    facts = tuple(
        Fact(index, None, index % 2, None, None, None, score)
        for index, score in enumerate((10.0, float("nan"), None, 0.0), start=1)
    )
    session = open_source(tmp_path, facts)
    sample = read_scores(session)
    left = sample.where(sample.coord(sample.axes[0]) <= 2).compute()
    right = sample.where(sample.coord(sample.axes[0]) >= 2).compute()
    _check_three_paths(
        align(_fixed_score_result(facts[:2]), _fixed_score_result(facts[1:])),
        left.align(right),
    )


@given(
    scores=st.lists(st.sampled_from((None, float("nan"), -inf, -2.0, 0.0, 3.0, inf)), max_size=7),
    left_count=st.integers(0, 7),
    right_start=st.integers(0, 7),
    limit=st.integers(0, 7),
    descending=st.booleans(),
)
def test_generated_fixed_scoring_and_alignment(
    observation_source: tuple[Path, mv.Session],
    scores: list[float | None],
    left_count: int,
    right_start: int,
    limit: int,
    descending: bool,
) -> None:
    root, session = observation_source
    facts = tuple(
        Fact(index, None, 0, None, None, None, score) for index, score in enumerate(scores, start=1)
    )
    replace_facts(root, facts)
    fixed = read_scores(session).compute()
    expected = top(_fixed_score_result(facts), limit, "score", descending=descending)
    split = len(facts) // 2
    members = tuple(
        top(_fixed_score_result(part), limit, "score", descending=descending)
        for part in (facts[:split], facts[split:])
    )
    partial = Result(
        tuple(key for member in members for key in member.keys),
        {},
        {
            "score": Column(
                {
                    key: value
                    for member in members
                    for key, value in member.columns["score"].x.items()
                }
            )
        },
    )
    assert top(partial, limit, "score", descending=descending).keys == expected.keys
    _check_three_paths(
        expected,
        fixed.top(limit, by=fixed["score"], descending=descending),
    )
    key = fixed.coord(fixed.axes[0])
    left = fixed.where(key <= left_count)
    right = fixed.where(key > right_start)
    _check_three_paths(
        align(_fixed_score_result(facts[:left_count]), _fixed_score_result(facts[right_start:])),
        left.align(right),
    )


def test_pending_integer_top_issued_sql_differs_with_unchanged_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = tuple(
        Fact(index, None, 0, None, score, None, score=float(score))
        for index, score in enumerate((1, 50, 30, 50), start=1)
    )
    session = open_source(tmp_path, facts)
    source = read_scores(session, field="amount")
    query = source.top(1, by=source["score"])
    expected = top(_fixed_score_result(facts), 1, "score")
    optimized, control = _trace_three_paths(expected, query, monkeypatch)
    assert any(
        "limit 1" in sql.lower() and "__frame_top_present" in sql for sql in optimized.native_sql
    ), optimized.native_sql
    assert all("__frame_top_present" not in sql for sql in control.native_sql), control.native_sql


def test_common_source_and_terminal_relation_preserve_values_and_na(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = (
        Fact(1, 2, 0, 0, 11, 2),
        Fact(2, 0, 0, 0, None, 3),
        Fact(3, 2, 1, 0, 17, None),
        Fact(4, None, 0, 0, None, None),
    )
    case = ObservationCase(
        facts,
        (BranchSpec("total", "amount"), BranchSpec("other", "extra")),
        (("total", Read("total")), ("other", Read("other"))),
    )
    branches, scope = branch_inputs(case)
    expected = observe(branches, dict(case.outputs), scope)
    query = observe_case(open_source(tmp_path, facts), case)
    optimized, control = _trace_three_paths(expected, query, monkeypatch)
    assert len(optimized.native_sql) == 1
    assert len(control.native_sql) == 2
    with monkeypatch.context() as patch:
        common = capture_source(patch)
        actual = run_frame(
            query, OptimizationPolicy.disabled().with_rule("source_merge", enabled=True)
        )
    assert len(common.native_sql) == 1, common.native_sql
    assert_equivalent(expected, actual)
    with monkeypatch.context() as patch:
        terminal = capture_source(patch)
        actual = run_frame(
            query, OptimizationPolicy.disabled().with_rule("source_terminal_fusion", enabled=True)
        )
    assert len(terminal.native_sql) == 1, terminal.native_sql
    assert_equivalent(expected, actual)


def test_shared_sample_folds_preserve_each_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = (
        Fact(1, 0, 0, 0, 3, None),
        Fact(2, 0, 0, 0, 5, None),
        Fact(3, 0, 1, 0, None, None),
        Fact(4, 0, 2, 0, 4, None),
    )
    session = open_source(tmp_path, facts)
    group = catalog_value(session.catalog, "trial.facts.group_id", kind="dimension")
    lane = catalog_value(session.catalog, "trial.facts.lane", kind="dimension")
    sample = source_metric(session, BranchSpec("amount", "amount")).per(lane)
    query = session.observe({"value": sample.mean(), "total": sample.sum()}, by=(group,))
    inputs, domain = sample_inputs(SampleCase(facts, "mean"))
    average = per(inputs, Reducer("sum"), Reducer("mean"), domain)
    total = per(inputs, Reducer("sum"), Reducer("sum"), domain)
    expected = Result(
        average.keys, {}, {"value": average.columns["value"], "total": total.columns["value"]}
    )
    optimized, control = _trace_three_paths(expected, query, monkeypatch)
    assert len(optimized.native_sql) == 1
    assert len(control.native_sql) == 2


def test_source_member_counts_keep_complete_tuples_and_null_categories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = (
        Fact(1, 1, 0, 0, 10, None),
        Fact(2, 1, 0, 0, 10, None),
        Fact(3, None, 0, 0, 10, None),
        Fact(4, None, 0, 0, 10, None),
        Fact(5, None, 0, 0, 10, None),
    )
    session = open_source(tmp_path, facts)
    entity = catalog_value(session.catalog, "trial.facts", kind="entity")
    category = catalog_value(session.catalog, "trial.facts.group_id", kind="dimension")
    population = session.members(entity).classify(by=(mv.identity(entity), category))
    query = session.observe({"value": population.count()}, by=(category,))
    expected = Result(((1,), (None,)), {}, {"value": Column.from_values({(1,): 2, (None,): 3})})
    off = OptimizationPolicy.disabled()
    for policy in (off, off.with_rule("source_member_count", enabled=True)):
        with monkeypatch.context() as patch:
            trace = capture_source(patch)
            assert_equivalent(expected, run_frame(query, policy))
        if policy is off:
            assert sum(trace.returned_rows) >= len(facts)
        else:
            assert trace.returned_rows == [2]
            assert "COUNT" in trace.native_sql[0].upper()


def test_native_population_check_fusion_keeps_complete_selected_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = (
        Fact(1, 0, 0, 0, 8, 5),
        Fact(2, None, 0, 0, 12, -7),
        Fact(3, 0, 1, 0, 9, 8),
        Fact(4, 1, 0, 0, 21, 3),
    )
    session = open_source(tmp_path, facts)
    entity = catalog_value(session.catalog, "trial.facts", kind="entity")
    amount = catalog_value(session.catalog, "trial.facts.amount", kind="measure")
    extra = source_metric(session, BranchSpec("extra", "extra"))
    keys = session.members(entity, where=amount < 14)
    query = session.observe({"value": extra.where(mv.in_population(keys))})
    expected = observe(
        {
            "value": Branch(
                Reducer("sum"),
                {
                    (): tuple(
                        Occurrence((f.identity,), f.extra)
                        for f in facts
                        if f.amount is not None and f.amount < 14
                    )
                },
            )
        },
        {"value": Read("value")},
        Scope("scalar"),
    )
    optimized, control = _trace_three_paths(expected, query, monkeypatch)
    assert len(optimized.native_sql) == 1 and optimized.uploaded_key_rows == []
    assert len(control.native_sql) == 2 and control.uploaded_key_rows == []
    with monkeypatch.context() as patch:
        check_fusion = capture_source(patch)
        actual = run_frame(
            query,
            OptimizationPolicy.disabled().with_rule("source_population_check_fusion", enabled=True),
        )
    assert len(check_fusion.native_sql) == 1, check_fusion.native_sql
    assert_equivalent(expected, actual)

    # A check-only producer is still obligated even when its keys are inlined.
    backend = ibis.duckdb.connect(tmp_path / "facts.duckdb", threads=1)
    try:
        backend.raw_sql("INSERT INTO facts (id, amount, extra) VALUES (NULL, 12, 200)")
    finally:
        backend.disconnect()
    for policy in (OptimizationPolicy.default(), OptimizationPolicy.disabled()):
        with pytest.raises(MaterializationError) as failure:
            compute_with_policy(query, policy)
        assert failure.value.stage == "population_identity"
        assert failure.value.expected == "non-null complete identity keys"


def test_joint_membership_matches_once_per_complete_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    session = membership_session(semantic_project_factory, tmp_path, monkeypatch)
    catalog = session.catalog
    metric = catalog_value(catalog, "sales.revenue", kind="metric")
    to_link = catalog_value(catalog, "sales.order_links", kind="relationship")
    to_tag = catalog_value(catalog, "sales.link_tags", kind="relationship")
    links = mv.identity(mv.path(to_link))
    tags = mv.identity(mv.path(to_link, to_tag))
    allowed = mv.coordinates(by=(links, tags), keys=(((1,), (1,)), ((2,), (1,)), ((4,), (2,))))
    predicate = mv.in_population(allowed)
    rows = (
        ((1,), (1,), (1,), 10),
        ((1,), (2,), (1,), 10),
        ((1,), (3,), (2,), 10),
        ((2,), (4,), (1,), 30),
    )
    selected = {(1,), (2,)}
    witnesses = {fact: value for fact, link, tag, value in rows if link in selected and tag == (1,)}
    fact_bag = {(1,): 10, (2,): 30, (3,): 100}
    expected = observe(
        {
            "matched": Branch(
                Reducer("sum"), {(): tuple(Occurrence(k, v) for k, v in witnesses.items())}
            ),
            "other": Branch(
                Reducer("sum"),
                {(): tuple(Occurrence(k, v) for k, v in fact_bag.items() if k not in witnesses)},
            ),
        },
        {"matched": Read("matched"), "other": Read("other")},
        Scope("scalar"),
    )
    _check_three_paths(
        expected,
        session.observe({"matched": metric.where(predicate), "other": metric.where(~predicate)}),
    )


def test_fixed_frame_selects_both_masks_under_one_qualified_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = tuple(
        Fact(index, None, 0, None, score, None, score=score)
        for index, score in enumerate((None, 1, -1, 50, 2), start=1)
    )
    session = open_source(tmp_path, facts)
    fixed = read_scores(session, field="amount").compute()
    first = fixed.where(fixed["score"] < 30)
    query = first.where(first["score"] >= 0)
    expected = where(
        where(_fixed_score_result(facts), Binary("lt", Read("score"), LiteralValue(30))),
        Unary("not", Binary("lt", Read("score"), LiteralValue(0))),
    )
    actual_steps: list[int] = []

    def trace_fusion(stage: FrameCompute, schema: DataSchema | None) -> tuple[FrameCompute, ...]:
        replay = frame_filter_steps(stage, schema)
        assert stage.filter_fusion is not None and len(stage.filter_fusion.fallback) == 2
        actual_steps.append(len(replay))
        return replay

    monkeypatch.setattr(graph_source_execution, "frame_filter_steps", trace_fusion)
    native, control = _trace_three_paths(expected, query, monkeypatch)
    assert actual_steps == [1]
    assert not native.native_sql and not control.native_sql


def test_time_comparison_uses_complete_correspondence_between_fixed_calendars(
    tmp_path: Path,
) -> None:
    values = (2, 0, None, 10, 15, None)
    facts = tuple(Fact(day + 1, None, 0, day, value, None) for day, value in enumerate(values))
    session = open_source(tmp_path, facts)
    metric = source_metric(session, BranchSpec("value", "amount", timed=True))
    days = session.time_scope(start=calendar_day(3), end=calendar_day(6)).by(mv.grain("day"))
    earlier = session.time_scope(start=calendar_day(0), end=calendar_day(3)).by(mv.grain("day"))
    current = session.observe({"value": metric}, by=(days,)).compute()
    baseline = session.observe({"value": metric}, by=(earlier,)).compute()

    def daily(start: int) -> Result:
        keys = tuple((calendar_day(day),) for day in range(start, start + 3))
        column = Column.from_values(dict(zip(keys, values[start : start + 3], strict=True)))
        return Result(keys, {}, {"value": column})

    now, prior = daily(3), daily(0)
    mapping: dict[Value, Value] = {calendar_day(day): calendar_day(day + 3) for day in range(3)}
    destination = frozenset(mapping.values())
    absolute = compare(now, prior, mapping, 0, current_times=destination, mode="absolute")
    relative = compare(now, prior, mapping, 0, current_times=destination, mode="relative")
    expected = replace(
        absolute,
        columns={
            "value": absolute.columns["left.value"],
            "reference.baseline.value": absolute.columns["right.value"],
            "reference.absolute.value": absolute.columns["absolute.value"],
            "reference.relative.value": relative.columns["relative.value"],
        },
        presence={
            "current.present": absolute.presence["left_present"],
            "reference.present": absolute.presence["right_present"],
        },
    )
    _check_three_paths(expected, current.compare(baseline, match=mv.ordinal()))
