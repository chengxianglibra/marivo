from __future__ import annotations

from datetime import date

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo._temporal import (
    TemporalResolver,
    TemporalSnapshotStore,
    certify_period_calendar,
)
from marivo.analysis.errors import (
    ForecastInputQualityError,
    ForecastInsufficientHistoryError,
    ForecastPolicyError,
    ForecastShapeUnsupportedError,
)
from marivo.analysis.session._load import load_frame
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref
from tests.run_read_helpers import run_arguments
from tests.shared_fixtures import (
    fiscal_analysis_project_files,
    make_metric_frame,
    publish_fiscal_calendar_artifact,
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
    jobs = [job for job in session.runs(limit=100).items if job.capability_id == "forecast"]
    assert run_arguments(session.get_run(jobs[0].run_id))["measure_column"] == "revenue"


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
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
        grain=mv.grain("day"),
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
    run_count = len(session._store.list_runs(session.id))

    with pytest.raises(ForecastPolicyError):
        session.forecast(history, horizon=0)
    with pytest.raises(ForecastPolicyError):
        session.forecast(history, horizon=1, interval_level=1.0)
    assert len(session._store.list_runs(session.id)) == run_count
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

    unsupported_grain = make_metric_frame(
        pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=2, freq="5min"),
                "value": [1.0, 2.0],
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "time", "grain": "5minute"}},
        measure={"field": "value"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={
            "start": "2026-01-01",
            "end": "2026-01-02",
            "grain": "5minute",
            "time_dimension": "time",
        },
        session=session,
    )
    with pytest.raises(ForecastShapeUnsupportedError, match="does not support grain"):
        session.forecast(unsupported_grain, horizon=1)

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


def _fiscal_forecast_project_files() -> dict[str, str]:
    files = fiscal_analysis_project_files()
    files["sales/metrics.py"] += (
        "\nregion = ms.dimension_column(name='region', entity=events, column='region')\n"
    )
    return files


def _seed_fiscal_forecast_backend() -> ibis.BaseBackend:
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql(
        "CREATE TABLE calendar (calendar_date DATE, fiscal_week VARCHAR, fiscal_month VARCHAR)"
    )
    calendar_rows = []
    cursor = date(2026, 1, 1)
    while cursor < date(2026, 3, 1):
        month = "M1" if cursor.month == 1 else "M2"
        week = f"{month}-W{((cursor.day - 1) // 7) + 1}"
        calendar_rows.append((cursor.isoformat(), week, month))
        cursor = cursor + pd.Timedelta(days=1).to_pytimedelta()
    backend.raw_sql(
        "INSERT INTO calendar VALUES "
        + ",".join(f"(DATE '{day}', '{week}', '{month}')" for day, week, month in calendar_rows)
    )
    backend.raw_sql("CREATE TABLE events (event_date DATE, amount DOUBLE, region VARCHAR)")
    event_rows = []
    for index, (day, _week, _month) in enumerate(calendar_rows):
        if (
            index == 0
            or day.endswith("01")
            or day.endswith("08")
            or day.endswith("15")
            or day.endswith("22")
            or day.endswith("29")
        ):
            event_rows.extend(
                [
                    f"(DATE '{day}', {index + 1}.0, 'US')",
                    f"(DATE '{day}', {index + 2}.0, 'CA')",
                ]
            )
    backend.raw_sql("INSERT INTO events VALUES " + ",".join(event_rows))
    return backend


def test_semantic_period_forecast_uses_certified_future_keys_and_contract(
    semantic_project_factory, monkeypatch
):
    project = semantic_project_factory(_fiscal_forecast_project_files())
    monkeypatch.chdir(project.workspace_dir)
    backend = _seed_fiscal_forecast_backend()
    catalog = ms.SemanticCatalog(project)
    calendar_ref = ms.ref.period_calendar("sales.fiscal")
    catalog.require(calendar_ref)
    publish_fiscal_calendar_artifact(catalog)
    session = session_attach.get_or_create(
        name="semantic-forecast",
        backends={"warehouse": lambda: backend},
        report_timezone="Asia/Shanghai",
    )
    history = session.observe(
        session.catalog.require(ms.ref.metric("sales.gmv")).ref,
        time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01"),
        grain=session.catalog.period_calendars.get("sales.fiscal").grain("fiscal_week"),
    )

    with pytest.raises(ForecastShapeUnsupportedError, match="explicit seasonality_period"):
        session.forecast(history, horizon=1)

    result = session.forecast(history, horizon=2, model="naive")
    output = result.to_pandas()
    assert result.meta.horizon_unit == "fiscal_week"
    assert output["period_key"].tolist() == ["M2-W1", "M2-W2"]
    assert output["period_start"].tolist() == [date(2026, 2, 1), date(2026, 2, 8)]
    assert output["period_end"].tolist() == [date(2026, 2, 8), date(2026, 2, 15)]
    assert output["period_ordinal"].tolist() == [5, 6]
    assert output["is_complete"].tolist() == [True, True]
    assert result.meta.temporal_contract is not None
    assert result.meta.temporal_contract.observation_period is not None
    assert result.meta.temporal_contract.observation_period.kind == "semantic_period"
    assert result.meta.temporal_contract.output_period_keys == ("M2-W1", "M2-W2")
    assert result.contract().temporal_contract == result.meta.temporal_contract


def test_semantic_period_forecast_validates_panel_sequences_and_ordinal_models(
    semantic_project_factory, monkeypatch
):
    project = semantic_project_factory(_fiscal_forecast_project_files())
    monkeypatch.chdir(project.workspace_dir)
    backend = _seed_fiscal_forecast_backend()
    catalog = ms.SemanticCatalog(project)
    calendar_ref = ms.ref.period_calendar("sales.fiscal")
    catalog.require(calendar_ref)
    publish_fiscal_calendar_artifact(catalog)
    session = session_attach.get_or_create(
        name="semantic-forecast-panel", backends={"warehouse": lambda: backend}
    )
    history = session.observe(
        session.catalog.require(ms.ref.metric("sales.gmv")).ref,
        time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01"),
        grain=session.catalog.period_calendars.get("sales.fiscal").grain("fiscal_week"),
        dimensions=[ms.ref.dimension("sales.events.region")],
    )
    drift = session.forecast(history, horizon=1, model="drift")
    assert set(drift.to_pandas()["region"]) == {"CA", "US"}
    assert drift.meta.train_row_count_per_segment == {"CA": 5, "US": 5}

    seasonal = session.forecast(
        history,
        horizon=1,
        model="seasonal_naive",
        seasonality_period=2,
    )
    assert seasonal.meta.seasonality_period == 2

    broken = history._dataframe_copy()
    broken.loc[broken.index[0], "period_ordinal"] = 99
    broken_frame = make_metric_frame(
        broken,
        metric_id="sales.gmv",
        axes=history.meta.axes,
        measure=history.meta.measure,
        semantic_kind="panel",
        semantic_model="sales",
        window=None,
        session=session,
    )
    broken_frame.meta = broken_frame.meta.model_copy(
        update={"temporal_contract": history.meta.temporal_contract}
    )
    with pytest.raises(ForecastShapeUnsupportedError, match="does not match its snapshot"):
        session.forecast(broken_frame, horizon=1, model="naive")


def test_semantic_period_forecast_replays_exact_history_snapshot_without_datasource_reads(
    semantic_project_factory, monkeypatch
):
    project = semantic_project_factory(_fiscal_forecast_project_files())
    monkeypatch.chdir(project.workspace_dir)
    backend = _seed_fiscal_forecast_backend()
    catalog = ms.SemanticCatalog(project)
    calendar_ref = ms.ref.period_calendar("sales.fiscal")
    catalog.require(calendar_ref)
    publish_fiscal_calendar_artifact(catalog)
    factory_calls: list[str] = []

    def backend_factory():
        factory_calls.append("warehouse")
        return backend

    session = session_attach.get_or_create(
        name="semantic-forecast-recovery",
        backends={"warehouse": backend_factory},
    )
    history = session.observe(
        session.catalog.require(ms.ref.metric("sales.gmv")).ref,
        time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01"),
        grain=session.catalog.period_calendars.get("sales.fiscal").grain("fiscal_week"),
    )
    calls_after_observe = len(factory_calls)
    binding = history.meta.temporal_contract.observation_period
    assert binding is not None
    store = TemporalSnapshotStore(session.project_root)
    original = store.load_exact(calendar_ref, snapshot_digest=binding.snapshot_digest)
    resolver = TemporalResolver(original)
    changed_until = resolver.period_on("fiscal_week", original.coverage[0]).end_date
    changed_rows = []
    for offset in range((original.coverage[1] - original.coverage[0]).days):
        current_date = original.coverage[0] + pd.Timedelta(days=offset).to_pytimedelta()
        row = {"date": current_date}
        for level in ("fiscal_week", "fiscal_month"):
            row[level] = resolver.period_on(level, current_date).key
        if current_date < changed_until:
            row["fiscal_week"] = f"{row['fiscal_week']}-changed"
        changed_rows.append(row)
    changed = certify_period_calendar(
        calendar_ref=calendar_ref,
        boundary_timezone=original.boundary_timezone,
        coverage=original.coverage,
        rows=changed_rows,
        levels={"fiscal_week": "fiscal_week", "fiscal_month": "fiscal_month"},
    )
    store.publish(changed, definition_digest="changed-definition")

    loaded_history = load_frame(history.ref, session=session)
    replayed = session.forecast(loaded_history, horizon=1, model="naive")
    assert replayed.to_pandas()["period_key"].tolist() == ["M2-W1"]
    assert len(factory_calls) == calls_after_observe
    assert (
        replayed.meta.temporal_contract.observation_period.snapshot_digest
        == binding.snapshot_digest
    )

    snapshot_path = store._directory(calendar_ref) / f"{binding.snapshot_digest}.json"
    snapshot_path.unlink()
    with pytest.raises(ForecastShapeUnsupportedError) as exc_info:
        session.forecast(loaded_history, horizon=1, model="naive")
    assert exc_info.value._context["case"] == "period_snapshot_unavailable"
    assert len(factory_calls) == calls_after_observe
