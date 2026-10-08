"""Observation entries share semantics while retaining current physical admission."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode, topology
from marivo.analysis.core.rules import ObserveCount, ObserveMetric
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import graph_observation
from marivo.analysis.public_dsl import MetricInputValue
from marivo.datasource.adapters import SourceSession
from marivo.semantic.metric_graph_lowering import (
    lower_catalog_metric,
    normalize_target_metric,
    normalize_target_metric_inputs,
)
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
@pytest.mark.parametrize("shape", ("catalog", "ratio", "linear2", "linear8", "linear32"))
def test_public_observation_resolves_once_and_retains_components(
    analysis_dsl_case_factory: DslCaseFactory, shape: str
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    revenue = ms.ref.metric(f"{names.domain}.{names.revenue}")
    count = ms.ref.metric(f"{names.domain}.{names.order_count}")
    metric: MetricInputValue = revenue
    components = 1
    expected = 1000.0
    if shape == "ratio":
        metric = mv.runtime_metric.ratio(revenue, count, label="average")
        components = 2
        expected /= 3
    elif shape.startswith("linear"):
        components = int(shape.removeprefix("linear"))
        metric = mv.runtime_metric.linear(add=(revenue,) * components, label=shape)
        expected *= components
    window = mv.time_scope(start="2026-08-01", end="2026-09-01")
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    runs_before = len(case.session.runs().items)
    with (
        patch.object(
            graph_observation,
            "normalize_target_metric",
            wraps=normalize_target_metric,
        ) as catalog_normalize,
        patch(
            "marivo.semantic.metric_graph_lowering.normalize_target_metric_inputs",
            wraps=normalize_target_metric_inputs,
        ) as runtime_normalize,
        patch(
            "marivo.semantic.runtime_metric_lowering.lower_catalog_metric",
            wraps=lower_catalog_metric,
        ) as lower,
    ):
        observed = members.observe(
            metric,
            during=window,
            via=buyer,
            by=(ms.ref.entity(f"{names.domain}.{names.customer}"),),
        )
        assert catalog_normalize.call_count + runtime_normalize.call_count == 1
        assert lower.call_count == (0 if shape == "catalog" else 2 if shape == "ratio" else 1)
        again = members.observe(
            metric,
            during=window,
            via=buyer,
            by=(ms.ref.entity(f"{names.domain}.{names.customer}"),),
        )
        assert catalog_normalize.call_count + runtime_normalize.call_count == 2
        assert again._node.root.signature.quantity == observed._node.root.signature.quantity

    assert len(case.session.runs().items) == runs_before
    occurrences = tuple(
        node.parameters
        for node in topology(observed._node.root)
        if isinstance(node, MethodNode)
        and isinstance(node.parameters, (ObserveMetric, ObserveCount))
    )
    assert len(occurrences) == components
    assert len({item.quantity.contribution_id for item in occurrences}) == components
    result = observed.rollup().execute().to_pandas()
    assert result.iloc[0]["value"] == pytest.approx(expected)


@pytest.mark.runtime
def test_business_completeness_shares_resolution_and_keeps_error_precedence(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    scope = mv.time_scope(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    grid = mv.time_grid(during=scope, grain=mv.grain("day"))
    each = members
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    with patch.object(
        graph_observation,
        "normalize_target_metric",
        wraps=normalize_target_metric,
    ) as normalize:
        each.observe(
            ms.ref.metric(f"{names.domain}.{names.revenue}"),
            during=grid,
            via=buyer,
            complete_during=(scope,),
            by=(ms.ref.entity(f"{names.domain}.{names.customer}"),),
        )
        assert normalize.call_count == 1
        with pytest.raises(AnalysisError, match="business-completeness"):
            each.observe(
                ms.ref.metric("missing.metric"),
                during=scope,
                complete_during=(),
                by=(ms.ref.entity(f"{names.domain}.{names.customer}"),),
            )
        assert normalize.call_count == 1


@pytest.mark.runtime
def test_next_observation_rechecks_physical_numeric_type(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    metric = ms.ref.metric(f"{names.domain}.{names.revenue}")
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("observation admission must not read business batches")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    members.observe(metric, via=buyer, by=(ms.ref.entity(f"{names.domain}.{names.customer}"),))
    runs_before = len(case.session.runs().items)
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'ALTER TABLE "{names.order}" ALTER "{names.amount}" TYPE VARCHAR')
    with pytest.raises(DatasetConstructionError) as error:
        members.observe(metric, via=buyer, by=(ms.ref.entity(f"{names.domain}.{names.customer}"),))
    assert error.value.received == "unqualified amount physical type"
    assert error.value.location == "analysis.graph_observation"
    assert len(case.session.runs().items) == runs_before
