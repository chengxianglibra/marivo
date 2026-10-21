"""Observation resolves definitions once and rechecks physical input at execution."""

from __future__ import annotations

from unittest.mock import patch

import duckdb
import pytest

import marivo.analysis as mv
from marivo.analysis.dsl import source_capture
from marivo.datasource.adapters import SourceSession
from marivo.semantic._deferred_semantics import consume_metric_in_state
from marivo.semantic.errors import SemanticExpressionError, SemanticRuntimeError
from tests.analysis.graph.execution_fixtures import source_graph
from tests.shared_fixtures import DslCaseFactory


def test_public_observation_resolves_once_and_retains_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    revenue = case.session.catalog.metrics.get(f"{names.domain}.{names.revenue}")
    count = case.session.catalog.metrics.get(f"{names.domain}.{names.order_count}")
    window = case.session.time_scope(start="2026-08-01", end="2026-09-01")
    for shape, metric, components, expected in (
        ("catalog", revenue, 1, 1000.0),
        ("ratio", revenue / count, 2, 1000 / 3),
        ("linear2", revenue + revenue, 2, 2000.0),
    ):
        runs_before = len(case.session.runs().items)
        with patch.object(
            source_capture,
            "consume_metric_in_state",
            wraps=consume_metric_in_state,
        ) as resolve:
            observed = case.session.observe({"value": metric}, by=(window,))
            assert resolve.call_count == 1
            again = case.session.observe({"value": metric}, by=(window,))
            assert resolve.call_count == 2
        assert len(case.session.runs().items) == runs_before
        assert source_graph(again).fingerprint == source_graph(observed).fingerprint
        binding = source_graph(observed).signature.observation
        assert binding is not None
        assert len(binding.contributions) == components
        assert len({item.occurrence for item in binding.contributions}) == components
        result = observed.persist().to_pandas()
        assert result[("value", "value")].iloc[0] == pytest.approx(expected), shape


def test_business_completeness_uses_the_complete_calendar_domain(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    revenue = case.session.catalog.metrics.get("sales.revenue")
    scope = case.session.time_scope(start="2026-08-01", end="2026-09-01")
    grid = scope.by(mv.grain("day"))
    observed = case.session.observe({"revenue": revenue}, by=(grid,))
    assert case.session.runs().items == ()
    frame = observed.persist().to_pandas()
    assert len(frame) == 31
    values = frame[("value", "revenue")]
    assert values.sum() == 1000
    assert values.eq(0).sum() == 28
    with pytest.raises(SemanticRuntimeError, match=r"missing\.metric") as missing:
        case.session.catalog.metrics.get("missing.metric")
    assert missing.value.repair
    assert "sales.revenue" in missing.value.repair.candidates


def test_next_observation_rechecks_physical_numeric_type(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    metric = case.session.catalog.metrics.get(f"{names.domain}.{names.revenue}")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("input type admission must not execute a business read")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    case.session.observe({"value": metric})
    runs_before = len(case.session.runs().items)
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'ALTER TABLE "{names.order}" ALTER "{names.amount}" TYPE VARCHAR')
    query = case.session.observe({"value": metric})
    with pytest.raises(SemanticExpressionError) as error:
        query.persist()
    cause = error.value
    assert isinstance(cause, SemanticExpressionError)
    assert (
        cause.expected == f"a numeric physical type for {names.domain}.{names.order}.{names.amount}"
    )
    assert cause.received == "string"
    assert cause.repair
    assert len(case.session.runs().items) == runs_before
