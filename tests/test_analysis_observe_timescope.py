"""observe timescope behavior against seeded DuckDB."""

from typing import get_type_hints

import ibis
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.intents.observe import observe
from marivo.analysis.windows.spec import GrainInput, TimeScopeInput
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    session_attach._reset_process_state()
    yield


def _bootstrap_sales(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.order_date.cast('date')\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
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
    assert hints["grain"] == GrainInput


def _bootstrap_multi_dataset(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.order_date.cast('date')\n"
        "\n"
        "refunds = ms.entity(name='refunds', datasource=md.ref('datasource.warehouse'), source=ms.table('refunds'))\n"
        "\n"
        "@ms.time_dimension(entity=refunds, granularity='day')\n"
        "def refund_date(refunds):\n"
        "    return refunds.refund_date.cast('date')\n"
        "\n"
        "@ms.metric(entities=[orders, refunds], root_entity=orders, additivity='additive', name='net', )\n"
        "def net(orders, refunds):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_multi_dataset(con):
    con.raw_sql("CREATE TABLE orders (order_date DATE, amount DOUBLE)")
    con.raw_sql("CREATE TABLE refunds (refund_date DATE, amount DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (DATE '2026-05-01', 10.0),(DATE '2026-05-24', 30.0)")
    con.raw_sql("INSERT INTO refunds VALUES (DATE '2026-05-24', 5.0)")


def _bootstrap_date_field(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.order_date.cast('date')\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_date_field(con):
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        timescope={"start": "2026-05-01", "end": "2026-05-25"},
        session=s,
    )

    assert frame.meta.semantic_kind == "scalar"
    assert frame.meta.window is not None
    assert frame.meta.window["start"] == "2026-05-01"
    assert frame.meta.window["end"] == "2026-05-25"

    jobs = s.jobs()
    assert len(jobs) == 1
    job = s.job(jobs[0].id)
    window_params = job["params"]["timescope"]
    assert window_params["original"] == {"start": "2026-05-01", "end": "2026-05-25"}
    assert window_params["resolved"]["start"] == "2026-05-01"
    assert window_params["resolved"]["end"] == "2026-05-25"
    assert window_params["report_tz"] == "Asia/Shanghai"


def test_timescope_with_grain_returns_time_series(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_sales_orders(con)
    s = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
    )

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        timescope={"start": "2026-05-01", "end": "2026-05-25"},
        grain="day",
        session=s,
    )

    assert frame.meta.semantic_kind == "time_series"
    assert frame.meta.axes == {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "order_date",
        }
    }
    assert list(frame.to_pandas().columns) == ["bucket_start", "value"]


def test_windowed_time_series_rejects_multi_dataset_metric(tmp_path):
    _bootstrap_multi_dataset(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_multi_dataset(con)
    s = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
    )

    from marivo.analysis.intents.observe_errors import ObservePlanningError

    with pytest.raises(ObservePlanningError) as exc_info:
        observe(
            make_ref("sales.net", SemanticKind.METRIC),
            timescope={"start": "2026-05-01", "end": "2026-05-24"},
            grain="day",
            session=s,
        )

    assert exc_info.value.details["code"] == "path-missing"
    assert s.jobs() == []
    assert s.frame_summaries() == []


def test_absolute_window_with_grain_persists_resolved_window_contract(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_sales_orders(con)
    s = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
    )

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        timescope={"start": "2026-05-01", "end": "2026-05-25"},
        grain="day",
        session=s,
    )

    job = s.job(s.jobs()[0].id)
    window_params = job["params"]["timescope"]
    assert window_params["original"] == {"start": "2026-05-01", "end": "2026-05-25"}
    assert window_params["report_tz"] == "Asia/Shanghai"
    assert window_params["resolved"] == {
        "kind": "absolute",
        "start": "2026-05-01",
        "end": "2026-05-25",
        "grain": "day",
        "time_dimension": None,
    }
    assert frame.meta.window == window_params["resolved"]


def test_date_time_series_day_bucket_respects_report_tz(tmp_path):
    _bootstrap_date_field(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_date_field(con)
    s = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: con},
    )

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        timescope={"start": "2026-05-01", "end": "2026-05-02"},
        grain="day",
        session=s,
    )

    df = frame.to_pandas()
    assert frame.meta.semantic_kind == "time_series"
    assert df["value"].tolist() == pytest.approx([30.0])
    assert [item.strftime("%Y-%m-%d") for item in df["bucket_start"]] == ["2026-05-01"]
