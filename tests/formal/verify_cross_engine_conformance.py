"""Run authored bag semantics on each governed SQL profile."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from math import inf
from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
from marivo.analysis.model.data_types import DataType, DecimalDataType, ScalarDataType
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.datasource.spi import query_dialect
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import CASES, DATA, _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.datasource.test_query_dialect_profiles import _scores
from tests.formal._runtime_algebra_relations import (
    test_live_civil_dst_boundary as test_live_civil_dst_boundary,
)
from tests.formal._runtime_algebra_relations import (
    test_live_obligatory_composite_witness as test_live_obligatory_composite_witness,
)
from tests.formal._runtime_algebra_relations import (
    test_live_shared_fanout_product as test_live_shared_fanout_product,
)
from tests.formal._smt import _z3
from tests.formal.reference.equivalence import (
    ObservedFailure,
    assert_equivalent,
    assert_policies_agree,
    run_frame,
)
from tests.formal.reference.evaluator import observe, reduce_bag, top
from tests.formal.reference.model import (
    Bag,
    Binary,
    Branch,
    Coalesce,
    Column,
    IfElse,
    Key,
    LiteralValue,
    Occurrence,
    Read,
    Reducer,
    Result,
    Scope,
)
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace, capture_source

pytestmark = pytest.mark.runtime
_DAY1 = date(2026, 7, 1)
_DAY2 = date(2026, 7, 2)
_DAY3 = date(2026, 7, 3)
_OUTSIDE = date(2026, 7, 4)


@dataclass(frozen=True, slots=True)
class _Fact:
    identity: int
    tenant: str
    category: str | None
    amount: Decimal | None
    big: int | None
    weight: int
    day: date

    def occurrence(self) -> Occurrence:
        return Occurrence(
            (self.tenant, self.identity),
            self.amount,
            weight=self.weight,
            time=datetime(self.day.year, self.day.month, self.day.day, tzinfo=timezone.utc),
        )


def _solve_float_overlap() -> tuple[int, int]:
    proof = _z3(
        """(set-logic QF_FPBV)
(declare-const exact (_ BitVec 64))
(define-fun neighbor () (_ BitVec 64) (bvadd exact (_ bv1 64)))
(assert (bvuge exact (_ bv9007199254740992 64)))
(assert (bvult exact (_ bv9007199254741008 64)))
(assert (fp.eq ((_ to_fp 11 53) RNE exact) ((_ to_fp 11 53) RNE neighbor)))
(check-sat)
(get-value (exact neighbor))"""
    )
    assert proof.splitlines()[0] == "sat", proof
    values = {
        name: int(value, 16)
        for name, value in re.findall(r"\((exact|neighbor) #x([0-9a-f]+)\)", proof)
    }
    first, second = values["exact"], values["neighbor"]
    assert first + 1 == second and float(first) == float(second)
    return first, second


@pytest.fixture(scope="module")
def corpus() -> tuple[_Fact, ...]:
    first, second = _solve_float_overlap()
    # The lower neighbor sorts first by key if SQL rounds the two scores to Float64.
    return (
        _Fact(1, "A", "west", Decimal("1.25"), first, 1, _DAY1),
        _Fact(1, "a", "west", Decimal("2.75"), second, 3, _DAY1),
        _Fact(1, "a ", "east", Decimal("0"), second, 2, _DAY2),
        _Fact(2, "A", "void", None, second, 5, _DAY1),
        _Fact(2, "a", "void", None, -1, 7, _DAY2),
        _Fact(2, "a ", None, Decimal("8"), 5, 1, _DAY2),
        _Fact(3, "distinct", "east", Decimal("0"), None, 1, _DAY2),
        _Fact(4, "outside", "east", Decimal("16"), None, 1, _OUTSIDE),
    )


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    assert isinstance(value, (int, Decimal))
    return str(value)


def _data(corpus: tuple[_Fact, ...], precision: int, scale: int) -> SourceData:
    names = ("id", "tenant", "category", "amount", "big", "weight", "day")
    rows = [
        (row.identity, row.tenant, row.category, row.amount, row.big, row.weight, row.day)
        for row in corpus
    ]
    values = ",".join("(" + ",".join(map(_sql_literal, row)) + ")" for row in rows)
    records: list[dict[str, object]] = [dict(zip(names, row, strict=True)) for row in rows]
    return SourceData(
        DATA.columns.replace("DECIMAL(18,2)", f"DECIMAL({precision},{scale})"),
        values,
        DATA.clickhouse_columns.replace("Decimal(18,2)", f"Decimal({precision},{scale})"),
        records,
    )


@contextmanager
def _open_session(
    provider: str,
    profile: str,
    data: SourceData,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> Iterator[mv.Session]:
    monkeypatch.chdir(tmp_path)
    with source_case(provider, profile, tmp_path, monkeypatch, data) as case:
        _project(provider, case, monkeypatch, semantic_project_factory)
        yield mv.session.get_or_create("formal-live-conformance", timezone="UTC")


@pytest.fixture(params=CASES, ids=[provider for provider, _ in CASES])
def sql_session(
    request: pytest.FixtureRequest,
    corpus: tuple[_Fact, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> Iterator[tuple[str, mv.Session]]:
    provider, profile = request.param
    assert isinstance(provider, str) and isinstance(profile, str)
    with _open_session(
        provider, profile, _data(corpus, 18, 2), tmp_path, monkeypatch, semantic_project_factory
    ) as session:
        yield provider, session


def _groups(
    rows: Iterable[_Fact], key: Callable[[_Fact], Key] = lambda row: (row.category,)
) -> dict[Key, Bag]:
    groups: defaultdict[Key, list[Occurrence]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row.occurrence())
    return {group: tuple(values) for group, values in groups.items()}


def _branch(
    bags: dict[Key, Bag],
    operation: Literal["sum", "weighted_mean"] = "sum",
    *,
    strict: bool = False,
) -> Branch:
    return Branch(Reducer(operation, strict=strict), bags)


def _verify(
    label: str,
    expected: Result,
    query: mv.AnalysisFrame,
    trace: SourceTrace,
    *,
    key_types: tuple[DataType, ...] | None = None,
    value_types: Mapping[str, DataType] | None = None,
) -> None:
    submitted = len(trace.native_sql)
    optimized = run_frame(query, OptimizationPolicy.default())
    reference = run_frame(query, OptimizationPolicy.disabled())
    assert_equivalent(expected, optimized, key_types=key_types, value_types=value_types)
    assert_equivalent(expected, reference, key_types=key_types, value_types=value_types)
    assert_policies_agree(reference, optimized)
    assert len(trace.native_sql) > submitted, label
    print(f"{label}: {len(trace.native_sql) - submitted} native submissions")


def test_live_bag_matrix(
    sql_session: tuple[str, mv.Session], corpus: tuple[_Fact, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, session = sql_session
    catalog = session.catalog
    amount = catalog_value(catalog, "sales.facts.amount", kind="measure")
    weight = catalog_value(catalog, "sales.facts.weight", kind="measure")
    category = catalog_value(catalog, "sales.facts.category", kind="dimension")
    total = amount.sum(time=None)
    strict = amount.sum(time=None, nulls="error")
    trace = capture_source(monkeypatch)
    groups = _groups(corpus)
    grouped = Scope("grouped")
    value = {"value": Read("value")}
    decimal = DecimalDataType(38, 2)
    floating = ScalarDataType("float64")
    for label, reducer, metric, dtype in (
        ("sum", _branch(groups), total, decimal),
        ("strict sum", _branch(groups, strict=True), strict, decimal),
        (
            "weighted mean",
            _branch(groups, "weighted_mean"),
            amount.weighted_mean(weight=weight, time=None),
            floating,
        ),
    ):
        query = session.observe({"value": metric}, by=(category,))
        _verify(
            f"{provider} {label}",
            observe({"value": reducer}, value, grouped),
            query,
            trace,
            key_types=(ScalarDataType("string"),),
            value_types={"value": dtype},
        )

    no_facts = observe({"value": _branch({(): ()})}, value, Scope("scalar"))
    _verify(
        f"{provider} empty input",
        no_facts,
        session.observe({"value": total.where(category == "absent")}),
        trace,
    )

    west = total.where(category == "west")
    east = total.where(category == "east")
    branches = {
        "west": _branch(_groups(row for row in corpus if row.category == "west")),
        "east": _branch(_groups(row for row in corpus if row.category == "east")),
    }
    for label, expression, metric in (
        ("sparse support", Binary("add", Read("west"), Read("east")), west + east),
        ("coalesce", Coalesce(Read("west"), Read("east")), west.coalesce(east)),
    ):
        query = session.observe({"value": metric}, by=(category,))
        _verify(
            f"{provider} {label}", observe(branches, {"value": expression}, grouped), query, trace
        )

    guard = IfElse(Binary("eq", Read("safe"), LiteralValue(0)), Read("strict"), Read("safe"))
    expected = observe(
        {"safe": _branch(groups), "strict": _branch(groups, strict=True)}, {"value": guard}, grouped
    )
    query = session.observe({"value": (total == 0).ifelse(strict, total)}, by=(category,))
    _verify(f"{provider} branch demand", expected, query, trace)

    fallback = {"value": Coalesce(Read("safe"), Read("strict"))}
    counted = Branch(Reducer("count"), groups)
    inactive = observe({"safe": counted, "strict": _branch(groups, strict=True)}, fallback, grouped)
    count = amount.count(time=None)
    _verify(
        f"{provider} coalesce masks strict fallback",
        inactive,
        session.observe({"value": count.coalesce(strict)}, by=(category,)),
        trace,
    )
    demanded = observe(
        {"safe": _branch(groups), "strict": _branch(groups, strict=True)}, fallback, grouped
    )
    _verify(
        f"{provider} coalesce demands strict fallback",
        demanded,
        session.observe({"value": total.coalesce(strict)}, by=(category,)),
        trace,
    )


def test_live_calendar_independent_broadcast(
    sql_session: tuple[str, mv.Session], corpus: tuple[_Fact, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, session = sql_session
    catalog = session.catalog
    day = catalog_value(catalog, "sales.facts.day", kind="time_dimension")
    amount = catalog_value(catalog, "sales.facts.amount", kind="measure")
    cells = tuple(row.occurrence() for row in corpus)
    timed = _groups(
        (row for row in corpus if _DAY1 <= row.day < _DAY3),
        key=lambda row: (row.occurrence().time,),
    )
    expected = observe(
        {"daily": Branch(Reducer("sum"), timed, timed=True), "all": _branch({(): cells})},
        {"daily": Read("daily"), "all": Read("all")},
        Scope("calendar", calendar_position=0),
    )
    calendar = session.time_scope(start=_DAY1, end=_DAY3).by(mv.grain("day"))
    query = session.observe(
        {"daily": amount.sum(time=day), "all": amount.sum(time=None)}, by=(calendar,)
    )
    _verify(f"{provider} calendar broadcast", expected, query, capture_source(monkeypatch))


def test_live_conditional_group_shares_five_independent_contributions(
    sql_session: tuple[str, mv.Session], corpus: tuple[_Fact, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, session = sql_session
    catalog = session.catalog
    amount = catalog_value(catalog, "sales.facts.amount", kind="measure")
    weight = catalog_value(catalog, "sales.facts.weight", kind="measure")
    category = catalog_value(catalog, "sales.facts.category", kind="dimension")
    grouped = Scope("grouped")
    west = _groups(row for row in corpus if row.category == "west")
    eastern_zeroes = _groups(row for row in corpus if row.category == "east" and row.amount == 0)
    void = _groups(row for row in corpus if row.category == "void")
    branches = {
        "total": _branch(west),
        "mean": Branch(Reducer("mean"), eastern_zeroes),
        "count": Branch(Reducer("count"), void),
        "weighted": _branch(west, "weighted_mean"),
        "strict": _branch({}, strict=True),
    }
    metrics = {
        "total": amount.sum(time=None).where(category == "west"),
        "mean": amount.mean(time=None).where((category == "east") & (amount == 0)),
        "count": amount.count(time=None).where(category == "void"),
        "weighted": amount.weighted_mean(weight=weight, time=None).where(
            (category == "west") | (category == "never")
        ),
        "strict": amount.sum(time=None, nulls="error").where(category == "absent"),
    }
    query = session.observe(metrics, by=(category,))
    expected = observe(branches, {label: Read(label) for label in branches}, grouped)
    trace = capture_source(monkeypatch)
    disabled = OptimizationPolicy.disabled()
    control = run_frame(query, disabled)
    assert len(trace.native_sql) == 5
    candidate = run_frame(query, disabled.with_rule("conditional_source_merge", enabled=True))
    assert len(trace.native_sql) == 6
    default = run_frame(query, OptimizationPolicy.default())
    for received in (control, candidate, default):
        assert_equivalent(expected, received)
    assert_policies_agree(control, candidate)
    assert_policies_agree(candidate, default)
    statement = trace.native_sql[5].upper()
    style = query_dialect(provider).conditional_aggregate_style
    if style == "filter_where":
        assert "FILTER" in statement and "WHERE" in statement
    elif style == "clickhouse_if":
        assert "SUMIF" in statement and "AVGIF" in statement
    else:
        assert style == "case_when" and "CASE" in statement


def test_live_conditional_group_preserves_excluded_predicate_witness(
    sql_session: tuple[str, mv.Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, session = sql_session
    catalog = session.catalog
    amount = catalog_value(catalog, "sales.facts.amount", kind="measure")
    big = catalog_value(catalog, "sales.facts.big", kind="measure")
    category = catalog_value(catalog, "sales.facts.category", kind="dimension")
    unsafe = big.sqrt() < 0
    evaluated = amount.count(time=None).where(unsafe)
    unmatched = amount.count(time=None).where(category == "absent")
    west = amount.count(time=None).where(category == "west")
    trace = capture_source(monkeypatch)
    disabled = OptimizationPolicy.disabled()
    for outputs in (
        {"evaluated": evaluated, "unmatched": unmatched},
        {"result": west.coalesce(evaluated)},
    ):
        query = session.observe(outputs, by=(category,))
        control = run_frame(query, disabled)
        candidate = run_frame(query, disabled.with_rule("conditional_source_merge", enabled=True))
        for received in (control, candidate):
            assert isinstance(received, ObservedFailure), received
            assert received.witnesses == (("void",),), received
            assert received.received is not None and "Sqrt" in received.received
        assert "__numeric_present.block1" in trace.native_sql[-1]


def test_live_conditional_weighted_product_skips_unselected_native_overflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    facts = (
        _Fact(1, "A", "selected", None, 2, 3, _DAY1),
        _Fact(2, "B", "outside", None, 2**62, 8, _DAY1),
    )
    provider, profile = CASES[0]
    assert provider == "duckdb"
    with _open_session(
        provider, profile, _data(facts, 18, 2), tmp_path, monkeypatch, semantic_project_factory
    ) as session:
        catalog = session.catalog
        big = catalog_value(catalog, "sales.facts.big", kind="measure")
        weight = catalog_value(catalog, "sales.facts.weight", kind="measure")
        category = catalog_value(catalog, "sales.facts.category", kind="dimension")
        query = session.observe(
            {
                "weighted": big.weighted_mean(weight=weight, time=None).where(
                    category == "selected"
                ),
                "outside": weight.count(time=None).where(category == "outside"),
            }
        )
        trace = capture_source(monkeypatch)
        disabled = OptimizationPolicy.disabled()
        expected = Result(
            ((),),
            {},
            {"weighted": Column.from_values({(): 2}), "outside": Column.from_values({(): 1})},
        )
        control = run_frame(query, disabled)
        candidate = run_frame(query, disabled.with_rule("conditional_source_merge", enabled=True))
        assert_equivalent(expected, control)
        assert_equivalent(expected, candidate)
        assert len(trace.native_sql) == 3


@pytest.mark.parametrize(
    "provider,profile", source_params([case for case in CASES if case[0] in ("postgres", "mysql")])
)
def test_live_conditional_filter_retains_native_floating_demand(
    provider: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    data = SourceData(
        DATA.columns.replace(
            "DECIMAL(18,2)", "DOUBLE PRECISION" if provider == "postgres" else "DOUBLE"
        ),
        "(1,'A','selected',7,1,1,DATE '2026-07-01'),(2,'B','outside',1e308,1,1,DATE '2026-07-01')",
        DATA.clickhouse_columns,
        [],
    )
    with _open_session(
        provider, profile, data, tmp_path, monkeypatch, semantic_project_factory
    ) as session:
        catalog = session.catalog
        amount = catalog_value(catalog, "sales.facts.amount", kind="measure")
        category = catalog_value(catalog, "sales.facts.category", kind="dimension")
        trace = capture_source(monkeypatch)
        disabled = OptimizationPolicy.disabled()
        expected = Result(
            ((),), {}, {"chosen": Column.from_values({(): 1}), "other": Column.from_values({(): 1})}
        )
        for expression in (amount * amount, amount + amount, amount / 1e-10):
            query = session.observe(
                {
                    "chosen": amount.count(time=None).where(
                        (expression > 2) & (category == "selected")
                    ),
                    "other": amount.count(time=None).where(category == "outside"),
                }
            )
            before = len(trace.native_sql)
            for policy in (disabled, disabled.with_rule("conditional_source_merge", enabled=True)):
                assert_equivalent(expected, run_frame(query, policy))
            assert len(trace.native_sql) - before == 4


def test_live_ordered_samples_and_explicit_population(
    sql_session: tuple[str, mv.Session], corpus: tuple[_Fact, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, session = sql_session
    catalog = session.catalog
    amount = catalog_value(catalog, "sales.facts.amount", kind="measure")
    category = catalog_value(catalog, "sales.facts.category", kind="dimension")
    day = catalog_value(catalog, "sales.facts.day", kind="time_dimension")
    tenant = catalog_value(catalog, "sales.facts.tenant", kind="dimension")
    trace = capture_source(monkeypatch)
    in_scope = tuple(row for row in corpus if _DAY1 <= row.day < _DAY3)
    groups = _groups(in_scope, key=lambda row: (row.category, row.occurrence().time))
    samples: defaultdict[Key, list[Occurrence]] = defaultdict(list)
    for key, bag in groups.items():
        samples[key[:1]].append(
            Occurrence(key, reduce_bag(Reducer("sum"), bag)[0], time=bag[0].time)
        )
    actual = amount.sum(time=day).per(day, grain=mv.grain("day"))
    scope = session.time_scope(start=_DAY1, end=_DAY3)
    for label, reducer, metric in (
        ("first", Reducer("first", include_null=True), actual.first(include_null=True)),
        ("last", Reducer("last"), actual.last()),
    ):
        branch = Branch(reducer, {key: tuple(bag) for key, bag in samples.items()})
        expected = observe({"value": branch}, {"value": Read("value")}, Scope("grouped"))
        _verify(
            f"{provider} {label}",
            expected,
            session.observe({"value": metric}, by=(category,), where=scope),
            trace,
        )

    domain = (("east",), ("west",), ("void",), (None,), ("empty",))
    population = mv.coordinates(by=(category,), keys=domain)
    inner = _groups(corpus)
    cells = tuple(
        Occurrence(key, reduce_bag(Reducer("sum"), inner.get(key, ()))[0]) for key in domain
    )
    branch = Branch(Reducer("mean"), {(): cells})
    expected = observe({"value": branch}, {"value": Read("value")}, Scope("scalar"))
    query = session.observe({"value": amount.sum(time=None).per(population).mean()})
    _verify(f"{provider} explicit samples", expected, query, trace)

    indexed = _groups(in_scope, key=lambda row: (row.category, row.occurrence().time, row.tenant))
    series: defaultdict[Key, list[Occurrence]] = defaultdict(list)
    for key, bag in indexed.items():
        series[key[:1]].append(
            Occurrence(key, reduce_bag(Reducer("sum"), bag)[0], time=bag[0].time)
        )
    branch = Branch(Reducer("first"), {key: tuple(bag) for key, bag in series.items()})
    duplicates = observe({"value": branch}, {"value": Read("value")}, Scope("grouped"))
    assert duplicates.witness("duplicate_order_key") == ("east",)
    sample = amount.sum(time=day).per((day, tenant)).first()
    query = session.observe({"value": sample}, by=(category,), where=scope)
    _verify(f"{provider} duplicate ordered clock", duplicates, query, trace)


def test_live_exact_integer_top_retains_composite_bytes(
    sql_session: tuple[str, mv.Session], corpus: tuple[_Fact, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, session = sql_session
    catalog = session.catalog
    entity = catalog_value(catalog, "sales.facts", kind="entity")
    score = catalog_value(catalog, "sales.facts.big", kind="measure")
    observed = session.read(session.members(entity), {"score": score})
    keys = tuple((row.tenant, row.identity) for row in corpus)
    result = Result(
        keys,
        {},
        {"score": Column.from_values(dict(zip(keys, (row.big for row in corpus), strict=True)))},
    )
    trace = capture_source(monkeypatch)
    for limit in (1, 3):
        submitted = len(trace.native_sql)
        _verify(
            f"{provider} exact integer Top({limit})",
            top(result, limit, "score"),
            observed.top(limit, by=observed["score"]),
            trace,
            key_types=(ScalarDataType("string"), ScalarDataType("int64")),
            value_types={"score": ScalarDataType("int64")},
        )
        assert any(
            "__frame_top_present" in sql and f"limit {limit}" in sql.lower()
            for sql in trace.native_sql[submitted:]
        )


@pytest.mark.parametrize("provider, profile", source_params(CASES))
def test_live_float_dialect_top_keeps_nan_separate_from_na(
    provider: str, profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capabilities = query_dialect(provider)
    order_keys = capabilities.float_order_keys
    assert order_keys is not None, provider
    scores = {1: 1.0, 2: -1.0, 3: 1.0, 4: 0.0, 9: None}
    if provider != "mysql":
        scores.update({5: -inf, 6: inf, 7: float("nan"), 8: float("nan")})
    keys = tuple((identity,) for identity in scores)
    values = Column.from_values({(identity,): value for identity, value in scores.items()})
    reference = Result(keys, {}, {"score": values})
    with source_case(provider, profile, tmp_path, monkeypatch, _scores(provider)) as case:
        source = case.session.bind(case.source, source_identity="formal-live-float-top").relation
        for descending in (False, True):
            for limit in (3, len(keys)):
                expected = top(reference, limit, "score", descending=descending)
                selected = source.order_by(*order_keys(source.score, descending), source.id)
                projected = selected.limit(limit).select("id", missing=selected.score.isnull())
                rows = case.session.collect_bounded(
                    projected, purpose="formal-live-float-top", max_rows=limit
                )
                assert tuple((identity,) for identity in rows["id"]) == expected.keys
                assert rows["missing"].tolist() == [values.at(key) is None for key in expected.keys]


@pytest.mark.parametrize("provider, profile", source_params(CASES))
def test_live_wide_decimal_weighting_retains_native_product_scale(
    provider: str,
    profile: str,
    corpus: tuple[_Fact, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    # DECIMAL(38,12) multiplied by a BIGINT meets the declared width cap at
    # scale zero. The selected values avoid a tie at the native cast boundary.
    wide = tuple(
        replace(row, amount=row.amount + Decimal("0.000000000004"))
        if row.category == "west" and row.amount is not None
        else row
        for row in corpus
    )
    west = tuple(row for row in wide if row.category == "west")
    products = tuple(
        (row.amount * row.weight).quantize(Decimal(1)) for row in west if row.amount is not None
    )
    expected_value = float(sum(products) / sum(row.weight for row in west))
    assert expected_value == 2.25
    expected = Result(((),), {}, {"value": Column.from_values({(): expected_value})})
    with _open_session(
        provider, profile, _data(wide, 38, 12), tmp_path, monkeypatch, semantic_project_factory
    ) as session:
        catalog = session.catalog
        amount = catalog_value(catalog, "sales.facts.amount", kind="measure")
        weight = catalog_value(catalog, "sales.facts.weight", kind="measure")
        category = catalog_value(catalog, "sales.facts.category", kind="dimension")
        selected = amount.weighted_mean(weight=weight, where=category == "west", time=None)
        _verify(
            f"{provider} Decimal product scale",
            expected,
            session.observe({"value": selected}),
            capture_source(monkeypatch),
            key_types=(),
            value_types={"value": ScalarDataType("float64")},
        )
        exact_sum = Result(
            ((),), {}, {"value": Column.from_values({(): Decimal("4.000000000008")})}
        )
        _verify(
            f"{provider} wide Decimal sum",
            exact_sum,
            session.observe({"value": amount.sum(where=category == "west", time=None)}),
            capture_source(monkeypatch),
            key_types=(),
            value_types={"value": DecimalDataType(38, 12)},
        )


@pytest.mark.parametrize("provider, profile", source_params(CASES))
def test_live_empty_table_publishes_one_typed_scalar(
    provider: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    with _open_session(
        provider, profile, _data((), 18, 2), tmp_path, monkeypatch, semantic_project_factory
    ) as session:
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        branch = Branch(Reducer("sum"), {})
        expected = observe(
            {"total": branch, "count": Branch(Reducer("count"), {})},
            {"total": Read("total"), "count": Read("count")},
            Scope("scalar"),
        )
        _verify(
            f"{provider} physically empty source",
            expected,
            session.observe({"total": amount.sum(time=None), "count": amount.count(time=None)}),
            capture_source(monkeypatch),
            key_types=(),
            value_types={"total": DecimalDataType(38, 2), "count": ScalarDataType("int64")},
        )
