from __future__ import annotations

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    ForecastInputQualityError,
    ForecastInsufficientHistoryError,
    ForecastPolicyError,
    ForecastShapeUnsupportedError,
)
from marivo.analysis.session._load import load_frame
from tests.shared_fixtures import make_metric_frame, seeded_time_series_metric_frame


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

    result = session.forecast(history, horizon=3, model="naive")
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

    result = session.forecast(
        history,
        horizon=7,
        model="seasonal_naive",
        seasonality_period=7,
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

    result = session.forecast(history, horizon=2, model="drift")

    assert result.to_pandas()["predicted"].round(6).tolist() == [15.0, 16.0]


def test_interval_width_grows_with_horizon(tmp_path):
    session = session_attach.get_or_create(name="demo")
    history = seeded_time_series_metric_frame(session=session, n_buckets=20, value_pattern="noisy")

    df = session.forecast(history, horizon=5, model="naive").to_pandas()
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
    combined = pd.concat([full._dataframe_copy(), pd.DataFrame(short_rows)], ignore_index=True)
    history = make_metric_frame(
        combined,
        metric_id="sales.revenue",
        axes={"time": {"field": "time", "grain": "day"}, "dimensions": [{"field": "segment"}]},
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind="panel",
        semantic_model="sales",
        window={
            "start": "2026-01-01",
            "end": "2026-01-04",
            "grain": "day",
            "time_dimension": "time",
        },
        session=session,
    )

    df = session.forecast(history, horizon=2, model="naive").to_pandas()

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
        session.forecast(history, horizon=0)
    with pytest.raises(ForecastPolicyError):
        session.forecast(history, horizon=1, interval_level=1.0)
    with pytest.raises(ForecastInsufficientHistoryError):
        session.forecast(history, horizon=1, model="seasonal_naive", seasonality_period=7)

    scalar = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"field": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=session,
    )
    with pytest.raises(ForecastShapeUnsupportedError):
        session.forecast(scalar, horizon=1)

    with_nan = history._dataframe_copy()
    with_nan.loc[0, "value"] = None
    nan_frame = make_metric_frame(
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
        session.forecast(nan_frame, horizon=1)

    gap = history.to_pandas().drop(index=[2])
    gap_frame = make_metric_frame(
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
        session.forecast(gap_frame, horizon=1)

    result = session.forecast(history, horizon=2, model="naive")
    loaded = load_frame(result.ref, session=session)
    assert loaded.meta.kind == "forecast_frame"
    assert loaded.lineage.steps[-1].intent == "forecast"
