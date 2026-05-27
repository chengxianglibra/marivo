"""mv.observe segmented shape (dimensions, no window grain)."""

import ibis
import pytest

import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import (
    DimensionAcrossDatasetsError,
    DimensionFieldNotFoundError,
    MetricShapeUnsupportedError,
)
from marivo.analysis_py.intents.observe import observe
from marivo.analysis_py.refs import DimensionRef, MetricRef


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _seed(con, *, with_users: bool = True):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, channel VARCHAR, user_id INTEGER, state VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 'north', 'web', 100, 'FAILED'),"
        "(2, DATE '2026-07-02', 20.0, 'north', 'app', 100, 'SUCCEEDED'),"
        "(3, DATE '2026-08-01', 30.0, 'south', 'web', 200, 'FAILED'),"
        "(4, DATE '2026-09-15', 40.0, 'north', 'app', 300, 'SUCCEEDED')"
    )
    if with_users:
        con.raw_sql("CREATE TABLE users (user_id INTEGER, tier VARCHAR)")
        con.raw_sql("INSERT INTO users VALUES (100, 'gold'),(200, 'silver'),(300, 'gold')")


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
        "@ms.dataset(name='users', datasource='warehouse')\n"
        "def users(backend):\n"
        "    return backend.table('users')\n"
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
        "@ms.field(dataset=orders)\n"
        "def order_user_id(orders):\n"
        "    return orders.user_id\n"
        "\n"
        "@ms.field(name='user_region', dataset=users)\n"
        "def user_region(users):\n"
        "    return users.tier\n"
        "\n"
        "@ms.field(dataset=users)\n"
        "def tier(users):\n"
        "    return users.tier\n"
        "\n"
        "@ms.field(dataset=users)\n"
        "def user_id(users):\n"
        "    return users.user_id\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum())\n"
        "def failed_count(orders):\n"
        "    return (orders.state == 'FAILED').cast('int64').sum()\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum())\n"
        "def total_count(orders):\n"
        "    return orders.count()\n"
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
        "@ms.metric(datasets=[orders, users], decomposition=ms.sum())\n"
        "def revenue_plus_user_count(orders, users):\n"
        "    return orders.amount.sum() + users.user_id.count()\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.semantic_py as ms\n"
        "from .datasets import orders, users, order_user_id, user_id\n"
        "\n"
        "ms.relationship(\n"
        "    name='orders_to_users',\n"
        "    from_dataset=orders,\n"
        "    to_dataset=users,\n"
        "    from_fields=[order_user_id],\n"
        "    to_fields=[user_id],\n"
        ")\n"
    )


def _backends(con):
    return {"warehouse": lambda: con}


def test_observe_single_dimension_returns_segmented_frame(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.revenue"),
        dimensions=[DimensionRef("region")],
        session=s,
    )

    assert mf.meta.semantic_kind == "segmented"
    assert "region" in mf.meta.axes
    assert mf.meta.axes["region"]["role"] == "dimension"
    df = mf.to_pandas()
    assert set(df.columns) == {"region", "revenue"}
    by_region = df.set_index("region")["revenue"].to_dict()
    assert by_region == {"NORTH": pytest.approx(70.0), "SOUTH": pytest.approx(30.0)}


def test_observe_multi_dimension_segmented(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.revenue"),
        dimensions=[DimensionRef("region"), DimensionRef("channel")],
        session=s,
    )

    assert mf.meta.semantic_kind == "segmented"
    df = mf.to_pandas()
    assert {"region", "channel", "revenue"} == set(df.columns)


def test_observe_derived_metric_dimension_from_component_dataset(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.failure_rate"),
        dimensions=[DimensionRef("region")],
        session=s,
    )

    assert mf.meta.semantic_kind == "segmented"
    assert mf.meta.measure["name"] == "failure_rate"
    df = mf.to_pandas()
    assert set(df.columns) == {"region", "failure_rate"}
    by_region = df.set_index("region")["failure_rate"].to_dict()
    assert by_region == {"NORTH": pytest.approx(1 / 3), "SOUTH": pytest.approx(1.0)}


def test_observe_derived_metric_dimension_via_relationship(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        MetricRef("sales.failure_rate"),
        dimensions=[DimensionRef("tier")],
        session=s,
    )

    assert mf.meta.semantic_kind == "segmented"
    df = mf.to_pandas()
    assert set(df.columns) == {"tier", "failure_rate"}
    by_tier = df.set_index("tier")["failure_rate"].to_dict()
    assert by_tier == {"gold": pytest.approx(1 / 3), "silver": pytest.approx(1.0)}


def test_observe_empty_dimensions_list_is_rejected(tmp_path):
    from marivo.analysis_py.errors import SemanticKindMismatchError

    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(SemanticKindMismatchError):
        observe(MetricRef("sales.revenue"), dimensions=[], session=s)


def test_observe_duplicate_dimensions_are_rejected(tmp_path):
    from marivo.analysis_py.errors import SemanticKindMismatchError

    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            MetricRef("sales.revenue"),
            dimensions=[DimensionRef("region"), DimensionRef("region")],
            session=s,
        )

    assert exc_info.value.details["expected_kind"] == "unique DimensionRef ids"
    assert exc_info.value.details["duplicate_dimensions"] == ["region"]


def test_observe_segmented_rejects_multi_dataset_metric(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(MetricShapeUnsupportedError) as exc_info:
        observe(
            MetricRef("sales.revenue_plus_user_count"),
            dimensions=[DimensionRef("channel")],
            session=s,
        )

    assert exc_info.value.details["kind"] == "SegmentedMultiDatasetUnsupported"
    assert exc_info.value.details["metric"] == "sales.revenue_plus_user_count"
    assert exc_info.value.details["datasets"] == ["sales.orders", "sales.users"]
    assert exc_info.value.details["dimensions"] == [{"id": "channel"}]


def test_observe_multi_dataset_missing_dimension_resolves_before_shape(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_users=False)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(DimensionFieldNotFoundError) as exc_info:
        observe(
            MetricRef("sales.revenue_plus_user_count"),
            dimensions=[DimensionRef("missing")],
            session=s,
        )

    assert exc_info.value.details["dimension_id"] == "missing"
    assert exc_info.value.details["searched_datasets"] == ["sales.orders", "sales.users"]


def test_observe_multi_dataset_full_dimension_resolves_before_shape(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_users=False)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(MetricShapeUnsupportedError) as exc_info:
        observe(
            MetricRef("sales.revenue_plus_user_count"),
            dimensions=[DimensionRef("sales.user_region")],
            session=s,
        )

    assert exc_info.value.details["kind"] == "SegmentedMultiDatasetUnsupported"


def test_observe_multi_dataset_cross_dataset_dimensions_resolve_before_shape(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_users=False)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(DimensionAcrossDatasetsError) as exc_info:
        observe(
            MetricRef("sales.revenue_plus_user_count"),
            dimensions=[DimensionRef("channel"), DimensionRef("tier")],
            session=s,
        )

    assert exc_info.value.details["dimensions_by_dataset"] == {
        "sales.orders": ["channel"],
        "sales.users": ["tier"],
    }


def test_observe_segmented_rejects_multi_dataset_metric_before_materialization(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_users=False)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(MetricShapeUnsupportedError) as exc_info:
        observe(
            MetricRef("sales.revenue_plus_user_count"),
            dimensions=[DimensionRef("channel")],
            session=s,
        )

    assert exc_info.value.details["kind"] == "SegmentedMultiDatasetUnsupported"
    assert exc_info.value.details["metric"] == "sales.revenue_plus_user_count"


def test_observe_dimensions_are_persisted_in_job_params_and_digest(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    by_region = observe(
        MetricRef("sales.revenue"),
        dimensions=[DimensionRef("region")],
        session=s,
    )
    by_channel = observe(
        MetricRef("sales.revenue"),
        dimensions=[DimensionRef("channel")],
        session=s,
    )

    region_job_summary = next(job for job in s.jobs() if job.output_frame_ref == by_region.ref)
    channel_job_summary = next(job for job in s.jobs() if job.output_frame_ref == by_channel.ref)
    region_job = s.job(region_job_summary.id)
    channel_job = s.job(channel_job_summary.id)

    assert region_job["params"]["dimensions"] == [{"id": "region"}]
    assert channel_job["params"]["dimensions"] == [{"id": "channel"}]
    assert (
        by_region.meta.lineage.steps[0].params_digest
        != by_channel.meta.lineage.steps[0].params_digest
    )


def test_observe_dimension_not_found(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(DimensionFieldNotFoundError) as exc_info:
        observe(
            MetricRef("sales.revenue"),
            dimensions=[DimensionRef("not_a_real_field")],
            session=s,
        )

    assert exc_info.value.details["dimension_id"] == "not_a_real_field"


def test_observe_dimension_rejects_bare_string(tmp_path):
    from marivo.analysis_py.errors import SemanticKindMismatchError

    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            MetricRef("sales.revenue"),
            dimensions=["region"],  # type: ignore[list-item]
            session=s,
        )

    assert exc_info.value.details["expected_kind"] == "DimensionRef"
