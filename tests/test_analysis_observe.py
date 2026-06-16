"""session.observe end-to-end against a seeded DuckDB."""

import inspect
from types import SimpleNamespace

import ibis
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    MetricNotFoundError,
    NoBackendFactoryError,
    SemanticKindMismatchError,
    WindowInvalidError,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents.observe import observe
from marivo.analysis.intents.observe_errors import ObservePlanningError
from marivo.semantic.catalog import SemanticKind, SemanticRef
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


def _bootstrap_sales_with_country_dimension(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(warehouse)\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = md.ref('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=ms.table('orders'))\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def country(orders):\n"
        "    return orders.country\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', "
        "name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_country_orders(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, amount DOUBLE, country VARCHAR)")
    con.raw_sql("INSERT INTO orders VALUES (1, 10.0, 'US'),(2, 20.0, 'US'),(3, 30.0, 'CA')")


@pytest.fixture
def sales_session(tmp_path):
    _bootstrap_sales_with_country_dimension(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_country_orders(con)
    return session_attach.get_or_create(name="demo", backends=_backends(con))


@pytest.fixture
def sales_catalog(sales_session):
    return sales_session.catalog


def _backends(con):
    return {"warehouse": lambda: con}


def _bootstrap_sales_with_two_time_fields(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.date())\n"
        "def create_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='hour', parse=ms.timestamp(timezone='UTC'))\n"
        "def create_time(orders):\n"
        "    return orders.created_ts\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _bootstrap_sales_with_default_time_field(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.date(), is_default=True)\n"
        "def create_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='hour', parse=ms.timestamp(timezone='UTC'))\n"
        "def create_time(orders):\n"
        "    return orders.created_ts\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
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
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d', data_type='string'))\n"
        "def log_date(orders):\n"
        "    return orders.log_date\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
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
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='hour', parse=ms.strptime('%Y%m%d%H', data_type='string'))\n"
        "def log_hour(orders):\n"
        "    return orders.log_hour\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _bootstrap_sales_with_composite_hour_partition_time_fields(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d', data_type='string'))\n"
        "def log_date(orders):\n"
        "    return orders.log_date\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='hour', parse=ms.hour_prefix('log_date', data_type='string'))\n"
        "def log_hour(orders):\n"
        "    return orders.log_hour\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
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


def test_observe_planner_does_not_require_catalog_private_state(
    tmp_path, monkeypatch, semantic_project_factory
):
    import marivo.analysis as mv
    import marivo.semantic as ms
    from marivo.analysis.intents.observe_planner import plan_base_observe, plan_observe
    from marivo.semantic.catalog import SemanticCatalog

    semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/model.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.dimension(entity=orders)\n"
                "def country(table):\n"
                "    return table.country\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def revenue(table):\n"
                "    return table.amount.sum()\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def failed_count(table):\n"
                "    return (table.state == 'FAILED').cast('int64').sum()\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def total_count(table):\n"
                "    return table.count()\n"
                "ms.ratio(name='failure_rate', numerator='sales.failed_count', denominator='sales.total_count')\n"
            ),
        }
    )
    monkeypatch.chdir(tmp_path)
    con = ibis.duckdb.connect()
    con.create_table("orders", {"country": ["US"], "amount": [10]}, overwrite=True)
    session = mv.session.get_or_create(
        name="catalog_observe",
        backends={"warehouse": lambda: con},
        use_datasources=False,
    )
    catalog = ms.load(workspace_dir=tmp_path)
    metric = catalog.get("sales.revenue")

    class GuardedCatalog(SemanticCatalog):
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattribute__(self, name):
            if name == "_project":
                raise AssertionError("observe planner must not access catalog._project")
            return object.__getattribute__(self, name)

        def get(self, *args, **kwargs):
            return self._wrapped.get(*args, **kwargs)

        def list(self, *args, **kwargs):
            return self._wrapped.list(*args, **kwargs)

        def _resolver(self, *args, **kwargs):
            return self._wrapped._resolver(*args, **kwargs)

    guarded_catalog = GuardedCatalog(catalog)

    def metric_adapter(ref):
        details = guarded_catalog.get(ref).details()
        return SimpleNamespace(
            semantic_id=details.ref.ref,
            name=details.name,
            root_entity=details.root_entity.ref if details.root_entity is not None else None,
            entities=tuple(entity.ref for entity in details.entities),
            additivity=details.additivity,
            fanout_policy=details.fanout_policy,
            metric_type=details.metric_type,
            composition=SimpleNamespace(
                kind=details.composition,
                components={role: component.ref for role, component in details.components},
            ),
            time_fold=None,
            status_time_dimension=details.status_time_dimension,
            unit=details.unit,
        )

    dataset_irs = {"sales.orders": SimpleNamespace(datasource_name="warehouse")}
    dataset_fns = {"sales.orders": lambda backend: backend.table("orders")}

    assert metric.ref.ref == "sales.revenue"
    assert hasattr(session, "catalog")
    planner_parameters = inspect.signature(plan_base_observe).parameters
    assert "catalog" in planner_parameters
    assert "project" not in planner_parameters
    base_plan = plan_base_observe(
        catalog=guarded_catalog,
        session=session,
        metric_ir=metric_adapter("sales.revenue"),
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=[guarded_catalog.get("sales.orders.country").ref],
        where=None,
        resolved_window=None,
        time_dimension=None,
    )
    assert base_plan.root_entity == "sales.orders"
    assert [dimension.field.semantic_id for dimension in base_plan.dimensions] == [
        "sales.orders.country"
    ]
    derived_plan = plan_observe(
        catalog=guarded_catalog,
        session=session,
        metric_ir=metric_adapter("sales.failure_rate"),
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=None,
        where=None,
        resolved_window=None,
        time_dimension=None,
    )
    assert len(derived_plan.component_plans) == 2


def test_observe_returns_metric_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC), session=s)
    assert isinstance(mf, MetricFrame)
    assert mf.meta.metric_id == "sales.revenue"
    assert mf.meta.session_id == s.id


def test_observe_rejects_bare_metric_string(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe("sales.revenue", session=s)  # type: ignore[arg-type]

    assert exc_info.value.details["expected_kind"] == "metric"
    assert exc_info.value.details["actual_kind"] == "str"
    rendered = str(exc_info.value)
    assert "catalog metric SemanticRef or SemanticObject" in rendered


def test_session_observe_accepts_catalog_object_and_ref(sales_session, sales_catalog):
    metric = sales_catalog.get("sales.revenue")
    country = sales_catalog.get("sales.orders.country").ref

    frame = sales_session.observe(metric, dimensions=[country])

    assert frame.meta.metric_id == "sales.revenue"
    assert "country" in frame.meta.axes


def test_session_observe_rejects_bare_metric_string(sales_session):
    from marivo.analysis.errors import SemanticKindMismatchError

    with pytest.raises(SemanticKindMismatchError):
        sales_session.observe("sales.revenue")


def test_observe_applies_window(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
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
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        timescope={"start": "2024-10-11", "end": "2025-08-01"},
        session=s,
    )

    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)
    assert mf.meta.window is not None
    assert mf.meta.window["start"] == "2024-10-11"
    assert mf.meta.window["end"] == "2025-08-01"


def test_observe_single_hour_partition_window_keeps_closed_result_semantics(tmp_path):
    _bootstrap_sales_with_single_hour_partition_time_field(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_single_hour_partition_orders(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
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
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        timescope={"start": "2024-10-11T03:00:00", "end": "2025-07-31T14:00:00"},
        time_dimension=SemanticRef("log_hour", kind=SemanticKind.DIMENSION),
        session=s,
    )

    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)
    assert mf.meta.window is not None
    assert mf.meta.window["start"] == "2024-10-11T03:00:00"
    assert mf.meta.window["end"] == "2025-07-31T14:00:00"
    assert mf.meta.window["time_dimension"] == "sales.orders.log_hour"


def test_observe_multiple_time_fields_mentions_time_field_fix(tmp_path):
    _bootstrap_sales_with_two_time_fields(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_two_time_fields(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(WindowInvalidError) as exc_info:
        observe(
            SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
            timescope={"start": "2026-07-01", "end": "2026-07-31"},
            session=s,
        )

    rendered = str(exc_info.value)
    assert "multiple time_dimensions" in rendered
    assert "create_date" in rendered
    assert "create_time" in rendered
    assert 'time_dimension=session.catalog.get("<domain.entity.time_dimension>").ref' in rendered
    assert "is_default=True" in rendered


def test_observe_multiple_time_fields_accepts_explicit_time_field(tmp_path):
    _bootstrap_sales_with_two_time_fields(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_two_time_fields(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        time_dimension=SemanticRef("create_date", kind=SemanticKind.DIMENSION),
        session=s,
    )

    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)


def test_observe_uses_default_time_field_when_not_specified(tmp_path):
    _bootstrap_sales_with_default_time_field(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_two_time_fields(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )

    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)


def test_observe_multiple_time_fields_no_default_error_mentions_is_default(tmp_path):
    _bootstrap_sales_with_two_time_fields(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_two_time_fields(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(WindowInvalidError) as exc_info:
        observe(
            SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
            timescope={"start": "2026-07-01", "end": "2026-07-31"},
            session=s,
        )

    rendered = str(exc_info.value)
    assert "is_default=True" in rendered


def test_observe_applies_slice(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        where={SemanticRef("region", kind=SemanticKind.DIMENSION): "NORTH"},
        session=s,
    )
    assert mf.to_pandas().iloc[0, 0] == pytest.approx(70.0)


def test_observe_cache_hit_clears_query_capture(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC), session=s)
    observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC), session=s)

    assert s._connection_runtime._capture_buffer is None


def test_observe_legacy_dimension_ref_where_must_be_declared(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    with pytest.raises((ObservePlanningError, SemanticKindMismatchError)):
        observe(
            SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
            where={SemanticRef("amount", kind=SemanticKind.DIMENSION): {"op": ">=", "value": 30}},
            session=s,
        )


def _bootstrap_sales_with_out_of_scope_amount_dimension(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(warehouse)\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "products = ms.entity(name='products', datasource='warehouse', source=ms.table('products'))\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region\n"
        "\n"
        "@ms.dimension(entity=products)\n"
        "def amount(products):\n"
        "    return products.amount\n"
        "\n"
        "@ms.metric(\n"
        "    entities=[orders],\n"
        "    additivity='additive',\n"
        "    name='revenue',\n"
        ")\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def test_observe_legacy_dimension_ref_where_does_not_borrow_out_of_scope_dimension(tmp_path):
    _bootstrap_sales_with_out_of_scope_amount_dimension(tmp_path)
    con = connect_sales_orders()
    con.raw_sql("CREATE TABLE products (product_id INTEGER, amount DOUBLE)")
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    with pytest.raises((ObservePlanningError, SemanticKindMismatchError)):
        observe(
            SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
            where={SemanticRef("amount", kind=SemanticKind.DIMENSION): {"op": ">=", "value": 30}},
            session=s,
        )


def test_observe_rejects_bare_string_time_field(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
            time_dimension="created_at",
            session=s,
        )
    assert exc_info.value.details["expected_kind"] == "dimension"


def test_observe_rejects_bare_string_where_key(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
            where={"region": "NORTH"},
            session=s,
        )
    assert exc_info.value.details["expected_kind"] == "dimension"


def test_observe_unknown_metric_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    with pytest.raises(MetricNotFoundError):
        observe(SemanticRef("sales.nonexistent", kind=SemanticKind.METRIC), session=s)


def test_observe_errored_project_raises(tmp_path, monkeypatch):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    # Simulate a project that re-loads and stays errored
    from marivo.semantic.errors import SemanticRuntimeError

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

    monkeypatch.setattr(type(s.catalog._project), "load", fail_load)
    s.catalog._project._status = "unloaded"

    with pytest.raises(SemanticRuntimeError) as exc_info:
        observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC), session=s)
    assert exc_info.value.kind == "project_not_loaded"


def test_observe_read_only_session_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(name="demo", use_datasources=False)
    with pytest.raises(NoBackendFactoryError):
        observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC), session=s)


def test_observe_persists_job_and_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC), session=s)
    summaries = s.jobs()
    assert len(summaries) == 1
    assert summaries[0].intent == "observe"
    assert summaries[0].output_frame_ref == mf.ref
    assert (s._layout.frames_dir / mf.ref / "data.parquet").is_file()


def test_observe_read_only_session_without_backend_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    # Session without backend factory is read-only and cannot execute.
    s = session_attach.get_or_create(name="demo", use_datasources=False)
    with pytest.raises(NoBackendFactoryError):
        observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC), session=s)


def test_observe_stale_session_without_backend_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    # Create a session with a backend, then re-open without backend.
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    session_attach._reset_process_state()
    # Re-open without backends -> session becomes read-only.
    s_ro = session_attach.get_or_create(name="demo", use_datasources=False)
    with pytest.raises(NoBackendFactoryError):
        observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC), session=s_ro)


def test_observe_frame_survives_reattach(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(SemanticRef("sales.revenue", kind=SemanticKind.METRIC), session=s)
    session_attach._reset_process_state()
    reattached = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    loaded = reattached.get_frame(mf.ref)
    assert loaded.ref == mf.ref


# ---------------------------------------------------------------------------
# Component-aware derived metric tests
# ---------------------------------------------------------------------------


def _bootstrap_failure_rate(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.date())\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', )\n"
        "def failed_count(orders):\n"
        "    return (orders.state == 'FAILED').cast('int64').sum()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', )\n"
        "def total_count(orders):\n"
        "    return orders.count()\n"
        "\n"
        "ms.ratio(\n"
        "    name='failure_rate',\n"
        "    numerator='sales.failed_count',\n"
        "    denominator='sales.total_count',\n"
        ")\n"
        "\n"
        "ms.ratio(\n"
        "    name='failed_count_ratio',\n"
        "    numerator='sales.failed_count',\n"
        "    denominator='sales.failed_count',\n"
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

    frame = observe(SemanticRef("sales.failure_rate", kind=SemanticKind.METRIC), session=session)

    assert frame.meta.component_ref is not None
    assert frame.meta.composition == {
        "kind": "ratio",
        "components": {
            "numerator": "sales.failed_count",
            "denominator": "sales.total_count",
        },
    }
    assert set(frame.to_pandas().columns) == {"failure_rate"}
    assert "failed_count" not in frame.summary().columns
    components = frame.components()
    assert components.meta.parent_ref == frame.ref
    assert components.meta.parent_kind == "metric_frame"
    assert components.meta.composition_kind == "ratio"
    assert components.meta.components == {
        "numerator": "sales.failed_count",
        "denominator": "sales.total_count",
    }
    component_df = components.to_pandas()
    assert list(component_df.columns) == ["failed_count", "total_count", "failure_rate"]
    assert component_df.iloc[0]["failed_count"] == pytest.approx(2.0)
    assert component_df.iloc[0]["total_count"] == pytest.approx(4.0)
    assert component_df.iloc[0]["failure_rate"] == pytest.approx(0.5)

    self_ratio = observe(
        SemanticRef("sales.failed_count_ratio", kind=SemanticKind.METRIC), session=session
    )
    assert self_ratio.to_pandas().iloc[0]["failed_count_ratio"] == pytest.approx(1.0)
    self_components = self_ratio.components().to_pandas()
    assert list(self_components.columns) == ["numerator", "denominator", "failed_count_ratio"]
    assert self_components.iloc[0]["numerator"] == pytest.approx(2.0)
    assert self_components.iloc[0]["denominator"] == pytest.approx(2.0)
    assert self_components.iloc[0]["failed_count_ratio"] == pytest.approx(1.0)


def test_observe_time_series_derived_ratio_links_component_frame(tmp_path):
    _bootstrap_failure_rate(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_failure_rate(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = observe(
        SemanticRef("sales.failure_rate", kind=SemanticKind.METRIC),
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
        "failed_count",
        "total_count",
        "failure_rate",
    ]
    by_bucket = {str(row.bucket_start): row for row in component_df.itertuples()}
    assert by_bucket["2026-07-01"].failed_count == pytest.approx(1.0)
    assert by_bucket["2026-07-01"].total_count == pytest.approx(1.0)
    assert by_bucket["2026-07-01"].failure_rate == pytest.approx(1.0)
    assert by_bucket["2026-07-02"].failed_count == pytest.approx(0.0)
    assert by_bucket["2026-07-02"].total_count == pytest.approx(1.0)
    assert by_bucket["2026-07-02"].failure_rate == pytest.approx(0.0)


def _bootstrap_sales_with_strptime_slash_time_field(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y/%m/%d', data_type='string'))\n"
        "def log_date(orders):\n"
        "    return orders.log_date\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
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
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        timescope={"start": "2024-10-11", "end": "2025-08-01"},
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
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        timescope={"start": "2024-10-10", "end": "2025-08-02"},
        grain="day",
        session=s,
    )
    assert frame.meta.semantic_kind == "time_series"
    df = frame.to_pandas()
    assert "bucket_start" in df.columns
    assert len(df) == 4


def _bootstrap_sales_with_string_timestamp_timezone(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='minute', parse=ms.strptime('%Y-%m-%d %H:%M:%S', data_type='string', timezone='UTC'))\n"
        "def create_time(orders):\n"
        "    return orders.create_time\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _seed_string_timestamp_timezone_orders(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, create_time VARCHAR, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, '2026-04-30 16:15:00', 10.0),"
        "(2, '2026-04-30 16:35:00', 20.0),"
        "(3, '2026-05-01 16:00:00', 30.0)"
    )


def test_observe_string_timestamp_timezone_subday_time_series(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    session_attach._reset_process_state()
    _bootstrap_sales_with_string_timestamp_timezone(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_string_timestamp_timezone_orders(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))
    frame = observe(
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        timescope={"start": "2026-05-01", "end": "2026-05-02"},
        grain=(30, "minute"),
        time_dimension=SemanticRef("create_time", kind=SemanticKind.DIMENSION),
        session=s,
    )

    assert frame.meta.semantic_kind == "time_series"
    assert frame.meta.axes["time"]["grain"] == "30minute"
    assert frame.meta.axes["time"]["time_dimension"] == "create_time"
    df = frame.to_pandas()
    assert [str(item) for item in df["bucket_start"]] == [
        "2026-05-01 00:00:00",
        "2026-05-01 00:30:00",
    ]
    assert df["revenue"].tolist() == pytest.approx([10.0, 20.0])


def _bootstrap_sales_with_strptime_integer_time_field(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d', data_type='integer'))\n"
        "def log_date(orders):\n"
        "    return orders.log_date\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
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
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
        timescope={"start": "2024-10-11", "end": "2025-08-01"},
        session=s,
    )
    df = frame.to_pandas()
    assert len(df) == 1
    assert df.iloc[0, 0] == pytest.approx(30.0)


def test_observe_expect_shape_accepts_matching_scalar(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    mf = observe(
        SemanticRef("sales.revenue", kind=SemanticKind.METRIC), expect_shape="scalar", session=s
    )

    assert mf.meta.semantic_kind == "scalar"


def test_observe_expect_shape_rejects_mismatch(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    # No grain and no dimensions -> predicted shape is "scalar", not "time_series".
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        observe(
            SemanticRef("sales.revenue", kind=SemanticKind.METRIC),
            expect_shape="time_series",
            session=s,
        )

    rendered = str(excinfo.value)
    assert "time_series" in rendered
    assert "scalar" in rendered
