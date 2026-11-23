"""Source singleton folds and shared Coverage preserve complete sample and key domains."""

from __future__ import annotations

from ast import literal_eval
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import ibis.expr.operations as ops
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.lowered_query import LoweredQuery
from marivo.analysis.compiler.optimization import optimize_query
from marivo.analysis.compiler.query_planning import plan_query
from marivo.analysis.compiler.query_stages import CommonRead, SourceReadStage
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.materialization.optimization import optimize_lowered_source
from marivo.analysis.materialization.query_sources import open_query_sources
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_original_multiroot_remote import (
    _session as multiroot_session,
)
from tests.analysis.numeric.test_remote_numeric_query import CASES, DATA, _project
from tests.datasource.source_cases import source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.optimization import compute_with_policy
from tests.support.source_trace import capture_source

_POLICY = OptimizationPolicy.default().with_rule("source_terminal_fusion", enabled=False)


@pytest.fixture
def session(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> Iterator[Session]:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        yield mv.session.get_or_create("singleton-keys", timezone="UTC")


@contextmanager
def _lowered(
    session: Session, query: mv.AnalysisFrame, policy: OptimizationPolicy
) -> Iterator[LoweredQuery]:
    backing = query._backing
    assert isinstance(backing, ExpressionFrameBacking)
    assert isinstance(backing.root, SourceFrameBinding)
    graph = backing.root.graph
    with (
        session._runtime.connection_service().operation() as connections,
        open_query_sources(connections, graph, session.catalog._state) as prepared,
    ):
        logical = optimize_query(plan_query(graph, prepared.schemas), policy=policy)
        lowered = lower_query(logical, prepared.relations, prepared.population_checks)
        yield optimize_lowered_source(prepared, lowered, policy)


def _coverage_antijoin(lowered: LoweredQuery) -> ops.JoinChain:
    """Select the keyed obligation, excluding the unkeyed diagnostic gate."""
    (check,) = tuple(
        join
        for read in lowered.reads
        if "__query_population_uncovered" in read.expression.columns
        for join in read.expression.op().find(ops.JoinChain)
        if any(
            link.how == "anti" and any(predicate.find(ops.Field) for predicate in link.predicates)
            for link in join.rest
        )
    )
    return check


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_identical_support_shares_three_distinct_reducers_in_common_read(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
    big = catalog_value(session.catalog, "sales.facts.big", kind="measure")
    weight = catalog_value(session.catalog, "sales.facts.weight", kind="measure")
    metrics = {"total": amount.sum(), "low": big.min(), "high": weight.max()}
    complete = mv.coordinates(by=(category,), keys=((None,), ("x",), ("empty",)))
    query = session.observe(metrics, by=complete)
    baseline = _POLICY.with_rule("source_merge", enabled=False).with_rule(
        "shared_output_coverage_check", enabled=False
    )
    with _lowered(session, query, baseline) as lowered:
        carrier = _coverage_antijoin(lowered)
        assert isinstance(carrier.first.parent, ops.Union)
        assert len(carrier.first.parent.find(ops.Union)) == 2
    with _lowered(session, query, _POLICY) as lowered:
        head = next(stage for stage in lowered.plan.stages if isinstance(stage, SourceReadStage))
        assert isinstance(head.program, CommonRead)
        assert len(head.program.members) == 3
        check = _coverage_antijoin(lowered)
        assert not check.first.parent.find(ops.Union)
        (decision,) = [
            item
            for item in lowered.plan.optimization_decisions
            if item.rule == "shared_output_coverage_check" and item.status == "adopted"
        ]
        assert decision.members == tuple(member.stage_id for member in head.program.members)

    expected = compute_with_policy(query, baseline).to_pandas()
    with monkeypatch.context() as observed:
        trace = capture_source(observed)
        actual = compute_with_policy(query, _POLICY).to_pandas()
    pd.testing.assert_frame_equal(actual, expected)
    pd.testing.assert_frame_equal(query.compute().to_pandas(), actual)
    represented = actual.loc[actual.iloc[:, 0] == "x", ["total", "low", "high"]]
    assert list(represented.itertuples(index=False, name=None)) == [
        (Decimal("4.00"), 9007199254740993, 3)
    ]
    (sql,) = trace.native_sql
    assert "__query_population_uncovered" in sql
    assert actual.loc[actual.iloc[:, 0] == "empty", "total"].tolist() == [0]

    undersized = mv.coordinates(by=(category,), keys=((None,),))
    incomplete = session.observe(metrics, by=undersized)
    with pytest.raises(MaterializationError) as failure:
        compute_with_policy(incomplete, _POLICY)
    assert failure.value.stage == "population_coverage"
    assert "'x'" in (failure.value.received or "")
    with pytest.raises(MaterializationError) as cropped:
        incomplete.top(0, by=incomplete["total"]).compute()
    assert cropped.value.stage == "population_coverage"


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_distinct_predicates_keep_later_uncovered_domain(session: Session) -> None:
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
    weight = catalog_value(session.catalog, "sales.facts.weight", kind="measure")
    incomplete = mv.coordinates(by=(category,), keys=(("x",),))
    query = session.observe(
        {
            "covered": amount.sum().where(weight < 5),
            "missing": amount.min().where(weight >= 100),
            "also_covered": amount.max().where(weight < 5),
        },
        by=incomplete,
    )
    with pytest.raises(MaterializationError) as failure:
        compute_with_policy(query, _POLICY)
    assert failure.value.stage == "population_coverage"
    with _lowered(session, query, _POLICY) as lowered:
        assert isinstance(_coverage_antijoin(lowered).first.parent, ops.Union)
        assert any(
            item.rule == "shared_output_coverage_check" and item.status == "refused"
            for item in lowered.plan.optimization_decisions
        )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_per_singleton_keeps_exact_values_counts_and_explicit_empty_samples(
    session: Session,
) -> None:
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    big = catalog_value(session.catalog, "sales.facts.big", kind="measure")
    category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
    exact = session.observe({"maximum": amount.sum().per(category).max()}, by=(category,))
    with _lowered(session, exact, _POLICY) as lowered:
        (read,) = lowered.reads
        assert len(read.expression.op().find(ops.Aggregate)) == 1
        assert any(
            item.rule == "unique_keys_degenerate_per" and item.status == "adopted"
            for item in lowered.plan.optimization_decisions
        )
    grouped_policy = _POLICY.with_rule("unique_keys_degenerate_per", enabled=False)
    with _lowered(session, exact, grouped_policy) as lowered:
        (read,) = lowered.reads
        assert len(read.expression.op().find(ops.Aggregate)) == 2
    grouped = compute_with_policy(exact, grouped_policy).to_pandas()
    actual = compute_with_policy(exact, _POLICY).to_pandas()
    pd.testing.assert_frame_equal(actual, grouped)
    assert actual["maximum"].dropna().tolist() == [Decimal("4.00")]
    assert actual["maximum"].isna().sum() == 1

    strict = session.observe(
        {"maximum": amount.sum(nulls="error").per(category).max()}, by=(category,)
    )
    for policy in (grouped_policy, _POLICY):
        with pytest.raises(MaterializationError, match="non-NA source values"):
            compute_with_policy(strict, policy)

    counted = session.observe({"samples": big.min().per(category).count()}, by=(category,))
    rows = compute_with_policy(counted, _POLICY).to_pandas()
    assert rows.loc[rows.iloc[:, 0] == "x", "samples"].tolist() == [1]
    assert rows.loc[rows.iloc[:, 0].isna(), "samples"].tolist() == [0]

    integer_mean = session.observe({"mean": big.min().per(category).mean()}, by=(category,))
    with _lowered(session, integer_mean, _POLICY) as lowered:
        assert all(
            item.rule != "unique_keys_degenerate_per"
            for item in lowered.plan.optimization_decisions
        )

    empty = mv.coordinates(by=(category,), keys=())
    absent = session.observe({"total": amount.sum().per(empty).sum()}, by=(category,))
    with _lowered(session, absent, _POLICY) as lowered:
        assert all(
            item.rule != "unique_keys_degenerate_per"
            for item in lowered.plan.optimization_decisions
        )
    assert compute_with_policy(absent, _POLICY).to_pandas()["total"].tolist() == [0, 0]


def test_mixed_root_common_reads_do_not_misattribute_uncovered_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    with multiroot_session(
        "duckdb", "table", tmp_path, monkeypatch, semantic_project_factory
    ) as session:
        category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
        subset = catalog_value(session.catalog, "sales.subset.weight", kind="measure")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        incomplete = mv.coordinates(by=(category,), keys=(("A",),))
        left = {"subset_sum": subset.sum(), "subset_low": subset.min(), "subset_high": subset.max()}
        right = {"total": amount.sum(), "low": amount.min(), "high": amount.max()}
        disabled = _POLICY.with_rule("shared_output_coverage_check", enabled=False)
        for metrics in (left | right, right | left):
            query = session.observe(metrics, by=incomplete)
            for frame in (query, query.top(0, by=query["total"])):
                with pytest.raises(MaterializationError) as baseline:
                    compute_with_policy(frame, disabled)
                with pytest.raises(MaterializationError) as actual:
                    compute_with_policy(frame, _POLICY)
                assert actual.value.stage == baseline.value.stage == "population_coverage"
                assert actual.value.received == baseline.value.received
                assert "'B'" in (actual.value.received or "")
            with _lowered(session, query, _POLICY) as lowered:
                assert (
                    sum(
                        isinstance(stage, SourceReadStage) and isinstance(stage.program, CommonRead)
                        for stage in lowered.plan.stages
                    )
                    == 2
                )
                assert all(
                    decision.rule != "shared_output_coverage_check"
                    for decision in lowered.plan.optimization_decisions
                )


def test_same_root_coverage_limits_distinct_complete_witnesses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    extra = ",".join(
        f"({100 + index},'tenant','{category}',1,1,2,DATE '2026-07-01')"
        for index, category in enumerate("ABCDEFG")
    )
    data = replace(DATA, values=DATA.values + "," + extra)
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("distinct-coverage", timezone="UTC")
        category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="measure")
        query = session.observe(
            {"sum": amount.sum().where(weight > 0), "max": amount.max().where(weight < 50)},
            by=mv.coordinates(by=(category,), keys=()),
        )
        with pytest.raises(MaterializationError) as failure:
            compute_with_policy(query, _POLICY)
        assert failure.value.stage == "population_coverage"
        witnesses = literal_eval((failure.value.received or "").split("coordinates=", 1)[1])
        assert [row["key.0"] for row in witnesses] == list("ABCDEF")
