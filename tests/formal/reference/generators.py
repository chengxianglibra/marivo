"""Small independent fact bags and well-typed observation grammars for differential tests."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal, TypeAlias

from hypothesis import strategies as st

from tests.formal.reference.model import (
    Bag,
    Binary,
    Branch,
    Coalesce,
    Expression,
    IfElse,
    Key,
    LiteralValue,
    Occurrence,
    Read,
    Reducer,
    Scope,
)

PERIOD_START = date(2026, 1, 1)
PERIOD_DAYS = 4
PERIOD_END = PERIOD_START + timedelta(days=PERIOD_DAYS)
ObservationMode: TypeAlias = Literal["scalar", "grouped", "calendar", "explicit"]


@dataclass(frozen=True, slots=True)
class Fact:
    identity: int
    group: int | None
    lane: int
    day: int | None
    amount: int | None
    extra: int | None
    score: float | None = None


@dataclass(frozen=True, slots=True)
class BranchSpec:
    name: str
    field: Literal["amount", "extra"]
    reducer: Literal["sum", "count", "min", "max", "mean"] = "sum"
    lane: int | None = None
    timed: bool = False
    strict: bool = False


@dataclass(frozen=True, slots=True)
class ObservationCase:
    facts: tuple[Fact, ...]
    branches: tuple[BranchSpec, ...]
    outputs: tuple[tuple[str, Expression], ...]
    mode: ObservationMode = "grouped"
    population: tuple[int | None, ...] = ()


@dataclass(frozen=True, slots=True)
class SampleCase:
    facts: tuple[Fact, ...]
    outer: Literal["sum", "mean", "min", "max"]
    lanes: tuple[int, ...] | None = None


def calendar_day(day: int) -> datetime:
    """Represent bucket instants independently of provider time reduction."""
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)


def branch_inputs(case: ObservationCase) -> tuple[dict[str, Branch], Scope]:
    """Gather raw contribution occurrences for the reference evaluator."""
    branches: dict[str, Branch] = {}
    for spec in case.branches:
        groups: dict[Key, list[Occurrence]] = defaultdict(list)
        for fact in case.facts:
            if spec.lane is not None and fact.lane != spec.lane:
                continue
            timestamp = calendar_day(fact.day) if fact.day is not None else None
            if case.mode == "calendar" and spec.timed:
                if fact.day is None or not 0 <= fact.day < PERIOD_DAYS:
                    continue
                key: Key = (fact.group, timestamp)
            else:
                key = () if case.mode == "scalar" else (fact.group,)
            groups[key].append(
                Occurrence(
                    (fact.identity,),
                    fact.amount if spec.field == "amount" else fact.extra,
                    time=timestamp if spec.timed else None,
                )
            )
        branches[spec.name] = Branch(
            Reducer(spec.reducer, strict=spec.strict),
            {key: tuple(rows) for key, rows in groups.items()},
            timed=spec.timed,
        )
    scope = Scope(
        case.mode,
        tuple((key,) for key in case.population),
        calendar_position=1 if case.mode == "calendar" else None,
    )
    return branches, scope


def sample_inputs(
    case: SampleCase,
) -> tuple[dict[Key, dict[Key, Bag]], dict[Key, tuple[Key, ...]] | None]:
    """Gather elementary occurrences before completing each lane's sample sum."""
    grouped: dict[Key, dict[Key, list[Occurrence]]] = {}
    for fact in case.facts:
        grouped.setdefault((fact.group,), {}).setdefault((fact.lane,), []).append(
            Occurrence((fact.identity,), fact.amount)
        )
    groups = {
        key: {lane: tuple(bag) for lane, bag in lanes.items()} for key, lanes in grouped.items()
    }
    domain: dict[Key, tuple[Key, ...]] | None = (
        None
        if case.lanes is None
        else {key: tuple((lane,) for lane in case.lanes) for key in groups}
    )
    return groups, domain


@st.composite
def fact_bags(draw: st.DrawFn) -> tuple[Fact, ...]:
    """Use a small domain to make overlaps, duplicate values, gaps and NA common."""
    measure = st.one_of(st.none(), st.integers(-3, 5))
    rows = draw(
        st.lists(
            st.tuples(
                st.one_of(st.none(), st.integers(0, 2)),
                st.integers(0, 2),
                st.one_of(st.none(), st.integers(-1, PERIOD_DAYS)),
                measure,
                measure,
            ),
            max_size=7,
        )
    )
    return tuple(Fact(index, *row) for index, row in enumerate(rows, start=1))


@st.composite
def observations(
    draw: st.DrawFn,
    mode: ObservationMode = "grouped",
) -> ObservationCase:
    """Grow legal numeric observation syntax to a depth between one and four."""
    facts = draw(fact_bags())
    lane = st.one_of(st.none(), st.integers(0, 2))
    left = BranchSpec("left", "amount", lane=draw(lane), timed=mode == "calendar")
    right = BranchSpec("right", "extra", lane=draw(lane))
    depth = draw(st.integers(1, 4))
    expression: Expression = Binary("add", Read(left.name), Read(right.name))
    operations: tuple[Literal["add", "subtract", "multiply", "coalesce", "choose"], ...] = (
        "add",
        "subtract",
        "multiply",
        "coalesce",
        "choose",
    )
    for _ in range(depth - 1):
        operation = draw(st.sampled_from(operations))
        if operation == "choose":
            expression = IfElse(
                Binary("lt", Read(left.name), LiteralValue(0)), expression, Read(right.name)
            )
        elif operation == "coalesce":
            expression = Coalesce(expression, Read(right.name))
        elif operation == "multiply":
            expression = Binary("multiply", expression, LiteralValue(draw(st.integers(-1, 1))))
        else:
            expression = Binary(operation, expression, Read(right.name))
    population: tuple[int | None, ...] = ()
    if mode == "explicit":
        population = tuple(
            draw(st.lists(st.sampled_from((0, 1, 2, 3, None)), unique=True, max_size=5))
        )
    return ObservationCase(facts, (left, right), (("result", expression),), mode, population)


@st.composite
def demand_cases(draw: st.DrawFn) -> ObservationCase:
    """Mix demanded and discarded strict branches over a common finite fact bag."""
    facts = draw(fact_bags())
    safe, strict = BranchSpec("safe", "amount"), BranchSpec("checked", "extra", strict=True)
    choose = draw(st.sampled_from(("ifelse", "coalesce", "multiply")))
    expression: Expression
    if choose == "ifelse":
        expression = IfElse(
            Binary("lt", Read("safe"), LiteralValue(0)), Read("checked"), Read("safe")
        )
    elif choose == "coalesce":
        expression = Coalesce(Read("safe"), Read("checked"))
    else:
        expression = Binary(
            "add", Read("safe"), Binary("multiply", LiteralValue(0), Read("checked"))
        )
    return ObservationCase(facts, (safe, strict), (("result", expression),))


@st.composite
def samples(draw: st.DrawFn, *, explicit: bool = False) -> SampleCase:
    """Exercise overlap and missing lane samples, with a finite explicit option."""
    outer: tuple[Literal["sum", "mean", "min", "max"], ...] = ("sum", "mean", "min", "max")
    return SampleCase(
        draw(fact_bags()), draw(st.sampled_from(outer)), (0, 1, 2, 3) if explicit else None
    )
