from __future__ import annotations

import pandas as pd
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import (
    ForecastInputQualityError,
    ForecastInsufficientHistoryError,
    ForecastPolicyError,
    ForecastShapeUnsupportedError,
)
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.session._load import load_frame
from tests.shared_fixtures import seeded_time_series_metric_frame


@pytest.fixture(autouse=True)
def _reset_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def test_naive_time_series_constant(tmp_path):
    session = session_attach.get_or_create(name="demo")
    history = seeded_time_series_metric_frame(
        session=session,
        n_buckets=10,
        value_pattern="constant",
    )

    result = mv.forecast(history, horizon=3, model="naive", session=session)
    df = result.to_pandas()

    assert result.meta.kind == "forecast_frame"
    assert df["predicted"].tolist() == [10.0, 10.0, 10.0]
    assert df["lower"].tolist() == [10.0, 10.0, 10.0]
    assert df["upper"].tolist() == [10.0, 10.0, 10.0]
    assert df["reason_code"].tolist() == [
        "constant_history",
        "constant_history",
        "constant_history",
    ]


def test_seasonal_naive_dow_period_7(tmp_path):
    session = session_attach.get_or_create(name="demo")
    history = seeded_time_series_metric_frame(
        session=session, n_buckets=21, value_pattern="seasonal_7"
    )

    result = mv.forecast(
        history,
        horizon=7,
        model="seasonal_naive",
        seasonality_period=7,
        session=session,
    )

    assert result.to_pandas()["predicted"].tolist() == [
        100.0,
        103.0,
        106.0,
        109.0,
        112.0,
        115.0,
        118.0,
    ]
    assert result.meta.seasonality_period == 7


def test_drift_trending_series(tmp_path):
    session = session_attach.get_or_create(name="demo")
    history = seeded_time_series_metric_frame(session=session, n_buckets=5, value_pattern="linear")

    result = mv.forecast(history, horizon=2, model="drift", session=session)

    assert result.to_pandas()["predicted"].round(6).tolist() == [15.0, 16.0]


def test_interval_width_grows_with_horizon(tmp_path):
    session = session_attach.get_or_create(name="demo")
    history = seeded_time_series_metric_frame(session=session, n_buckets=20, value_pattern="noisy")

    df = mv.forecast(history, horizon=5, model="naive", session=session).to_pandas()
    width = df["upper"] - df["lower"]
    assert width.iloc[-1] > width.iloc[0]


def test_panel_per_segment_and_insufficient_history(tmp_path):
    session = session_attach.get_or_create(name="demo")
    full = seeded_time_series_metric_frame(
        session=session,
        n_buckets=4,
        segments=["US"],
        value_pattern="linear",
    )
    short_rows = [{"segment": "CA", "time": pd.Timestamp("2026-01-01"), "value": 3.0}]
    combined = pd.concat([full.to_pandas(), pd.DataFrame(short_rows)], ignore_index=True)
    history = MetricFrame.from_dataframe(
        combined,
        metric_id="sales.revenue",
        axes={"time": {"field": "time", "grain": "day"}, "dimensions": [{"field": "segment"}]},
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind="panel",
        semantic_model="sales",
        window={"start": "2026-01-01", "end": "2026-01-04", "grain": "day", "time_field": "time"},
        session=session,
    )

    df = mv.forecast(history, horizon=2, model="naive", session=session).to_pandas()

    assert len(df) == 4
    assert set(df["segment"]) == {"US", "CA"}
    assert df[df["segment"] == "CA"]["reason_code"].tolist() == [
        "insufficient_history",
        "insufficient_history",
    ]
    assert df[df["segment"] == "CA"]["predicted"].isna().all()


def test_forecast_errors_and_persistence(tmp_path):
    session = session_attach.get_or_create(name="demo")
    history = seeded_time_series_metric_frame(session=session, n_buckets=5)

    with pytest.raises(ForecastPolicyError):
        mv.forecast(history, horizon=0, session=session)
    with pytest.raises(ForecastPolicyError):
        mv.forecast(history, horizon=1, interval_level=1.0, session=session)
    with pytest.raises(ForecastInsufficientHistoryError):
        mv.forecast(
            history, horizon=1, model="seasonal_naive", seasonality_period=7, session=session
        )

    scalar = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [1.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"field": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=session,
    )
    with pytest.raises(ForecastShapeUnsupportedError):
        mv.forecast(scalar, horizon=1, session=session)

    with_nan = history.to_pandas()
    with_nan.loc[0, "value"] = None
    nan_frame = MetricFrame.from_dataframe(
        with_nan,
        metric_id="sales.revenue",
        axes=history.meta.axes,
        measure=history.meta.measure,
        semantic_kind="time_series",
        semantic_model="sales",
        window=history.meta.window,
        session=session,
    )
    with pytest.raises(ForecastInputQualityError):
        mv.forecast(nan_frame, horizon=1, session=session)

    gap = history.to_pandas().drop(index=[2])
    gap_frame = MetricFrame.from_dataframe(
        gap,
        metric_id="sales.revenue",
        axes=history.meta.axes,
        measure=history.meta.measure,
        semantic_kind="time_series",
        semantic_model="sales",
        window=history.meta.window,
        session=session,
    )
    with pytest.raises(ForecastInputQualityError):
        mv.forecast(gap_frame, horizon=1, session=session)

    result = mv.forecast(history, horizon=2, model="naive", session=session)
    loaded = load_frame(result.ref, session=session)
    assert loaded.meta.kind == "forecast_frame"
    assert loaded.lineage.steps[-1].intent == "forecast"
