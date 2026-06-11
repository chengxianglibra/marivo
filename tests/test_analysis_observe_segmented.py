"""session.observe segmented shape (dimensions, no window grain)."""

import ibis
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.intents.observe import observe
from marivo.analysis.refs import DimensionRef, MetricRef


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
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "\n"
        "users = ms.entity(name='users', datasource='warehouse', primary_key=['user_id'], source=ms.table('users'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def channel(orders):\n"
        "    return orders.channel\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def order_user_id(orders):\n"
        "    return orders.user_id\n"
        "\n"
        "@ms.dimension(name='user_region', entity=users)\n"
        "def user_region(users):\n"
        "    return users.tier\n"
        "\n"
        "@ms.dimension(entity=users)\n"
        "def tier(users):\n"
        "    return users.tier\n"
        "\n"
        "@ms.dimension(entity=users)\n"
        "def user_id(users):\n"
        "    return users.user_id\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)\n"
        "def failed_count(orders):\n"
        "    return (orders.state == 'FAILED').cast('int64').sum()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)\n"
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
        "@ms.metric(entities=[orders, users], root_entity=orders, additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)\n"
        "def revenue_plus_user_count(orders, users):\n"
        "    return orders.amount.sum()\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.semantic as ms\n"
        "from .datasets import orders, users, order_user_id, user_id\n"
        "\n"
        "ms.relationship(\n"
        "    name='orders_to_users',\n"
        "    from_entity=orders,\n"
        "    to_entity=users,\n"
        "    from_dimensions=[order_user_id],\n"
        "    to_dimensions=[user_id],\n"
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


def test_observe_derived_metric_dimension_honors_timescope(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    full = observe(
        MetricRef("sales.failure_rate"),
        dimensions=[DimensionRef("region")],
        session=s,
    )
    windowed = observe(
        MetricRef("sales.failure_rate"),
        timescope={"start": "2026-07-02", "end": "2026-08-02"},
        dimensions=[DimensionRef("region")],
        session=s,
    )

    assert windowed.meta.semantic_kind == "segmented"
    assert "time" not in windowed.meta.axes
    assert windowed.meta.window == {
        "kind": "absolute",
        "start": "2026-07-02",
        "end": "2026-08-02",
        "grain": None,
        "time_dimension": None,
    }
    job = s.job(windowed.meta.produced_by_job)
    assert job["params"]["timescope"] == {
        "original": {"start": "2026-07-02", "end": "2026-08-02"},
        "resolved": windowed.meta.window,
        "session_tz": str(s.tz),
    }
    windowed_by_region = windowed.to_pandas().set_index("region")["failure_rate"].to_dict()
    full_by_region = full.to_pandas().set_index("region")["failure_rate"].to_dict()
    assert windowed_by_region == {"NORTH": pytest.approx(0.0), "SOUTH": pytest.approx(1.0)}
    assert windowed_by_region["NORTH"] != pytest.approx(full_by_region["NORTH"])

    assert windowed.meta.component_ref is not None
    component_df = windowed.components().to_pandas().set_index("region")
    assert component_df.loc["NORTH", "failed_count"] == pytest.approx(0.0)
    assert component_df.loc["NORTH", "total_count"] == pytest.approx(1.0)
    assert component_df.loc["NORTH", "failure_rate"] == pytest.approx(0.0)
    assert component_df.loc["SOUTH", "failed_count"] == pytest.approx(1.0)
    assert component_df.loc["SOUTH", "total_count"] == pytest.approx(1.0)
    assert component_df.loc["SOUTH", "failure_rate"] == pytest.approx(1.0)


def test_observe_derived_metric_scalar_uses_component_datasets(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(MetricRef("sales.failure_rate"), session=s)

    assert mf.meta.semantic_kind == "scalar"
    assert mf.meta.measure["name"] == "failure_rate"
    df = mf.to_pandas()
    assert set(df.columns) == {"failure_rate"}
    assert df.iloc[0, 0] == pytest.approx(0.5)


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
    from marivo.analysis.errors import SemanticKindMismatchError

    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(MetricRef("sales.revenue"), dimensions=[], session=s)

    assert "For time-series observations, omit dimensions or pass None" in str(exc_info.value)


def test_observe_duplicate_dimensions_are_rejected(tmp_path):
    from marivo.analysis.errors import SemanticKindMismatchError

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


def test_observe_segmented_multi_dataset_metric_with_root_dimension(tmp_path):
    """Cross-dataset base metric with root-dataset dimension now works through planner."""
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    frame = observe(
        MetricRef("sales.revenue_plus_user_count"),
        dimensions=[DimensionRef("channel")],
        session=s,
    )

    assert frame.meta.semantic_kind == "segmented"
    df = frame.to_pandas()
    assert set(df.columns) == {"channel", "revenue_plus_user_count"}


def test_observe_segmented_multi_dataset_missing_dimension_is_blocked(tmp_path):
    """Cross-dataset base metric with missing dimension field raises planner error."""
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_users=False)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    from marivo.analysis.intents.observe_errors import ObservePlanningError

    with pytest.raises(ObservePlanningError) as exc_info:
        observe(
            MetricRef("sales.revenue_plus_user_count"),
            dimensions=[DimensionRef("missing")],
            session=s,
        )

    assert exc_info.value.details["code"] == "field-ref-not-found"


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

    assert region_job["params"]["dimensions"] == [{"semantic_id": "region"}]
    assert channel_job["params"]["dimensions"] == [{"semantic_id": "channel"}]
    assert (
        by_region.meta.lineage.steps[0].params_digest
        != by_channel.meta.lineage.steps[0].params_digest
    )


def test_observe_dimension_not_found(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    from marivo.analysis.intents.observe_errors import ObservePlanningError

    with pytest.raises(ObservePlanningError) as exc_info:
        observe(
            MetricRef("sales.revenue"),
            dimensions=[DimensionRef("not_a_real_field")],
            session=s,
        )

    assert exc_info.value.details["code"] == "field-ref-not-found"
    assert "searched_datasets" in exc_info.value.details["candidates"]


def test_observe_dimension_rejects_bare_string(tmp_path):
    from marivo.analysis.errors import SemanticKindMismatchError

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


def test_observe_segmented_derived_ratio_links_aligned_component_frame(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = session_attach.get_or_create(name="demo", backends=_backends(con))

    frame = observe(
        MetricRef("sales.failure_rate"),
        dimensions=[DimensionRef("region")],
        session=session,
    )

    assert frame.meta.component_ref is not None
    assert set(frame.to_pandas().columns) == {"region", "failure_rate"}
    components = frame.components()
    assert components.meta.parent_ref == frame.ref
    component_df = components.to_pandas()
    assert list(component_df.columns) == ["region", "failed_count", "total_count", "failure_rate"]
    by_region = component_df.set_index("region")
    assert by_region.loc["NORTH", "failed_count"] == pytest.approx(1.0)
    assert by_region.loc["NORTH", "total_count"] == pytest.approx(3.0)
    assert by_region.loc["NORTH", "failure_rate"] == pytest.approx(1.0 / 3.0)
    assert by_region.loc["SOUTH", "failed_count"] == pytest.approx(1.0)
    assert by_region.loc["SOUTH", "total_count"] == pytest.approx(1.0)
    assert by_region.loc["SOUTH", "failure_rate"] == pytest.approx(1.0)
