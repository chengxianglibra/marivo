"""session.observe end-to-end against a seeded DuckDB."""

import ibis
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import (
    MetricNotFoundError,
    NoBackendFactoryError,
    SemanticKindMismatchError,
    SessionStateError,
    WindowInvalidError,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents.observe import observe
from marivo.analysis.refs import MetricRef
from tests.conftest import bootstrap_sales_project
from tests.shared_fixtures import connect_sales_orders, sales_backends


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    yield


def _seed(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 'north', 100),"
        "(2, DATE '2026-07-02', 20.0, 'north', 100),"
        "(3, DATE '2026-08-01', 30.0, 'south', 200),"
        "(4, DATE '2026-09-15', 40.0, 'north', 300)"
    )


def _backends(con):
    return {"warehouse": lambda: con}


def _bootstrap_sales_with_two_time_fields(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def create_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='timestamp', granularity='hour')\n"
        "def create_time(orders):\n"
        "    return orders.created_ts\n"
        "\n"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_two_time_fields(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, created_ts TIMESTAMP, amount DOUBLE)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', TIMESTAMP '2026-07-01 08:00:00', 10.0),"
        "(2, DATE '2026-07-02', TIMESTAMP '2026-07-02 09:00:00', 20.0)"
    )


def _bootstrap_sales_with_string_partition_time_field(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='string', granularity='day', "
        "date_format='yyyymmdd')\n"
        "def log_date(orders):\n"
        "    return orders.log_date\n"
        "\n"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_string_partition_orders(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, log_date VARCHAR, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, '20241010', 5.0),"
        "(2, '20241011', 10.0),"
        "(3, '20250731', 20.0),"
        "(4, '20250801', 30.0)"
    )


def _bootstrap_sales_with_single_hour_partition_time_field(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='string', granularity='hour', "
        "date_format='yyyymmddhh')\n"
        "def log_hour(orders):\n"
        "    return orders.log_hour\n"
        "\n"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _bootstrap_sales_with_composite_hour_partition_time_fields(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='string', granularity='day', "
        "date_format='yyyymmdd')\n"
        "def log_date(orders):\n"
        "    return orders.log_date\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='string', granularity='hour', "
        "date_format='hh', required_prefix='log_date')\n"
        "def log_hour(orders):\n"
        "    return orders.log_hour\n"
        "\n"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_single_hour_partition_orders(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, log_hour VARCHAR, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, '2024101102', 5.0),"
        "(2, '2024101103', 10.0),"
        "(3, '2025073114', 20.0),"
        "(4, '2025073115', 30.0)"
    )


def _seed_composite_hour_partition_orders(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, log_date VARCHAR, log_hour VARCHAR, amount DOUBLE)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, '20241011', '02', 5.0),"
        "(2, '20241011', '03', 10.0),"
        "(3, '20250731', '14', 20.0),"
        "(4, '20250731', '15', 30.0)"
    )


def test_observe_returns_metric_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(MetricRef("sales.revenue"), session=s)
    assert isinstance(mf, MetricFrame)
    assert mf.meta.metric_id == "sales.revenue"
    assert mf.meta.session_id == s.id


def test_observe_rejects_bare_metric_string(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe("sales.revenue", session=s)  # type: ignore[arg-type]

    assert exc_info.value.details["expected_kind"] == "MetricRef"
    assert exc_info.value.details["got_kind"] == "str"
    rendered = str(exc_info.value)
    assert "frame kind" not in rendered
    assert 'mv.MetricRef("sales.revenue")' in rendered


def test_observe_applies_window(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)


def test_observe_string_partition_window_keeps_closed_result_semantics(tmp_path):
    _bootstrap_sales_with_string_partition_time_field(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_string_partition_orders(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2024-10-11", "end": "2025-07-31"},
        session=s,
    )

    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)
    assert mf.meta.window is not None
    assert mf.meta.window["start"] == "2024-10-11"
    assert mf.meta.window["end"] == "2025-07-31"


def test_observe_single_hour_partition_window_keeps_closed_result_semantics(tmp_path):
    _bootstrap_sales_with_single_hour_partition_time_field(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_single_hour_partition_orders(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2024-10-11T03:00:00", "end": "2025-07-31T14:00:00"},
        session=s,
    )

    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)
    assert mf.meta.window is not None
    assert mf.meta.window["start"] == "2024-10-11T03:00:00"
    assert mf.meta.window["end"] == "2025-07-31T14:00:00"


def test_observe_composite_hour_partition_window_keeps_closed_result_semantics(tmp_path):
    _bootstrap_sales_with_composite_hour_partition_time_fields(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_composite_hour_partition_orders(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2024-10-11T03:00:00", "end": "2025-07-31T14:00:00"},
        time_field="log_hour",
        session=s,
    )

    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)
    assert mf.meta.window is not None
    assert mf.meta.window["start"] == "2024-10-11T03:00:00"
    assert mf.meta.window["end"] == "2025-07-31T14:00:00"
    assert mf.meta.window["time_field"] == "log_hour"


def test_observe_multiple_time_fields_mentions_time_field_fix(tmp_path):
    _bootstrap_sales_with_two_time_fields(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_two_time_fields(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(WindowInvalidError) as exc_info:
        observe(
            MetricRef("sales.revenue"),
            timescope={"start": "2026-07-01", "end": "2026-07-31"},
            session=s,
        )

    rendered = str(exc_info.value)
    assert "multiple time_fields" in rendered
    assert "create_date" in rendered
    assert "create_time" in rendered
    assert 'time_field="create_date"' in rendered


def test_observe_multiple_time_fields_accepts_explicit_time_field(tmp_path):
    _bootstrap_sales_with_two_time_fields(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_two_time_fields(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        time_field="create_date",
        session=s,
    )

    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)


def test_observe_applies_slice(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(MetricRef("sales.revenue"), where={"region": "NORTH"}, session=s)
    assert mf.to_pandas().iloc[0, 0] == pytest.approx(70.0)


def test_observe_unknown_metric_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    with pytest.raises(MetricNotFoundError):
        observe(MetricRef("sales.nonexistent"), session=s)


def test_observe_errored_project_raises(tmp_path, monkeypatch):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    # Simulate a project that re-loads and stays errored
    from marivo.semantic.errors import SemanticLoadFailed

    def fail_load(self):
        from marivo.semantic.errors import SemanticError
        from marivo.semantic.loader import LoadResult

        err = SemanticError(kind="test_error", message="test error")
        result = LoadResult(status="errored", errors=(err,))
        # Also update the project state
        self._status = result.status
        self._errors = result.errors
        self._registry = result.registry
        self._sidecar = result.sidecar
        return result

    monkeypatch.setattr(type(s.semantic_project), "load", fail_load)
    s.semantic_project._status = "unloaded"

    with pytest.raises(SemanticLoadFailed):
        observe(MetricRef("sales.revenue"), session=s)


def test_observe_read_only_session_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(name="demo", use_datasources=False)
    with pytest.raises(NoBackendFactoryError):
        observe(MetricRef("sales.revenue"), session=s)


def test_observe_persists_job_and_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(MetricRef("sales.revenue"), session=s)
    summaries = s.jobs()
    assert len(summaries) == 1
    assert summaries[0].intent == "observe"
    assert summaries[0].output_frame_ref == mf.ref
    assert (s.layout.frames_dir / mf.ref / "data.parquet").is_file()


def test_observe_archived_session_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    session_attach.archive("demo")
    with pytest.raises(SessionStateError):
        observe(MetricRef("sales.revenue"), session=s)


def test_observe_stale_archived_session_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    session_attach._reset_process_state()
    session_attach.archive("demo")
    assert s.state == "active"
    with pytest.raises(SessionStateError):
        observe(MetricRef("sales.revenue"), session=s)


def test_observe_persists_known_datasources(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    observe(MetricRef("sales.revenue"), session=s)
    session_attach._reset_process_state()
    reattached = session_attach.get_or_create(name="demo")
    assert reattached.known_datasources == {"warehouse"}


# ---------------------------------------------------------------------------
# Component-aware derived metric tests
# ---------------------------------------------------------------------------


def _bootstrap_failure_rate(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
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
        "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum())\n"
        "def failed_count(orders):\n"
        "    return (orders.state == 'FAILED').cast('int64').sum()\n"
        "\n"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum())\n"
        "def total_count(orders):\n"
        "    return orders.count()\n"
        "\n"
        "ms.derived_metric(\n"
        "    name='failure_rate',\n"
        "    decomposition=ms.ratio(\n"
        "        numerator='sales.failed_count',\n"
        "        denominator='sales.total_count',\n"
        "    ),\n"
        ")\n"
        "\n"
        "ms.derived_metric(\n"
        "    name='failed_count_ratio',\n"
        "    decomposition=ms.ratio(\n"
        "        numerator='sales.failed_count',\n"
        "        denominator='sales.failed_count',\n"
        "    ),\n"
        ")\n"
    )


def _seed_failure_rate(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at DATE, state VARCHAR)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 'FAILED'),"
        "(2, DATE '2026-07-02', 'SUCCEEDED'),"
        "(3, DATE '2026-07-03', 'FAILED'),"
        "(4, DATE '2026-07-04', 'SUCCEEDED')"
    )


def test_observe_scalar_derived_ratio_links_clean_component_frame(tmp_path):
    _bootstrap_failure_rate(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_failure_rate(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = observe(MetricRef("sales.failure_rate"), session=session)

    assert frame.meta.component_ref is not None
    assert frame.meta.decomposition == {
        "kind": "ratio",
        "components": {
            "numerator": "sales.failed_count",
            "denominator": "sales.total_count",
        },
    }
    assert set(frame.to_pandas().columns) == {"failure_rate"}
    assert "numerator" not in frame.summary().columns
    components = frame.components()
    assert components.meta.parent_ref == frame.ref
    assert components.meta.parent_kind == "metric_frame"
    assert components.meta.decomposition_kind == "ratio"
    assert components.meta.components == {
        "numerator": "sales.failed_count",
        "denominator": "sales.total_count",
    }
    component_df = components.to_pandas()
    assert list(component_df.columns) == ["numerator", "denominator", "metric_value"]
    assert component_df.iloc[0]["numerator"] == pytest.approx(2.0)
    assert component_df.iloc[0]["denominator"] == pytest.approx(4.0)
    assert component_df.iloc[0]["metric_value"] == pytest.approx(0.5)

    self_ratio = observe(MetricRef("sales.failed_count_ratio"), session=session)
    assert self_ratio.to_pandas().iloc[0]["failed_count_ratio"] == pytest.approx(1.0)
    self_components = self_ratio.components().to_pandas()
    assert list(self_components.columns) == ["numerator", "denominator", "metric_value"]
    assert self_components.iloc[0]["numerator"] == pytest.approx(2.0)
    assert self_components.iloc[0]["denominator"] == pytest.approx(2.0)
    assert self_components.iloc[0]["metric_value"] == pytest.approx(1.0)


def test_observe_time_series_derived_ratio_links_component_frame(tmp_path):
    _bootstrap_failure_rate(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_failure_rate(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = observe(
        MetricRef("sales.failure_rate"),
        timescope={"start": "2026-07-01", "end": "2026-07-04"},
        grain="day",
        session=session,
    )

    assert frame.meta.semantic_kind == "time_series"
    assert frame.meta.component_ref is not None
    assert frame.meta.axes["time"]["column"] == "bucket_start"
    assert set(frame.to_pandas().columns) == {"bucket_start", "failure_rate"}

    components = frame.components()
    assert components.meta.parent_ref == frame.ref
    assert components.meta.semantic_kind == "time_series"
    assert components.meta.axes == frame.meta.axes
    component_df = components.to_pandas()
    assert list(component_df.columns) == [
        "bucket_start",
        "numerator",
        "denominator",
        "metric_value",
    ]
    by_bucket = {str(row.bucket_start): row for row in component_df.itertuples()}
    assert by_bucket["2026-07-01"].numerator == pytest.approx(1.0)
    assert by_bucket["2026-07-01"].denominator == pytest.approx(1.0)
    assert by_bucket["2026-07-01"].metric_value == pytest.approx(1.0)
    assert by_bucket["2026-07-02"].numerator == pytest.approx(0.0)
    assert by_bucket["2026-07-02"].denominator == pytest.approx(1.0)
    assert by_bucket["2026-07-02"].metric_value == pytest.approx(0.0)


def _bootstrap_sales_with_strptime_slash_time_field(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='string', granularity='day', "
        "date_format='%Y/%m/%d')\n"
        "def log_date(orders):\n"
        "    return orders.log_date\n"
        "\n"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_strptime_slash_orders(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, log_date VARCHAR, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, '2024/10/10', 5.0),"
        "(2, '2024/10/11', 10.0),"
        "(3, '2025/07/31', 20.0),"
        "(4, '2025/08/01', 30.0)"
    )


def test_observe_strptime_day_format_filters_correctly(tmp_path):
    _bootstrap_sales_with_strptime_slash_time_field(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_strptime_slash_orders(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    frame = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2024-10-11", "end": "2025-07-31"},
        session=s,
    )
    df = frame.to_pandas()
    assert len(df) == 1
    assert df.iloc[0, 0] == pytest.approx(30.0)


def test_observe_strptime_day_format_time_series(tmp_path):
    _bootstrap_sales_with_strptime_slash_time_field(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_strptime_slash_orders(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    frame = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2024-10-10", "end": "2025-08-01"},
        grain="day",
        session=s,
    )
    assert frame.meta.semantic_kind == "time_series"
    df = frame.to_pandas()
    assert "bucket_start" in df.columns
    assert len(df) == 4


def _bootstrap_sales_with_strptime_integer_time_field(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='integer', granularity='day', "
        "date_format='%Y%m%d')\n"
        "def log_date(orders):\n"
        "    return orders.log_date\n"
        "\n"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_strptime_integer_orders(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, log_date INTEGER, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, 20241010, 5.0),"
        "(2, 20241011, 10.0),"
        "(3, 20250731, 20.0),"
        "(4, 20250801, 30.0)"
    )


def test_observe_strptime_integer_format_filters_correctly(tmp_path):
    _bootstrap_sales_with_strptime_integer_time_field(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_strptime_integer_orders(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    frame = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2024-10-11", "end": "2025-07-31"},
        session=s,
    )
    df = frame.to_pandas()
    assert len(df) == 1
    assert df.iloc[0, 0] == pytest.approx(30.0)


def test_observe_expect_shape_accepts_matching_scalar(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    mf = observe(MetricRef("sales.revenue"), expect_shape="scalar", session=s)

    assert mf.meta.semantic_kind == "scalar"


def test_observe_expect_shape_rejects_mismatch(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    # No grain and no dimensions -> predicted shape is "scalar", not "time_series".
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        observe(MetricRef("sales.revenue"), expect_shape="time_series", session=s)

    rendered = str(excinfo.value)
    assert "time_series" in rendered
    assert "scalar" in rendered
