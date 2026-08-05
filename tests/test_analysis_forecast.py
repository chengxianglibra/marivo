from __future__ import annotations

import ibis
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
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref
from tests.shared_fixtures import (
    make_metric_frame,
    seeded_time_series_metric_frame,
)


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


def test_forecast_resolves_public_value_name_and_excludes_numeric_dimension(tmp_path):
    session = session_attach.get_or_create(name="demo")
    times = pd.date_range("2026-01-01", periods=4, freq="D")
    history = make_metric_frame(
        pd.DataFrame(
            [
                {"segment": segment, "time": time, "value": float(index + segment)}
                for segment in [1, 2]
                for index, time in enumerate(times)
            ]
        ),
        metric_id="sales.revenue",
        axes={
            "time": {"role": "time", "field": "time", "grain": "day"},
            "segment": {"role": "dimension", "field": "segment"},
        },
        measure={"name": "revenue"},
        semantic_kind="panel",
        semantic_model="sales",
        session=session,
    )

    inferred = session.forecast(history, horizon=1, model="naive")
    explicit = session.forecast(
        history,
        horizon=1,
        model="naive",
        measure_column=history.value_columns[0],
    )

    assert len(inferred) == 2
    assert len(explicit) == 2
    jobs = [job for job in session.jobs() if job.intent == "forecast"]
    assert session.job(jobs[-1].id)["params"]["measure_column"] == "revenue"


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
    history = make_metric_frame(
        pd.DataFrame(
            [
                {"segment": "US", "time": pd.Timestamp("2026-01-01"), "value": 10.0},
                {"segment": "CA", "time": pd.Timestamp("2026-01-01"), "value": 3.0},
            ]
        ),
        metric_id="sales.revenue",
        axes={
            "time": {"role": "time", "field": "time", "grain": "day"},
            "segment": {"role": "dimension", "field": "segment"},
        },
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
    assert set(df["reason_code"]) == {"insufficient_history"}
    assert df["predicted"].isna().all()


def test_forecast_observe_panel_uses_canonical_dimension_axes(tmp_path, semantic_project_factory):
    semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/model.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "orders = ms.entity(\n"
                "    name='orders', datasource=ms.ref.datasource('warehouse'),\n"
                "    source=md.table('orders'),\n"
                ")\n"
                "@ms.time_dimension(entity=orders, granularity='day', is_default=True)\n"
                "def order_date(orders):\n"
                "    return orders.created_at.cast('date')\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region.upper()\n"
                "@ms.metric(entities=[orders], additivity='additive')\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 'north', 100),"
        "(2, DATE '2026-07-02', 20.0, 'north', 101),"
        "(3, DATE '2026-07-01', 30.0, 'south', 200),"
        "(4, DATE '2026-07-02', 40.0, 'south', 201)"
    )
    session = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
    )
    history = session.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
        dimensions=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )

    result = session.forecast(history, horizon=2, model="naive")
    output = result.to_pandas()

    assert len(output) == 4
    assert set(output["region"]) == {"NORTH", "SOUTH"}
    assert result.meta.segment_dimensions == ["region"]
    assert result.meta.train_row_count_per_segment == {"NORTH": 2, "SOUTH": 2}


def test_forecast_panel_rejects_missing_bucket_within_segment(tmp_path):
    session = session_attach.get_or_create(name="demo")
    months = pd.date_range("2026-01-01", periods=4, freq="MS")
    history = make_metric_frame(
        pd.DataFrame(
            [
                {"major_category": category, "time": month, "value": float(index + 1)}
                for category in ("Baking", "Produce")
                for index, month in enumerate(months)
                if not (category == "Baking" and month == pd.Timestamp("2026-04-01"))
            ]
        ),
        metric_id="sales.revenue",
        axes={
            "time": {"role": "time", "column": "time", "grain": "month"},
            "major_category": {
                "role": "dimension",
                "column": "major_category",
            },
        },
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind="panel",
        semantic_model="sales",
        window={
            "start": "2026-01-01",
            "end": "2026-05-01",
            "grain": "month",
            "time_dimension": "time",
        },
        session=session,
    )

    with pytest.raises(ForecastInputQualityError) as exc_info:
        session.forecast(history, horizon=4, model="naive")

    error = exc_info.value
    assert error.message == "forecast panel history has missing time buckets within segments"
    assert error._context == {
        "segment_dimensions": ["major_category"],
        "invalid_segment_count": 1,
        "invalid_segments": [
            {
                "keys": {"major_category": "Baking"},
                "missing_bucket_count": 1,
                "missing_buckets": ["2026-04-01T00:00:00"],
            }
        ],
    }


def test_forecast_panel_without_dimension_axis_raises_typed_shape_error(tmp_path):
    session = session_attach.get_or_create(name="demo")
    history = make_metric_frame(
        pd.DataFrame(
            [
                {"time": pd.Timestamp("2026-01-01"), "value": 1.0},
                {"time": pd.Timestamp("2026-02-01"), "value": 2.0},
            ]
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "time", "grain": "month"}},
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind="panel",
        semantic_model="sales",
        session=session,
    )

    with pytest.raises(
        ForecastShapeUnsupportedError,
        match="forecast panel input requires at least one dimension axis",
    ):
        session.forecast(history, horizon=1, model="naive")


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

    gap = history._dataframe_copy().drop(index=[2])
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
