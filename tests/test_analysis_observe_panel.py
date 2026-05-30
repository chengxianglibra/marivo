"""session.observe panel shape (dimensions + window grain)."""

import ibis
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis.intents.observe import observe
from marivo.analysis.refs import DimensionRef, MetricRef


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
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

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
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.revenue"),
        window={"start": "2026-07-01", "end": "2026-07-31", "grain": "day"},
        dimensions=[DimensionRef("region"), DimensionRef("channel")],
        session=s,
    )

    assert mf.meta.semantic_kind == "panel"
    df = mf.to_pandas()
    assert {"bucket_start", "region", "channel", "revenue"} == set(df.columns)


# ---------------------------------------------------------------------------
# Derived metric panel fixtures and tests
# ---------------------------------------------------------------------------


def _bootstrap_failure_metrics(tmp_path):
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
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.field(dataset=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum())\n"
        "def failed_count(orders):\n"
        "    return (orders.state == 'FAILED').cast('int64').sum()\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum())\n"
        "def total_count(orders):\n"
        "    return orders.count()\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum())\n"
        "def weighted_failed(orders):\n"
        "    return ((orders.state == 'FAILED').cast('int64') * orders.weight).sum()\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum())\n"
        "def total_weight(orders):\n"
        "    return orders.weight.sum()\n"
        "\n"
        "@ms.metric(\n"
        "    datasets=[],\n"
        "    decomposition=ms.ratio(\n"
        "        numerator='sales.failed_count',\n"
        "        denominator='sales.total_count',\n"
        "    ),\n"
        ")\n"
        "def failure_rate():\n"
        "    return ms.component('numerator') / ms.component('denominator')\n"
        "\n"
        "@ms.metric(\n"
        "    datasets=[],\n"
        "    decomposition=ms.weighted_average(\n"
        "        value='sales.weighted_failed',\n"
        "        weight='sales.total_weight',\n"
        "    ),\n"
        ")\n"
        "def weighted_failure_rate():\n"
        "    return ms.component('numerator') / ms.component('weight')\n"
    )


def _seed_failure_metrics(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "state VARCHAR, region VARCHAR, weight DOUBLE)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 'FAILED', 'north', 2.0),"
        "(2, DATE '2026-07-01', 'SUCCEEDED', 'north', 1.0),"
        "(3, DATE '2026-07-01', 'FAILED', 'south', 3.0),"
        "(4, DATE '2026-07-02', 'SUCCEEDED', 'north', 1.0),"
        "(5, DATE '2026-07-02', 'FAILED', 'south', 2.0)"
    )


def test_observe_panel_derived_ratio_links_component_frame(tmp_path):
    _bootstrap_failure_metrics(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_failure_metrics(con)
    session = session_attach.get_or_create(name="demo", backends=_backends(con))

    frame = observe(
        MetricRef("sales.failure_rate"),
        window={"start": "2026-07-01", "end": "2026-07-02", "grain": "day"},
        dimensions=[DimensionRef("region")],
        session=session,
    )

    assert frame.meta.semantic_kind == "panel"
    assert frame.meta.component_ref is not None
    assert set(frame.to_pandas().columns) == {"bucket_start", "region", "failure_rate"}
    components = frame.components()
    assert components.meta.semantic_kind == "panel"
    assert components.meta.axes == frame.meta.axes
    component_df = components.to_pandas()
    assert list(component_df.columns) == [
        "bucket_start",
        "region",
        "numerator",
        "denominator",
        "metric_value",
    ]
    by_key = {(str(row.bucket_start), row.region): row for row in component_df.itertuples()}
    assert by_key[("2026-07-01", "NORTH")].numerator == pytest.approx(1.0)
    assert by_key[("2026-07-01", "NORTH")].denominator == pytest.approx(2.0)
    assert by_key[("2026-07-01", "NORTH")].metric_value == pytest.approx(0.5)
    assert by_key[("2026-07-02", "SOUTH")].numerator == pytest.approx(1.0)
    assert by_key[("2026-07-02", "SOUTH")].denominator == pytest.approx(1.0)


def test_observe_panel_derived_weighted_average_uses_weight_component(tmp_path):
    _bootstrap_failure_metrics(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_failure_metrics(con)
    session = session_attach.get_or_create(name="demo", backends=_backends(con))

    frame = observe(
        MetricRef("sales.weighted_failure_rate"),
        window={"start": "2026-07-01", "end": "2026-07-02", "grain": "day"},
        dimensions=[DimensionRef("region")],
        session=session,
    )

    components = frame.components()
    assert components.meta.decomposition_kind == "weighted_average"
    assert set(frame.to_pandas().columns) == {
        "bucket_start",
        "region",
        "weighted_failure_rate",
    }
    component_df = components.to_pandas()
    assert "weight" in component_df.columns
    assert "denominator" not in component_df.columns
    by_key = {(str(row.bucket_start), row.region): row for row in component_df.itertuples()}
    assert by_key[("2026-07-01", "NORTH")].numerator == pytest.approx(2.0)
    assert by_key[("2026-07-01", "NORTH")].weight == pytest.approx(3.0)
    assert by_key[("2026-07-01", "NORTH")].metric_value == pytest.approx(2.0 / 3.0)
