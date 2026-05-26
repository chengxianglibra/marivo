"""mv.observe panel shape (dimensions + window grain)."""

import ibis
import pytest

import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.intents.observe import observe
from marivo.analysis_py.refs import DimensionRef, MetricRef


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _seed(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, channel VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 'north', 'web', 100),"
        "(2, DATE '2026-07-02', 20.0, 'north', 'app', 100),"
        "(3, DATE '2026-07-01', 30.0, 'south', 'web', 200),"
        "(4, DATE '2026-07-02', 40.0, 'south', 'app', 300)"
    )


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
        "warehouse = ms.datasource(name='warehouse', backend_type='duckdb')\n"
        "\n"
        "@ms.dataset(name='orders', datasource=warehouse)\n"
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
        "@ms.field(dataset=orders)\n"
        "def channel(orders):\n"
        "    return orders.channel\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _backends(con):
    return {"warehouse": lambda: con}


def test_observe_panel_returns_time_and_dimension_axes(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-31", "grain": "day"},
        dimensions=[DimensionRef("region")],
        session=s,
    )

    assert mf.meta.semantic_kind == "panel"
    assert mf.meta.axes["time"]["grain"] == "day"
    assert mf.meta.axes["region"]["role"] == "dimension"
    df = mf.to_pandas()
    assert {"bucket_start", "region", "revenue"} == set(df.columns)
    assert len(df) == 4
    by_key = {(str(row.bucket_start), row.region): row.revenue for row in df.itertuples()}
    assert by_key[("2026-07-01", "NORTH")] == pytest.approx(10.0)
    assert by_key[("2026-07-02", "SOUTH")] == pytest.approx(40.0)


def test_observe_panel_multi_dimension(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-31", "grain": "day"},
        dimensions=[DimensionRef("region"), DimensionRef("channel")],
        session=s,
    )

    assert mf.meta.semantic_kind == "panel"
    df = mf.to_pandas()
    assert {"bucket_start", "region", "channel", "revenue"} == set(df.columns)
