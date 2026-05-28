from __future__ import annotations

import json

import ibis
import pandas as pd
import pytest

import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import AlignmentFailedError, PanelGrainMismatchError
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.intents.compare import compare
from marivo.analysis_py.intents.observe import observe
from marivo.analysis_py.policies import AlignmentPolicy
from marivo.analysis_py.refs import CalendarRef, DimensionRef, MetricRef


@pytest.fixture(autouse=True)
def _session_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def _seed(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-06-24', 8.0, 'north', 100),"
        "(2, DATE '2026-06-25', 18.0, 'north', 101),"
        "(3, DATE '2026-06-24', 28.0, 'south', 200),"
        "(4, DATE '2026-06-25', 38.0, 'south', 201),"
        "(5, DATE '2026-07-01', 10.0, 'north', 102),"
        "(6, DATE '2026-07-02', 20.0, 'north', 103),"
        "(7, DATE '2026-07-01', 30.0, 'south', 202),"
        "(8, DATE '2026-07-02', 40.0, 'south', 203)"
    )


def _bootstrap_sales(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource_py as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        "@ms.dataset(name='orders', datasource='warehouse')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.field(dataset=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _session(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    return session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})


def _panel(session, *, start: str, end: str, grain: str = "day"):
    return observe(
        MetricRef("sales.revenue"),
        window={"start": start, "end": end, "grain": grain},
        dimensions=[DimensionRef("region")],
        session=session,
    )


def test_window_bucket_aligns_equal_length_panel_by_ordinal_bucket(tmp_path):
    s = _session(tmp_path)
    cur = _panel(s, start="2026-07-01", end="2026-07-02")
    prev = _panel(s, start="2026-06-24", end="2026-06-25")

    delta = compare(cur, prev, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    df = delta.to_pandas()
    assert {"bucket_start", "bucket_start_b", "region", "current", "baseline"} <= set(df.columns)
    north = df[df["region"] == "NORTH"].sort_values("bucket_start").reset_index(drop=True)
    assert list(north["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert list(north["bucket_start_b"].astype(str)) == ["2026-06-24", "2026-06-25"]
    assert list(north["delta"]) == [pytest.approx(2.0), pytest.approx(2.0)]
    assert delta.meta.alignment["mode"] == "ordinal_bucket"


def test_window_bucket_panel_different_expected_counts_explains_requirement(tmp_path):
    s = _session(tmp_path)
    cur = _panel(s, start="2026-07-01", end="2026-07-02")
    prev = _panel(s, start="2026-06-24", end="2026-06-24")

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(cur, prev, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    assert "equal expected bucket counts" in str(exc_info.value)
    assert exc_info.value.details["kind"] == "WindowBucketExpectedCountMismatch"


def _panel_metric(
    session,
    rows,
    *,
    axes: dict[str, object] | None = None,
    window: dict[str, object] | None = None,
):
    return MetricFrame.from_dataframe(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes=axes
        or {
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_field": "order_date",
            },
            "region": {"role": "dimension", "column": "region"},
        },
        measure={"name": "value"},
        semantic_kind="panel",
        semantic_model="sales",
        window=window,
        session=session,
    )


def test_window_bucket_panel_sparse_segment_uses_window_spine():
    s = session_attach.get_or_create(name="demo")
    current_rows = [
        {
            "bucket_start": f"2026-05-12 {hour:02d}:00:00",
            "region": "WEB",
            "value": float(hour),
        }
        for hour in range(24)
    ]
    baseline_rows = [
        {
            "bucket_start": f"2026-05-05 {hour:02d}:00:00",
            "region": "WEB",
            "value": float(hour + 100),
        }
        for hour in range(11)
    ]
    axes = {
        "time": {"role": "time", "column": "bucket_start", "grain": "hour"},
        "region": {"role": "dimension", "column": "region"},
    }
    current = _panel_metric(
        s,
        current_rows,
        axes=axes,
        window={"start": "2026-05-12", "end": "2026-05-12", "grain": "hour"},
    )
    baseline = _panel_metric(
        s,
        baseline_rows,
        axes=axes,
        window={"start": "2026-05-05", "end": "2026-05-05", "grain": "hour"},
    )

    out = compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    df = out.to_pandas()
    assert len(df) == 24
    assert list(df["bucket_start"].astype(str).head(2)) == [
        "2026-05-12 00:00:00",
        "2026-05-12 01:00:00",
    ]
    assert list(df["bucket_start_b"].astype(str).head(2)) == [
        "2026-05-05 00:00:00",
        "2026-05-05 01:00:00",
    ]
    assert df.iloc[10]["baseline"] == pytest.approx(110.0)
    assert pd.isna(df.iloc[11]["baseline"])
    assert pd.isna(df.iloc[11]["delta"])
    assert out.meta.alignment["coverage"]["baseline"]["missing_buckets"] == 13
    assert out.meta.alignment["segment_info"]["coverage"]["baseline"]["missing_buckets"] == 13


def _write_calendar(tmp_path):
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True)
    (calendar_dir / "cn_holidays.json").write_text(
        json.dumps(
            {
                "name": "cn_holidays",
                "timezone": "Asia/Shanghai",
                "holidays": [],
                "adjusted_workdays": [],
            }
        ),
        encoding="utf-8",
    )


def test_compare_panel_window_bucket(tmp_path):
    s = _session(tmp_path)
    current = _panel(s, start="2026-07-01", end="2026-07-03")
    baseline = _panel(s, start="2026-07-01", end="2026-07-03")

    out = compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    assert out.meta.semantic_kind == "panel"
    assert out.meta.alignment["segment_info"] == {
        "segment_count": 2,
        "a_only_segments_count": 0,
        "b_only_segments_count": 0,
    }
    assert out.meta.alignment["axes"] == current.meta.axes
    df = out.to_pandas()
    assert list(df.columns) == [
        "bucket_start",
        "region",
        "current",
        "baseline",
        "delta",
        "pct_change",
    ]
    by_key = {(str(row.bucket_start), row.region): row for row in df.itertuples()}
    assert by_key[("2026-07-01", "NORTH")].delta == pytest.approx(0.0)
    assert by_key[("2026-07-02", "SOUTH")].delta == pytest.approx(0.0)


def test_compare_panel_window_bucket_outer_joins_bucket_keys():
    s = session_attach.get_or_create(name="demo")
    current = _panel_metric(
        s,
        [
            {"bucket_start": "2026-07-01", "region": "NORTH", "value": 10.0},
            {"bucket_start": "2026-07-03", "region": "NORTH", "value": 30.0},
        ],
    )
    baseline = _panel_metric(
        s,
        [
            {"bucket_start": "2026-07-01", "region": "NORTH", "value": 8.0},
            {"bucket_start": "2026-07-02", "region": "NORTH", "value": 20.0},
        ],
    )

    out = compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    df = out.to_pandas()
    assert list(df.columns) == [
        "bucket_start",
        "region",
        "current",
        "baseline",
        "delta",
        "pct_change",
    ]
    by_bucket = {str(row.bucket_start): row for row in df.itertuples()}
    assert by_bucket["2026-07-01"].current == pytest.approx(10.0)
    assert by_bucket["2026-07-01"].baseline == pytest.approx(8.0)
    assert by_bucket["2026-07-01"].delta == pytest.approx(2.0)
    assert pd.isna(by_bucket["2026-07-02"].current)
    assert by_bucket["2026-07-02"].baseline == pytest.approx(20.0)
    assert pd.isna(by_bucket["2026-07-02"].delta)
    assert by_bucket["2026-07-03"].current == pytest.approx(30.0)
    assert pd.isna(by_bucket["2026-07-03"].baseline)
    assert pd.isna(by_bucket["2026-07-03"].delta)


def test_compare_panel_calendar_alignment_one_sided_segment_has_consistent_columns(tmp_path):
    _write_calendar(tmp_path)
    s = session_attach.get_or_create(name="demo", timezone="Asia/Shanghai")
    current = _panel_metric(
        s,
        [
            {"bucket_start": "2026-05-05", "region": "APP", "value": 10.0},
            {"bucket_start": "2026-05-05", "region": "WEB", "value": 100.0},
        ],
    )
    baseline = _panel_metric(
        s,
        [{"bucket_start": "2026-04-07", "region": "WEB", "value": 80.0}],
    )

    out = compare(
        current,
        baseline,
        alignment=AlignmentPolicy(
            kind="dow_aligned",
            calendar=CalendarRef("cn_holidays"),
            period="month",
        ),
        session=s,
    )

    df = out.to_pandas()
    assert list(df.columns) == [
        "region",
        "align_key",
        "align_quality",
        "bucket_start_a",
        "bucket_start_b",
        "current",
        "baseline",
        "delta",
        "pct_change",
    ]
    by_region = {row.region: row for row in df.itertuples()}
    assert by_region["APP"].align_quality == "unmatched"
    assert by_region["APP"].bucket_start_a == "2026-05-05"
    assert pd.isna(by_region["APP"].bucket_start_b)
    assert by_region["WEB"].align_quality == "exact"
    assert by_region["WEB"].bucket_start_a == "2026-05-05"
    assert by_region["WEB"].bucket_start_b == "2026-04-07"


def test_compare_panel_grain_mismatch(tmp_path):
    s = _session(tmp_path)
    current = _panel(s, start="2026-07-01", end="2026-07-03", grain="day")
    baseline = _panel(s, start="2026-06-01", end="2026-08-01", grain="month")

    with pytest.raises(PanelGrainMismatchError):
        compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)


def test_compare_panel_grain_mismatch_uses_time_axis_role(tmp_path):
    s = session_attach.get_or_create(name="demo")
    current = _panel_metric(
        s,
        [{"bucket_start": "2026-07-01", "region": "NORTH", "value": 10.0}],
        axes={
            "event_time": {"role": "time", "column": "bucket_start", "grain": "day"},
            "region": {"role": "dimension", "column": "region"},
        },
    )
    baseline = _panel_metric(
        s,
        [{"bucket_start": "2026-06-01", "region": "NORTH", "value": 8.0}],
        axes={
            "event_time": {"role": "time", "column": "bucket_start", "grain": "month"},
            "region": {"role": "dimension", "column": "region"},
        },
    )

    with pytest.raises(PanelGrainMismatchError):
        compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"), session=s)
