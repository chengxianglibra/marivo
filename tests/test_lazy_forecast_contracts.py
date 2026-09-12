"""Pinned private Forecast construction, closed helpers and local placement."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.placement import PandasStep, SourceStep, place
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.publication import materialization_contract
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators.errors import ForecastError
from marivo.analysis.operators.forecast_contracts import (
    ForecastHorizon,
    ForecastModel,
    drift,
    periods,
    seasonal_naive,
)
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_forecast_fixtures import history
from tests.lazy_observation_fixtures import NoIoActionPort, make_sources


@pytest.mark.parametrize("panel", [False, True])
def test_exact_private_family_and_capabilities(panel: bool) -> None:
    result = history(make_sources(), panel=panel).forecast(horizon=periods(4))
    assert result.row_contract.shape_id.local_shape_id == ("dimension-time" if panel else "time")
    assert [f.name for f in result.schema.columns] == (["channel"] if panel else []) + [
        "order_time",
        "horizon_ordinal",
        "forecast_value",
        "interval_lower",
        "interval_upper",
        "training_row_count",
    ]
    assert all(not f.nullable for f in result.schema.columns if f.role_id == "effect_value")
    contract = materialization_contract(result)
    assert contract.finding_policy_id == "forecast_point_findings@v1"
    assert contract.finding_extractor_id == "forecast_point_finding"
    assert "nominal prediction" in result.contract().render()
    assert "no empirical coverage" in result.contract().render()
    for name in (
        "LogicalForecastDataset",
        "ForecastHorizon",
        "ForecastModel",
        "periods",
        "naive",
        "drift",
        "seasonal_naive",
    ):
        exports = mv.__all__
        assert isinstance(exports, (list, tuple))
        assert name in exports and hasattr(mv, name)
    assert "ForecastDataset" not in exports and not hasattr(mv, "ForecastDataset")
    assert not hasattr(result, "compare") and not hasattr(result, "discover")
    selected = result.where(gt(result.fields.get("forecast_value"), 0))
    ranked = selected.rank(selected.fields.get("forecast_value")).limit(2)
    assert ranked.kind == "forecast"
    with pytest.raises(DatasetConstructionError):
        ranked.rank(ranked.fields.get("forecast_value"))
    foreign = history(make_sources(session_id="foreign")).forecast(horizon=periods(4))
    with pytest.raises(DatasetConstructionError):
        result.where(gt(foreign.fields.get("forecast_value"), 0))


@pytest.mark.parametrize("value", [True, False, 0, -1, 1001, 1.5, "4", None])
def test_horizon_boundary(value: object) -> None:
    from typing import cast

    with pytest.raises(ForecastError):
        periods(cast("int", value))


@pytest.mark.parametrize("value", [True, 1, 0, -4, 2.5, "2", None])
def test_explicit_season_boundary(value: object) -> None:
    from typing import cast

    with pytest.raises(ForecastError):
        seasonal_naive(periods=cast("int", value))


@pytest.mark.parametrize("value", [True, 0.0, 1.0, -1.0, float("nan"), float("inf")])
def test_interval_boundary(value: float) -> None:
    with pytest.raises(ForecastError):
        history(make_sources()).forecast(horizon=periods(1), interval_level=value)


def test_helper_identity_and_receiver_rejection() -> None:
    assert periods(1).count == 1 and periods(1000).count == 1000
    with pytest.raises(ForecastError):
        ForecastHorizon(4)
    with pytest.raises(ForecastError):
        ForecastModel("naive@v1")
    with pytest.raises(FrozenInstanceError):
        field = "count"
        setattr(periods(1), field, 3)
    source = make_sources()
    for invalid in (
        source.observe(ref.metric("sales.revenue")),
        source.observe(ref.metric("sales.revenue")).aggregate(),
    ):
        with pytest.raises(ForecastError):
            invalid.forecast(horizon=periods(4))
    h = history(source)
    variants = (
        h.forecast(horizon=periods(4)),
        h.forecast(horizon=periods(3)),
        h.forecast(horizon=periods(4), model=drift()),
        h.forecast(horizon=periods(4), interval_level=0.9),
    )
    assert len({v.definition_fingerprint for v in variants}) == 4


def test_registered_local_frontier_without_backend_access() -> None:
    semantic, sidecar = make_execution_registry(Path("never-opened.duckdb"))
    sources = make_lazy_sources(
        semantic_registry=semantic,
        sidecar=sidecar,
        session_id="forecast-test",
        store_id="forecast-test",
        action_port=NoIoActionPort(),
    )
    forecast = history(sources).forecast(horizon=periods(4))
    selected = forecast.where(gt(forecast.fields.get("forecast_value"), 0))
    graph = place(selected.rank(selected.fields.get("forecast_value")).limit(2))
    assert len(graph.steps) == 5 and isinstance(graph.steps[0], SourceStep)
    assert all(isinstance(step, PandasStep) for step in graph.steps[1:])
    assert graph.local_steps[0].implementation.local_method == "metric.forecast"
    assert graph.local_steps[0].implementation.source_adapter is None
