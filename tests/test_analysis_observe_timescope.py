"""observe timescope behavior against seeded DuckDB."""

from typing import get_type_hints

import ibis
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import MetricShapeUnsupportedError
from marivo.analysis.intents.observe import observe
from marivo.analysis.refs import MetricRef
from marivo.analysis.windows.spec import TimeGrain, TimeScopeInput


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    session_attach._reset_process_state()
    yield


def _bootstrap_sales(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "@ms.dataset(name='orders', datasource='warehouse')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.order_date.cast('date')\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum(), name='revenue')\n"
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


def test_observe_time_type_hints_match_supported_input_forms():
    hints = get_type_hints(observe)
    assert hints["timescope"] == TimeScopeInput
    assert hints["grain"] == TimeGrain | None


def _bootstrap_multi_dataset(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "@ms.dataset(name='orders', datasource='warehouse')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.order_date.cast('date')\n"
        "\n"
        "@ms.dataset(name='refunds', datasource='warehouse')\n"
        "def refunds(backend):\n"
        "    return backend.table('refunds')\n"
        "\n"
        "@ms.time_field(dataset=refunds, data_type='date', granularity='day')\n"
        "def refund_date(refunds):\n"
        "    return refunds.refund_date.cast('date')\n"
        "\n"
        "@ms.metric(datasets=[orders, refunds], decomposition=ms.sum(), name='net')\n"
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
    datasource_dir = semantic_dir.parent.parent / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    # v1.1 does not support data_type='integer' / format='epoch_seconds' yet;
    # use a date-based time field instead so the session_tz bucketing logic
    # can still be exercised.
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "@ms.dataset(name='orders', datasource='warehouse')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.order_date.cast('date')\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_epoch_seconds(con):
    con.raw_sql("CREATE TABLE orders (order_date DATE, amount DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (DATE '2026-05-01', 10.0),(DATE '2026-05-01', 20.0)")


def test_timescope_without_grain_stays_scalar(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_sales_orders(con)
    s = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
    )

    frame = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-05-01", "end": "2026-05-24"},
        session=s,
    )

    assert frame.meta.semantic_kind == "scalar"
    assert frame.meta.window is not None
    assert frame.meta.window["start"] == "2026-05-01"
    assert frame.meta.window["end"] == "2026-05-24"

    jobs = s.jobs()
    assert len(jobs) == 1
    job = s.job(jobs[0].id)
    window_params = job["params"]["timescope"]
    assert window_params["original"] == {"start": "2026-05-01", "end": "2026-05-24"}
    assert window_params["resolved"]["start"] == "2026-05-01"
    assert window_params["resolved"]["end"] == "2026-05-24"
    assert window_params["session_tz"] == "Asia/Shanghai"


def test_timescope_with_grain_returns_time_series(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_sales_orders(con)
    s = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
    )

    frame = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-05-01", "end": "2026-05-24"},
        grain="day",
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
    s = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
    )

    with pytest.raises(MetricShapeUnsupportedError) as exc_info:
        observe(
            MetricRef("sales.net"),
            timescope={"start": "2026-05-01", "end": "2026-05-24"},
            grain="day",
            session=s,
        )

    assert exc_info.value.details["kind"] == "WindowedTimeSeriesUnsupported"
    assert s.jobs() == []
    assert s.frames() == []


def test_absolute_window_with_grain_persists_resolved_window_contract(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_sales_orders(con)
    s = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
    )

    frame = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-05-01", "end": "2026-05-24"},
        grain="day",
        session=s,
    )

    job = s.job(s.jobs()[0].id)
    window_params = job["params"]["timescope"]
    assert window_params["original"] == {"start": "2026-05-01", "end": "2026-05-24"}
    assert window_params["session_tz"] == "Asia/Shanghai"
    assert window_params["resolved"] == {
        "kind": "absolute",
        "start": "2026-05-01",
        "end": "2026-05-24",
        "grain": "day",
        "time_field": None,
    }
    assert frame.meta.window == window_params["resolved"]


def test_date_time_series_day_bucket_respects_session_tz(tmp_path):
    _bootstrap_epoch_seconds(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_epoch_seconds(con)
    s = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
    )

    frame = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-05-01", "end": "2026-05-01"},
        grain="day",
        session=s,
    )

    df = frame.to_pandas()
    assert frame.meta.semantic_kind == "time_series"
    assert df["revenue"].tolist() == pytest.approx([30.0])
    assert [item.strftime("%Y-%m-%d") for item in df["bucket_start"]] == ["2026-05-01"]
