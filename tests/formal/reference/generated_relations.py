"""Adversarial generated relation, demand and fixed-expression programs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import ibis
import pandas as pd
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

import marivo.analysis as mv
from marivo._definition.value_ir import ValuePartition
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.methods import query_values
from marivo.analysis.methods.frame_evaluation import FrameEvaluation
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_population_membership import _session as membership_session
from tests.formal.reference.bridge import observe_case, replace_facts, source_metric
from tests.formal.reference.equivalence import assert_equivalent, assert_policies_agree, run_frame
from tests.formal.reference.evaluator import compare, observe, over, select, top
from tests.formal.reference.generators import (
    BranchSpec,
    Fact,
    ObservationCase,
    branch_inputs,
    calendar_day,
)
from tests.formal.reference.model import (
    Bag,
    Binary,
    Branch,
    Column,
    Key,
    Occurrence,
    Read,
    Reducer,
    Result,
    Scope,
    Value,
    observation_key_rank,
)
from tests.formal.reference.relations import Relation, in_population
from tests.shared_fixtures import catalog_value


def _check(expected: Result, query: mv.AnalysisFrame) -> None:
    control = run_frame(query, OptimizationPolicy.disabled())
    optimized = run_frame(query, OptimizationPolicy.default())
    assert_equivalent(expected, control)
    assert_equivalent(expected, optimized)
    assert_policies_agree(control, optimized)


@pytest.fixture
def generated_membership_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> tuple[Path, mv.Session]:
    session = membership_session(semantic_project_factory, tmp_path, monkeypatch)
    return tmp_path / "population.duckdb", session


def _reload_relationships(
    path: Path,
    amounts: tuple[int, int, int],
    links: tuple[tuple[int, int, int], ...],
) -> None:
    backend = ibis.duckdb.connect(path, threads=1)
    try:
        orders = ", ".join(str((key, key, amount)) for key, amount in enumerate(amounts, start=1))
        encoded_links = ", ".join(str(link) for link in links)
        backend.raw_sql(
            "BEGIN TRANSACTION; DELETE FROM links; DELETE FROM orders; "
            f"INSERT INTO orders VALUES {orders}; INSERT INTO links VALUES {encoded_links}; COMMIT"
        )
    finally:
        backend.disconnect()


@settings(suppress_health_check=(HealthCheck.function_scoped_fixture,))
@given(
    amounts=st.tuples(*(st.integers(1, 15) for _ in range(3))),
    repeated_matches=st.integers(1, 3),
    left_total=st.booleans(),
)
@example(amounts=(6, 3, 4), repeated_matches=2, left_total=False)
def test_generated_joint_product_membership(
    generated_membership_source: tuple[Path, mv.Session],
    amounts: tuple[int, int, int],
    repeated_matches: int,
    left_total: bool,
) -> None:
    # Order one hits both selected marginals through distinct links. Order two
    # hits the joint key on every matching link and contributes exactly once.
    distinct_links = ((1, 1, 2), (2, 1, 1))
    repeated = tuple((3 + index, 2, 1) for index in range(repeated_matches))
    third = ((6, 3, 2),) if left_total else ()
    links = (*distinct_links, *repeated, *third)
    selected_links = (1, *(link for link, _, _ in repeated))
    fact_ids = (1, 2, 3)
    bag: Bag = tuple(
        Occurrence((key,), value) for key, value in zip(fact_ids, amounts, strict=True)
    )
    joint: Relation = {
        (key,): tuple((link, tag) for link, order, tag in links if order == key) for key in fact_ids
    }
    link_image: Relation = {
        (key,): tuple((link,) for link, order, _ in links if order == key) for key in fact_ids
    }
    tag_image: Relation = {
        (key,): tuple((tag,) for _, order, tag in links if order == key) for key in fact_ids
    }
    target = tuple((link, 1) for link in selected_links)
    reachable = tuple((tag,) for tag in sorted({tag for _, _, tag in links}))
    matched = in_population(bag, target, (joint,))
    separate = in_population(bag, target, (link_image, tag_image))
    round_tripped = in_population(bag, reachable, (tag_image,))
    assert tuple(row.identity for row in matched) == ((2,),)
    assert tuple(row.identity for row in separate) == ((1,), (2,))
    assert (round_tripped == bag) is left_total
    expected = observe(
        {
            name: Branch(Reducer("sum"), {(): selected})
            for name, selected in (("joint", matched), ("round_trip", round_tripped))
        },
        {name: Read(name) for name in ("joint", "round_trip")},
        Scope("scalar"),
    )

    path, session = generated_membership_source
    _reload_relationships(path, amounts, links)
    catalog = session.catalog
    metric = catalog_value(catalog, "sales.revenue", kind="metric")
    to_link = catalog_value(catalog, "sales.order_links", kind="relationship")
    to_tag = catalog_value(catalog, "sales.link_tags", kind="relationship")
    link_axis = mv.identity(mv.path(to_link))
    tag_axis = mv.identity(mv.path(to_link, to_tag))
    link_population = mv.coordinates(
        by=(link_axis,), keys=tuple(((link,),) for link in selected_links)
    )
    tag_population = mv.coordinates(by=(tag_axis,), keys=(((1,),),))
    image = mv.coordinates(by=(tag_axis,), keys=tuple((key,) for key in reachable))
    _check(
        expected,
        session.observe(
            {
                "joint": metric.where(
                    mv.in_population(mv.product(link_population, tag_population))
                ),
                "round_trip": metric.where(mv.in_population(image)),
            }
        ),
    )


@given(
    group=st.integers(-3, 3),
    amount=st.integers(-3, 5),
    extra=st.integers(-2, 5),
    missing=st.booleans(),
)
@example(group=2, amount=4, extra=1, missing=True)
def test_generated_upstream_strict_failure_survives_top_zero(
    observation_source: tuple[Path, mv.Session], group: int, amount: int, extra: int, missing: bool
) -> None:
    root, session = observation_source
    facts = (
        Fact(1, group, 0, 0, amount, None if missing else extra),
        Fact(2, group + 1, 1, 0, 1, extra),
    )
    case = ObservationCase(
        facts,
        (BranchSpec("safe", "amount"), BranchSpec("checked", "extra", strict=True)),
        (("safe", Read("safe")), ("checked", Read("checked"))),
    )
    branches, scope = branch_inputs(case)
    expected = top(
        select(observe(branches, dict(case.outputs), scope), {"safe": Read("safe")}), 0, "safe"
    )
    assert (expected.witness("strict_null") == (group,)) is missing
    assert expected.keys == ()
    replace_facts(root, facts)
    pending = observe_case(session, case)
    dropped = pending.select({"safe": pending["safe"]})
    _check(expected, dropped.top(0, by=dropped["safe"]))


@given(values=st.lists(st.integers(-4, 5), min_size=1, max_size=6))
def test_generated_fixed_repeated_partition_expression(
    observation_source: tuple[Path, mv.Session], values: list[int]
) -> None:
    _, session = observation_source
    group = catalog_value(session.catalog, "trial.facts.group_id", kind="dimension")
    rows = tuple(((index,), {"value": value}) for index, value in enumerate(values))
    fixed = session.values(by=(group,), columns={"value": None}, rows=rows).compute()
    keys = tuple((index,) for index in range(len(values)))
    captured = Result(keys, {}, {"value": Column.from_values(dict(zip(keys, values, strict=True)))})
    total = over(captured, (), Reducer("sum"), "value").columns["value"]
    joint = Result(keys, {}, {"value": captured.columns["value"], "total": total})
    expected = select(
        joint, {"total": Read("total"), "with_value": Binary("add", Read("total"), Read("value"))}
    )
    partition = fixed.over(fixed["value"].sum())
    pending = fixed.select({"total": partition, "with_value": partition + fixed["value"]})
    evaluate = query_values.partition_series
    calls: list[ValuePartition] = []

    def counted(
        expression: ValuePartition,
        rows: pd.DataFrame,
        program: FrameProgram,
        evaluation: FrameEvaluation,
    ) -> pd.Series:
        calls.append(expression)
        return evaluate(expression, rows, program, evaluation)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(query_values, "partition_series", counted)
        control = run_frame(pending, OptimizationPolicy.disabled())
        assert len(calls) == 2 and calls[0] == calls[1]
        calls.clear()
        optimized = run_frame(pending, OptimizationPolicy.default())
        assert len(calls) == 1
    assert_equivalent(expected, control)
    assert_equivalent(expected, optimized)
    assert_policies_agree(control, optimized)


def _daily_frame(facts: tuple[Fact, ...]) -> Result:
    values: dict[Key, Value] = {}
    for fact in facts:
        assert fact.group is not None and fact.day is not None
        values[fact.group, calendar_day(fact.day)] = fact.amount
    assert len(values) == len(facts)
    return Result(
        tuple(sorted(values, key=observation_key_rank)), {}, {"value": Column.from_values(values)}
    )


_TIME_AMOUNT = st.one_of(st.none(), st.integers(-4, 5))


@given(
    group=st.integers(-3, 3),
    before=st.tuples(_TIME_AMOUNT, _TIME_AMOUNT),
    after=st.tuples(_TIME_AMOUNT, _TIME_AMOUNT, _TIME_AMOUNT),
    partial=st.booleans(),
    ordinal=st.booleans(),
    unoccupied=st.booleans(),
)
@example(group=0, before=(7, 2), after=(9, 11, 5), partial=True, ordinal=False, unoccupied=True)
def test_generated_partial_time_comparison(
    observation_source: tuple[Path, mv.Session],
    group: int,
    before: tuple[int | None, int | None],
    after: tuple[int | None, int | None, int | None],
    partial: bool,
    ordinal: bool,
    unoccupied: bool,
) -> None:
    baseline_facts = (
        Fact(1, group + 1, 0, 0, before[0], None),
        Fact(2, group, 0, 1, before[1], None),
    )
    current_facts: tuple[Fact, ...] = (
        Fact(3, group, 0, 3, after[0], None),
        Fact(4, group, 0, 4, after[1], None),
    )
    if unoccupied:
        current_facts = current_facts[1:]
    if partial:
        current_facts = (*current_facts, Fact(5, group + 2, 0, 5, after[2], None))
    now, prior = _daily_frame(current_facts), _daily_frame(baseline_facts)
    correspondence: dict[Value, Value] = {
        calendar_day(day): calendar_day(day + 3) for day in range(2)
    }
    current_end = 6 if partial else 5
    current_times = frozenset(calendar_day(day) for day in range(3, current_end))
    absolute = compare(now, prior, correspondence, 1, current_times=current_times, mode="absolute")
    relative = compare(now, prior, correspondence, 1, current_times=current_times, mode="relative")
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
    baseline_only = (group + 1, calendar_day(3))
    assert (
        baseline_only
        in expected.presence["reference.present"] - expected.presence["current.present"]
    )
    if partial:
        unmatched_day = (group + 2, calendar_day(5))
        assert (
            unmatched_day
            in expected.presence["current.present"] - expected.presence["reference.present"]
        )

    root, session = observation_source
    replace_facts(root, (*baseline_facts, *current_facts))
    coordinate = catalog_value(session.catalog, "trial.facts.group_id", kind="dimension")
    metric = source_metric(session, BranchSpec("value", "amount", timed=True))
    grain = mv.grain("day")

    def input_frame(start: int, end: int) -> mv.AnalysisFrame:
        scope = session.time_scope(start=calendar_day(start), end=calendar_day(end))
        return session.observe({"value": metric}, by=(coordinate, scope.by(grain))).compute()

    current, baseline = input_frame(3, current_end), input_frame(0, 2)
    rule = mv.ordinal() if ordinal else mv.offset(periods=-3)
    _check(expected, current.compare(baseline, match=rule))
