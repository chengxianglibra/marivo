"""observe relative-window behavior against seeded DuckDB."""

from datetime import datetime
from typing import get_type_hints
from zoneinfo import ZoneInfo

import ibis
import pytest

import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import MetricShapeUnsupportedError
from marivo.analysis_py.intents.observe import observe
from marivo.analysis_py.windows.spec import WindowInput


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _bootstrap_sales(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        "@ms.datasource(name='warehouse')\n"
        "def warehouse(): ...\n"
        "\n"
        "@ms.dataset(name='orders', datasource=warehouse)\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.time_field(dataset='orders', data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.order_date.cast('date')\n"
        "\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_sales_orders(con):
    con.raw_sql("CREATE TABLE orders (order_date DATE, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(DATE '2026-05-01', 10.0),"
        "(DATE '2026-05-02', 20.0),"
        "(DATE '2026-05-24', 30.0)"
    )


def test_observe_window_type_hint_matches_supported_window_input_forms():
    assert get_type_hints(observe)["window"] == WindowInput


def _bootstrap_multi_dataset(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        "@ms.datasource(name='warehouse')\n"
        "def warehouse(): ...\n"
        "\n"
        "@ms.dataset(name='orders', datasource=warehouse)\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.time_field(dataset='orders', data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.order_date.cast('date')\n"
        "\n"
        "@ms.dataset(name='refunds', datasource=warehouse)\n"
        "def refunds(backend):\n"
        "    return backend.table('refunds')\n"
        "\n"
        "@ms.time_field(dataset='refunds', data_type='date', granularity='day')\n"
        "def refund_date(refunds):\n"
        "    return refunds.refund_date.cast('date')\n"
        "\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def net(orders, refunds):\n"
        "    return orders.amount.sum() - refunds.amount.sum()\n"
    )


def _seed_multi_dataset(con):
    con.raw_sql("CREATE TABLE orders (order_date DATE, amount DOUBLE)")
    con.raw_sql("CREATE TABLE refunds (refund_date DATE, amount DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (DATE '2026-05-01', 10.0),(DATE '2026-05-24', 30.0)")
    con.raw_sql("INSERT INTO refunds VALUES (DATE '2026-05-24', 5.0)")


def _bootstrap_epoch_seconds(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        "@ms.datasource(name='warehouse')\n"
        "def warehouse(): ...\n"
        "\n"
        "@ms.dataset(name='orders', datasource=warehouse)\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.time_field(\n"
        "    dataset='orders',\n"
        "    data_type='integer',\n"
        "    format='epoch_seconds',\n"
        "    granularity='day',\n"
        ")\n"
        "def event_ts(orders):\n"
        "    return orders.event_ts\n"
        "\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_epoch_seconds(con):
    tz = ZoneInfo("Asia/Shanghai")
    first = int(datetime(2026, 5, 1, 0, 30, tzinfo=tz).timestamp())
    second = int(datetime(2026, 5, 1, 23, 30, tzinfo=tz).timestamp())
    con.raw_sql("CREATE TABLE orders (event_ts BIGINT, amount DOUBLE)")
    con.raw_sql(f"INSERT INTO orders VALUES ({first}, 10.0),({second}, 20.0)")


def test_relative_window_without_grain_stays_scalar(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_sales_orders(con)
    s = session_attach.create(
        name="demo",
        tz="Asia/Shanghai",
        backends={"warehouse": lambda: con},
    )

    frame = observe(
        "sales.revenue",
        window={"expr": "mtd", "as_of": "2026-05-24T13:42:11+08:00"},
        session=s,
    )

    assert frame.meta.semantic_kind == "scalar"
    assert frame.meta.window is not None
    assert frame.meta.window["start"] == "2026-05-01"
    assert frame.meta.window["end"] == "2026-05-24"

    jobs = s.jobs()
    assert len(jobs) == 1
    job = s.job(jobs[0].id)
    window_params = job["params"]["window"]
    assert window_params["original"]["expr"] == "mtd"
    assert window_params["resolved"]["start"] == "2026-05-01"
    assert window_params["resolved"]["end"] == "2026-05-24"
    assert window_params["as_of_resolved"] == "2026-05-24T13:42:11+08:00"
    assert window_params["session_tz"] == "Asia/Shanghai"


def test_relative_window_with_grain_returns_time_series(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_sales_orders(con)
    s = session_attach.create(
        name="demo",
        tz="Asia/Shanghai",
        backends={"warehouse": lambda: con},
    )

    frame = observe(
        "sales.revenue",
        window={"expr": "mtd", "grain": "day", "as_of": "2026-05-24T13:42:11+08:00"},
        session=s,
    )

    assert frame.meta.semantic_kind == "time_series"
    assert frame.meta.axes == {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_field": "order_date",
        }
    }
    assert list(frame.to_pandas().columns) == ["bucket_start", "revenue"]


def test_windowed_time_series_rejects_multi_dataset_metric(tmp_path):
    _bootstrap_multi_dataset(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_multi_dataset(con)
    s = session_attach.create(
        name="demo",
        tz="Asia/Shanghai",
        backends={"warehouse": lambda: con},
    )

    with pytest.raises(MetricShapeUnsupportedError) as exc_info:
        observe(
            "sales.net",
            window={"start": "2026-05-01", "end": "2026-05-24", "grain": "day"},
            session=s,
        )

    assert exc_info.value.details["kind"] == "WindowedTimeSeriesUnsupported"
    assert s.jobs() == []
    assert s.frames() == []


def test_absolute_window_with_grain_persists_resolved_window_contract(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_sales_orders(con)
    s = session_attach.create(
        name="demo",
        tz="Asia/Shanghai",
        backends={"warehouse": lambda: con},
    )

    frame = observe(
        "sales.revenue",
        window={"start": "2026-05-01", "end": "2026-05-24", "grain": "day"},
        session=s,
    )

    job = s.job(s.jobs()[0].id)
    window_params = job["params"]["window"]
    assert window_params["original"] is None
    assert window_params["as_of_resolved"] is None
    assert window_params["session_tz"] == "Asia/Shanghai"
    assert window_params["resolved"] == {
        "kind": "absolute",
        "start": "2026-05-01",
        "end": "2026-05-24",
        "grain": "day",
        "tz": None,
        "time_field": None,
    }
    assert frame.meta.window == window_params["resolved"]


def test_epoch_seconds_time_series_day_bucket_respects_session_tz(tmp_path):
    _bootstrap_epoch_seconds(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_epoch_seconds(con)
    s = session_attach.create(
        name="demo",
        tz="Asia/Shanghai",
        backends={"warehouse": lambda: con},
    )

    frame = observe(
        "sales.revenue",
        window={"start": "2026-05-01", "end": "2026-05-01", "grain": "day"},
        session=s,
    )

    df = frame.to_pandas()
    assert frame.meta.semantic_kind == "time_series"
    assert df["revenue"].tolist() == pytest.approx([30.0])
    assert [item.strftime("%Y-%m-%d") for item in df["bucket_start"]] == ["2026-05-01"]
