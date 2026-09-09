"""Actual worker and atomic Forecast publication through retained authority."""

from itertools import pairwise
from pathlib import Path

import pytest

from marivo.analysis.materialization.targets import EngineTarget, LocalTarget
from marivo.analysis.operators.forecast_contracts import (
    ForecastModel,
    drift,
    naive,
    periods,
    seasonal_naive,
)
from tests.lazy_forecast_fixtures import history, setup_forecast

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("model", [naive(), drift(), seasonal_naive(periods=2)])
@pytest.mark.parametrize("input_kind", ["logical", "engine", "local"])
def test_real_forecast(tmp_path: Path, model: ForecastModel, input_kind: str) -> None:
    runtime, source, database = setup_forecast(tmp_path)
    value = history(source)
    if input_kind != "logical":
        runtime.target = EngineTarget("warehouse") if input_kind == "engine" else LocalTarget()
        retained = value.execute()
        database.rename(tmp_path / "origin.offline")
        runtime.target = LocalTarget()
        logical = retained.forecast(horizon=periods(4), model=model)
    else:
        logical = value.forecast(horizon=periods(4), model=model)
    result = logical.execute()
    frame = result.to_pandas()
    assert len(frame) == 4
    assert frame.training_row_count.tolist() == [4] * 4
    assert result.evidence_digest.finding_count == 4
    assert len(result.findings().items) == 4
    result.show()
    selected = result.rank(result.fields.get("forecast_value")).limit(2).execute()
    assert len(selected.to_pandas()) == 2
    assert selected.evidence_digest.finding_count == 2
    assert logical.execute().state.artifact_ref == result.state.artifact_ref


def test_local_successors_share_frames_and_keep_training_through_empty_selection(
    tmp_path: Path,
) -> None:
    from marivo.analysis.observation.predicates import gt

    runtime, source, _ = setup_forecast(tmp_path)
    forecast = history(source).forecast(horizon=periods(4), model=drift())
    filtered = forecast.where(gt(forecast.fields.get("forecast_value"), 5))
    result = filtered.rank(filtered.fields.get("forecast_value")).limit(2).execute()
    links = runtime.statistics.local_handoffs
    assert len(links) == 4 and all(a[1] == b[0] for a, b in pairwise(links))
    frame = result.to_pandas()
    assert frame.horizon_ordinal.tolist() == [4, 3]
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.forecast_evidence is not None
    assert record.descriptor.forecast_evidence.training.training_row_count == 4
    assert record.descriptor.forecast_evidence.training.residual_df == 2
    assert record.descriptor.forecast_evidence.training.series_count == 1
    empty = result.where(gt(result.fields.get("forecast_value"), 100)).execute()
    assert empty.evidence_digest.finding_count == 0 and empty.to_pandas().empty


def test_sampling_disclosure_survives_forecast_and_selection(tmp_path: Path) -> None:
    from marivo._temporal import builtin_grain, time_scope
    from marivo.analysis.observation.sampling import engine_sample
    from marivo.analysis.operators.forecast_contracts import ForecastSemantics
    from marivo.refs import ref

    runtime, source, _ = setup_forecast(tmp_path)
    population = source.population(ref.entity("sales.orders")).sample(
        engine_sample(target_rows=100, seed=1)
    )
    metric = (
        source.observe(
            ref.metric("sales.revenue"),
            population=population,
            time_scope=time_scope(start="2026-02-01", end="2026-02-05"),
        )
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=builtin_grain("day"))
        .aggregate()
    )
    logical = metric.forecast(horizon=periods(2))
    meaning = logical.row_contract.family_semantics
    assert isinstance(meaning, ForecastSemantics) and meaning.approximation == "sampled_population"
    result = logical.execute()
    selected = result.limit(1).execute()
    assert "sampled_population" in selected.contract().render()
    assert selected.evidence_digest.finding_count == 1


def test_certified_custom_period_forecast_uses_retained_snapshot(tmp_path: Path) -> None:
    from dataclasses import replace
    from datetime import date

    from marivo._temporal import certify_period_calendar, semantic_grain, time_scope
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from marivo.refs import ref
    from marivo.semantic.ir import PeriodCalendarIR
    from tests.lazy_execution_fixtures import make_execution_registry

    runtime, _, database = setup_forecast(tmp_path, (1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
    registry, sidecar = make_execution_registry(database)
    calendar = ref.period_calendar("sales.fiscal")
    time = ref.time_dimension("sales.orders.order_time")
    snapshot = certify_period_calendar(
        calendar_ref=calendar,
        boundary_timezone="UTC",
        coverage=(date(2026, 2, 1), date(2026, 2, 13)),
        rows=tuple({"date": date(2026, 2, day), "period": (day - 1) // 2} for day in range(1, 13)),
        levels={"reporting_period": "period"},
    )
    template = registry.metrics["sales.revenue"]
    registry = replace(
        registry,
        period_calendars={
            calendar.path: PeriodCalendarIR(
                calendar.path,
                "sales",
                "fiscal",
                time.path,
                "UTC",
                ("2026-02-01", "2026-02-13"),
                (("reporting_period", "sales.orders.channel"),),
                template.ai_context,
                "fiscal",
                template.location,
            )
        },
    )
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=runtime,
        session_id=runtime.session_ref,
        store_id=runtime.store.store_id,
        period_calendar_snapshots=(snapshot,),
    )
    h = (
        sources.observe(
            ref.metric("sales.revenue"), time_scope=time_scope(start="2026-02-01", end="2026-02-07")
        )
        .with_time_axis(time, grain=semantic_grain(calendar=calendar, level="reporting_period"))
        .aggregate()
        .execute()
    )
    database.rename(tmp_path / "origin.offline")
    result = h.forecast(horizon=periods(3), model=seasonal_naive(periods=2)).execute()
    frame = result.to_pandas()
    assert frame.order_time.tolist() == [date(2026, 2, d) for d in (7, 9, 11)]
    assert frame.forecast_value.tolist() == [7.0, 11.0, 7.0]
    from marivo.analysis.materialization.errors import MaterializationError

    with pytest.raises(MaterializationError):
        h.forecast(horizon=periods(4)).execute()


def test_semantic_approximation_keeps_numeric_history_source_private(tmp_path: Path) -> None:
    from marivo._temporal import builtin_grain, time_scope
    from marivo.refs import ref
    from marivo.semantic._quantile import quantile_metric
    from tests.lazy_distribution_fixtures import (
        guard_distribution_transport,
        make_distribution_registry,
    )

    runtime, _, database = setup_forecast(tmp_path)
    registry, sidecar = make_distribution_registry(database)
    source = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    metric = (
        source.observe(
            quantile_metric(ref.metric("sales.revenue"), method="duckdb_tdigest@v1"),
            time_scope=time_scope(start="2026-02-01", end="2026-02-05"),
        )
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=builtin_grain("day"))
        .aggregate()
    )
    with guard_distribution_transport():
        result = metric.forecast(horizon=periods(2)).execute()
    assert "semantic_percentile" in result.contract().render()
    assert result.evidence_digest.finding_count == 2
