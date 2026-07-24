"""session.observe end-to-end against a seeded DuckDB."""

import inspect
import json
from types import SimpleNamespace

import ibis
import pytest

import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis.errors import (
    AnalysisError,
    MetricNotFoundError,
    NoBackendFactoryError,
    SemanticKindMismatchError,
    SliceEmptyResultError,
    WindowInvalidError,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents.observe import observe
from marivo.semantic.catalog import DerivedMetricDetails, SemanticKind
from tests.conftest import bootstrap_sales_project
from tests.ref_helpers import make_ref
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
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
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
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
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
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', is_default=True)\n"
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
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d'))\n"
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
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='hour', parse=ms.strptime('%Y%m%d%H'))\n"
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
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d'))\n"
        "def log_date(orders):\n"
        "    return orders.log_date\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='hour', parse=ms.hour_prefix(log_date))\n"
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
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n",
            "sales/model.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
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
                "ms.ratio(name='failure_rate', numerator=failed_count, denominator=total_count)\n"
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
    metric = catalog.require(ms.ref.metric("sales.revenue"))

    class GuardedCatalog(SemanticCatalog):
        def __init__(self, wrapped):
            object.__setattr__(self, "_wrapped", wrapped)
            object.__setattr__(self, "_state", wrapped._state)

        def __getattribute__(self, name):
            if name == "_project":
                raise AssertionError("observe planner must not access catalog._project")
            return object.__getattribute__(self, name)

        def require(self, *args, **kwargs):
            return self._wrapped.require(*args, **kwargs)

        def _require_index(self, *args, **kwargs):
            return self._wrapped._require_index(*args, **kwargs)

        def _semantic_resolver(self, *args, **kwargs):
            return self._wrapped._semantic_resolver(*args, **kwargs)

    guarded_catalog = GuardedCatalog(catalog)

    def metric_adapter(ref):
        details = guarded_catalog.require(ms.ref.metric(ref)).details()
        composition_ns = None
        if isinstance(details, DerivedMetricDetails):
            composition_ns = SimpleNamespace(
                kind=details.composition,
                components={role: component.path for role, component in details.components},
            )
        return SimpleNamespace(
            semantic_id=details.ref.path,
            name=details.name,
            root_entity=details.root_entity.path if details.root_entity is not None else None,
            entities=tuple(entity.path for entity in details.entities),
            additivity=details.additivity,
            fanout_policy=details.fanout_policy,
            metric_type=details.metric_type,
            composition=composition_ns,
            time_fold=None,
            status_time_dimension=details.status_time_dimension,
            unit=details.unit,
        )

    dataset_irs = {"sales.orders": SimpleNamespace(datasource_name="warehouse")}
    dataset_fns = {"sales.orders": lambda backend: backend.table("orders")}

    assert metric.ref.path == "sales.revenue"
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
        dimensions=[guarded_catalog.require(ms.ref.dimension("sales.orders.country")).ref],
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
    assert len(derived_plan.leaves) == 2
    assert len(derived_plan.graph.roots) == 1
    assert any(record.node.kind == "ratio" for record in derived_plan.graph.nodes)


def test_observe_returns_metric_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    assert isinstance(mf, MetricFrame)
    assert mf.meta.metric_id == "sales.revenue"
    assert mf.meta.session_id == s.id


def test_observe_single_metric_value_columns_match_metric_name_export(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    assert list(mf.to_pandas().columns) == ["revenue"]
    assert mf.value_columns == ("revenue",)


def test_observe_rejects_bare_metric_string(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe("sales.revenue", session=s)  # type: ignore[arg-type]

    assert exc_info.value._context["expected_type"] == "Ref[metric] or RuntimeMetricExpr"
    assert exc_info.value._context["actual_type"] == "str"
    rendered = str(exc_info.value)
    assert "exact Ref[metric] or RuntimeMetricExpr" in rendered


def test_session_observe_rejects_catalog_object_and_accepts_exact_ref(sales_session, sales_catalog):
    metric = sales_catalog.require(ms.ref.metric("sales.revenue"))
    country = sales_catalog.require(ms.ref.dimension("sales.orders.country")).ref

    with pytest.raises(AnalysisError, match="received MetricEntry"):
        sales_session.observe(metric, dimensions=[country])  # type: ignore[arg-type]
    frame = sales_session.observe(metric.ref, dimensions=[country])

    assert frame.meta.metric_id == "sales.revenue"
    assert "country" in frame.meta.axes


def test_session_observe_rejects_bare_metric_string(sales_session):
    from marivo.analysis.errors import AnalysisError

    with pytest.raises(AnalysisError) as exc:
        sales_session.observe("sales.revenue")

    assert exc.value.location == "observe.metric"


def test_observe_applies_window(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)


def test_observe_string_partition_window_keeps_closed_result_semantics(tmp_path):
    _bootstrap_sales_with_string_partition_time_field(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_string_partition_orders(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2024-10-11", "end": "2025-08-01"},
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2024-10-11T03:00:00", "end": "2025-07-31T14:00:00"},
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2024-10-11T03:00:00", "end": "2025-07-31T14:00:00"},
        time_dimension=make_ref("sales.orders.log_hour", SemanticKind.TIME_DIMENSION),
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
            make_ref("sales.revenue", SemanticKind.METRIC),
            time_scope={"start": "2026-07-01", "end": "2026-07-31"},
            session=s,
        )

    rendered = str(exc_info.value)
    assert "multiple time_dimensions" in rendered
    assert "create_date" in rendered
    assert "create_time" in rendered
    assert (
        'time_dimension=session.catalog.require(ms.ref.time_dimension("<domain.entity.time_dimension>")).ref'
        in rendered
    )
    assert "is_default=True" in rendered


def test_observe_multiple_time_fields_accepts_explicit_time_field(tmp_path):
    _bootstrap_sales_with_two_time_fields(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_two_time_fields(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
        time_dimension=make_ref("sales.orders.create_date", SemanticKind.TIME_DIMENSION),
        session=s,
    )

    assert mf.to_pandas().iloc[0, 0] == pytest.approx(30.0)


def test_observe_uses_default_time_field_when_not_specified(tmp_path):
    _bootstrap_sales_with_default_time_field(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_two_time_fields(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
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
            make_ref("sales.revenue", SemanticKind.METRIC),
            time_scope={"start": "2026-07-01", "end": "2026-07-31"},
            session=s,
        )

    rendered = str(exc_info.value)
    assert "is_default=True" in rendered


def test_observe_applies_slice(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        slice_by={make_ref("sales.orders.region", SemanticKind.DIMENSION): "NORTH"},
        session=s,
    )
    assert mf.to_pandas().iloc[0, 0] == pytest.approx(70.0)


def test_observe_slice_by_empty_result_raises_teaching_error(tmp_path):
    """A slice_by that produces 0 rows must raise a typed teaching error, not
    silently return an empty frame. The check reads only the result row_count
    (no source scan). See issue #26.
    """
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    with pytest.raises(SliceEmptyResultError) as exc_info:
        observe(
            make_ref("sales.revenue", SemanticKind.METRIC),
            time_scope={"start": "2026-07-01", "end": "2026-07-31"},
            grain="day",
            slice_by={make_ref("sales.orders.region", SemanticKind.DIMENSION): "NOPE"},
            session=s,
        )
    err = exc_info.value
    assert err.received == "0 rows"
    assert "slice_by" in err.message
    assert err.repair.kind == "inspect"
    assert "md.inspect" in err.repair.snippet


def test_observe_slice_by_empty_result_in_list_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    with pytest.raises(SliceEmptyResultError):
        observe(
            make_ref("sales.revenue", SemanticKind.METRIC),
            time_scope={"start": "2026-07-01", "end": "2026-07-31"},
            grain="day",
            slice_by={make_ref("sales.orders.region", SemanticKind.DIMENSION): ["NOPE1", "NOPE2"]},
            session=s,
        )


def test_observe_cache_hit_clears_query_capture(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)

    assert s._connection_runtime._capture_buffer is None


def test_observe_rejects_bare_string_time_field(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            make_ref("sales.revenue", SemanticKind.METRIC),
            time_dimension="created_at",
            session=s,
        )
    assert exc_info.value._context["expected_kind"] == "time_dimension"


def test_observe_rejects_bare_string_where_key(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            make_ref("sales.revenue", SemanticKind.METRIC),
            slice_by={"region": "NORTH"},
            session=s,
        )
    assert exc_info.value._context["expected_kind"] == "dimension or time_dimension"


def test_observe_unknown_metric_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    with pytest.raises(MetricNotFoundError):
        observe(make_ref("sales.nonexistent", SemanticKind.METRIC), session=s)


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
        observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    assert exc_info.value.kind == "project_not_loaded"


def test_observe_read_only_session_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(name="demo", use_datasources=False)
    with pytest.raises(NoBackendFactoryError):
        observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)


def test_observe_persists_job_and_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    summaries = s.jobs()
    assert len(summaries) == 1
    assert summaries[0].intent == "observe"
    assert summaries[0].output_frame_ref == mf.ref
    frame_dir = s._layout.frames_dir / mf.ref
    assert (frame_dir / "data.parquet").is_file()
    persisted_meta = json.loads((frame_dir / "meta.json").read_text())
    assert persisted_meta["artifact_schema_version"] == "analysis-artifact/v6"
    assert {
        "metric_id",
        "axes",
        "where",
        "status_time_dimension",
        "semantic_model",
    }.isdisjoint(persisted_meta)
    assert persisted_meta["metric_identity"]["metric_ref"] == {
        "schema": "marivo.semantic_ref/v1",
        "kind": "metric",
        "path": "sales.revenue",
    }
    assert "axis_bindings" in persisted_meta
    assert "slice_predicates" in persisted_meta

    job_path = next(s._layout.jobs_dir.glob("*.json"))
    persisted_job = json.loads(job_path.read_text())
    assert persisted_job["schema"] == "marivo.analysis_job/v2"
    assert persisted_job["subject"]["metric_ref"]["path"] == "sales.revenue"
    assert {
        "semantic_model",
        "semantic_anchors",
        "metric_id",
        "metric_ids",
    }.isdisjoint(persisted_job)
    assert {"metric", "dimensions", "where"}.isdisjoint(persisted_job["params"])

    loaded = s.get_frame(mf.ref)
    assert loaded.meta.metric_id == "sales.revenue"
    assert loaded.meta.semantic_model == "sales"
    assert loaded.meta.axes == mf.meta.axes
    assert loaded.meta.where == mf.meta.where


def test_observe_read_only_session_without_backend_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    # Session without backend factory is read-only and cannot execute.
    s = session_attach.get_or_create(name="demo", use_datasources=False)
    with pytest.raises(NoBackendFactoryError):
        observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)


def test_observe_stale_session_without_backend_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    # Create a session with a backend, then re-open without backend.
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    session_attach._reset_process_state()
    # Re-open without backends -> session becomes read-only.
    s_ro = session_attach.get_or_create(name="demo", use_datasources=False)
    with pytest.raises(NoBackendFactoryError):
        observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s_ro)


def test_observe_frame_survives_reattach(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))
    mf = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
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
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
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
        "failure_rate = ms.ratio(\n"
        "    name='failure_rate',\n"
        "    numerator=failed_count,\n"
        "    denominator=total_count,\n"
        ")\n"
        "\n"
        "failed_count_ratio = ms.ratio(\n"
        "    name='failed_count_ratio',\n"
        "    numerator=failed_count,\n"
        "    denominator=failed_count,\n"
        ")\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', )\n"
        "def succeeded_count(orders):\n"
        "    return (orders.state == 'SUCCEEDED').cast('int64').sum()\n"
        "\n"
        "ms.ratio(\n"
        "    name='failed_per_succeeded',\n"
        "    numerator=failed_count,\n"
        "    denominator=succeeded_count,\n"
        ")\n"
        "\n"
        "ms.ratio(\n"
        "    name='nested_failure_rate',\n"
        "    numerator=failure_rate,\n"
        "    denominator=failed_count_ratio,\n"
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

    frame = observe(make_ref("sales.failure_rate", SemanticKind.METRIC), session=session)

    assert frame.meta.component_ref is not None
    assert frame.meta.composition == {
        "kind": "ratio",
        "components": {
            "numerator": "sales.failed_count",
            "denominator": "sales.total_count",
        },
    }
    assert set(frame.to_pandas().columns) == {"failure_rate"}
    assert "failed_count" not in [c.name for c in frame.contract().artifact_schema.columns]
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

    self_ratio = observe(make_ref("sales.failed_count_ratio", SemanticKind.METRIC), session=session)
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
        make_ref("sales.failure_rate", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-04"},
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
    component_meta = json.loads(
        (session._layout.frames_dir / components.ref / "meta.json").read_text()
    )
    assert {"metric_id", "components", "axes", "semantic_model"}.isdisjoint(component_meta)
    assert component_meta["metric_identity"]["metric_ref"]["path"] == "sales.failure_rate"
    assert [binding["role"] for binding in component_meta["component_bindings"]] == [
        "numerator",
        "denominator",
    ]
    component_df = components.to_pandas()
    assert list(component_df.columns) == [
        "bucket_start",
        "failed_count",
        "total_count",
        "failure_rate",
    ]
    by_bucket = {str(row.bucket_start.date()): row for row in component_df.itertuples()}
    assert by_bucket["2026-07-01"].failed_count == pytest.approx(1.0)
    assert by_bucket["2026-07-01"].total_count == pytest.approx(1.0)
    assert by_bucket["2026-07-01"].failure_rate == pytest.approx(1.0)
    assert by_bucket["2026-07-02"].failed_count == pytest.approx(0.0)
    assert by_bucket["2026-07-02"].total_count == pytest.approx(1.0)
    assert by_bucket["2026-07-02"].failure_rate == pytest.approx(0.0)


def test_observe_nested_catalog_ratio_reuses_leaf_cse(tmp_path):
    _bootstrap_failure_rate(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_failure_rate(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = observe(
        make_ref("sales.nested_failure_rate", SemanticKind.METRIC),
        session=session,
    )

    assert frame.to_pandas().iloc[0]["nested_failure_rate"] == pytest.approx(0.5)
    params = frame.meta.lineage.steps[0].params
    assert len(params["lineage_metadata"]["physical_leaves"]) == 2
    assert len(params["metric_graph"]["nodes"]) == 5
    assert frame.meta.metric_identity is not None
    assert frame.meta.metric_identity.kind == "catalog"
    assert frame.meta.metric_identities == (frame.meta.metric_identity,)
    assert frame.meta.expression_graph is not None
    assert frame.meta.semantic_dependency_digest is not None
    assert frame.meta.key_schema is not None
    assert frame.meta.source_compatibility_domain is not None
    assert frame.meta.comparable_value_semantics is not None
    assert frame.meta.artifact_schema_version == "analysis-artifact/v6"

    store = session._evidence_store()
    assert store is not None
    row = (
        store.read()
        .execute("SELECT subject_payload FROM artifacts WHERE artifact_id = ?", (frame.ref,))
        .fetchone()
    )
    assert row is not None
    subject = json.loads(row["subject_payload"])
    assert subject["typed_metric_subject"] == {
        "kind": "catalog_metric",
        "session_id": session.id,
        "metric_ref": {
            "schema": "marivo.semantic_ref/v1",
            "kind": "metric",
            "path": "sales.nested_failure_rate",
        },
        "artifact_id": frame.ref,
        "scope_fingerprint": subject["typed_metric_subject"]["scope_fingerprint"],
    }


def _seed_zero_denominator_orders(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at DATE, state VARCHAR)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 'FAILED'),"
        "(2, DATE '2026-07-01', 'SUCCEEDED'),"
        "(3, DATE '2026-07-02', 'FAILED'),"
        "(4, DATE '2026-07-02', 'FAILED'),"
        "(5, DATE '2026-07-03', 'SUCCEEDED'),"
        "(6, DATE '2026-07-04', 'PENDING')"
    )


def test_observe_derived_ratio_zero_denominator_yields_null_with_quality_count(tmp_path):
    import numpy as np
    import pandas as pd

    _bootstrap_failure_rate(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_zero_denominator_orders(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = observe(
        make_ref("sales.failed_per_succeeded", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-05"},
        grain="day",
        session=session,
    )

    df = frame.to_pandas()
    assert not np.isinf(df["failed_per_succeeded"]).any()
    by_bucket = {str(row.bucket_start.date()): row for row in df.itertuples()}
    assert by_bucket["2026-07-01"].failed_per_succeeded == pytest.approx(1.0)
    # A present zero denominator yields null, never +/-inf (2 failed / 0 succeeded).
    assert pd.isna(by_bucket["2026-07-02"].failed_per_succeeded)
    assert by_bucket["2026-07-03"].failed_per_succeeded == pytest.approx(0.0)
    # 0 / 0 is also a present zero denominator, not only a source null.
    assert pd.isna(by_bucket["2026-07-04"].failed_per_succeeded)

    # Affected rows are counted in quality metadata and survive recomputation.
    assert frame.meta.zero_denominator_rows == 2
    assert frame.meta.quality_summary is not None
    assert frame.meta.quality_summary.zero_denominator_rows == 2
    # The zero-division policy participates in the observe params so pre-policy
    # cached artifacts cannot be reused as if they matched these semantics.
    assert frame.meta.lineage.steps[0].params["zero_division"] == "null"

    component_df = frame.components().to_pandas()
    zero_rows = {str(row.bucket_start.date()): row for row in component_df.itertuples()}
    assert zero_rows["2026-07-02"].failed_count == pytest.approx(2.0)
    assert zero_rows["2026-07-02"].succeeded_count == pytest.approx(0.0)
    assert pd.isna(zero_rows["2026-07-02"].failed_per_succeeded)


def test_transform_clears_zero_denominator_rows_from_parent(tmp_path):
    _bootstrap_failure_rate(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_zero_denominator_orders(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = observe(
        make_ref("sales.failed_per_succeeded", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-05"},
        grain="day",
        session=session,
    )
    assert frame.meta.zero_denominator_rows == 2

    # Clip to the one bucket without a zero denominator: the parent's count no
    # longer describes the surviving rows, so the transformed frame must not
    # carry it into its meta, quality summary, or persisted evidence.
    windowed = frame.transform.window(window={"start": "2026-07-01", "end": "2026-07-02"})
    assert windowed.meta.zero_denominator_rows is None
    assert windowed.meta.quality_summary is not None
    assert windowed.meta.quality_summary.zero_denominator_rows is None


def test_observe_time_series_with_empty_dimensions_list(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    frame = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-10-01"},
        grain="day",
        dimensions=[],
        session=s,
    )

    assert frame.meta.semantic_kind == "time_series"
    assert set(frame.to_pandas().columns) == {"bucket_start", "revenue"}


def _bootstrap_sales_with_strptime_slash_time_field(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y/%m/%d'))\n"
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2024-10-11", "end": "2025-08-01"},
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2024-10-10", "end": "2025-08-02"},
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
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='minute', parse=ms.strptime('%Y-%m-%d %H:%M:%S', timezone='UTC'))\n"
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-05-01", "end": "2026-05-02"},
        grain=(30, "minute"),
        time_dimension=make_ref("sales.orders.create_time", SemanticKind.TIME_DIMENSION),
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
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d'))\n"
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2024-10-11", "end": "2025-08-01"},
        session=s,
    )
    df = frame.to_pandas()
    assert len(df) == 1
    assert df.iloc[0, 0] == pytest.approx(30.0)


def test_observe_expect_shape_accepts_matching_scalar(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    mf = observe(make_ref("sales.revenue", SemanticKind.METRIC), expect_shape="scalar", session=s)

    assert mf.meta.semantic_kind == "scalar"


def test_observe_expect_shape_rejects_mismatch(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = connect_sales_orders()
    s = session_attach.get_or_create(name="demo", backends=sales_backends(con))

    # No grain and no dimensions -> predicted shape is "scalar", not "time_series".
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        observe(
            make_ref("sales.revenue", SemanticKind.METRIC),
            expect_shape="time_series",
            session=s,
        )

    rendered = str(excinfo.value)
    assert "time_series" in rendered
    assert "scalar" in rendered
