"""session.observe segmented shape (dimensions, no window grain)."""

import ibis
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.intents.observe import observe
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref


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
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasources"
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
        "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), primary_key=['order_id'], source=md.table('orders'))\n"
        "\n"
        "users = ms.entity(name='users', datasource=md.ref('datasource.warehouse'), primary_key=['user_id'], source=md.table('users'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
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
        "@ms.metric(entities=[orders], additivity='additive', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
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
        "    numerator=failed_count,\n"
        "    denominator=total_count,\n"
        ")\n"
        "\n"
        "@ms.metric(entities=[orders, users], root_entity=orders, additivity='additive', )\n"
        "def revenue_plus_user_count(orders, users):\n"
        "    return orders.amount.sum()\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "from .datasets import orders, users, order_user_id, user_id\n"
        "\n"
        "ms.relationship(\n"
        "    name='orders_to_users',\n"
        "    from_entity=orders,\n"
        "    to_entity=users,\n"
        "    keys=[ms.join_on(order_user_id, user_id)],\n"
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
        session=s,
    )

    assert mf.meta.semantic_kind == "segmented"
    assert "region" in mf.meta.axes
    assert mf.meta.axes["region"]["role"] == "dimension"
    df = mf.to_pandas()
    assert set(df.columns) == {"region", "revenue"}
    by_region = df.set_index("region")["revenue"].to_dict()
    assert by_region == {"NORTH": pytest.approx(70.0), "SOUTH": pytest.approx(30.0)}


def test_ratio_segmented_observation_digest_omits_composition_fields(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        make_ref("sales.failure_rate", SemanticKind.METRIC),
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
        session=s,
    )

    assert mf.meta.additivity == "non_additive"
    observations = s.knowledge().observations()
    assert len(observations) == 1
    digest = observations[0].digest
    assert digest.shape == "segmented"
    assert digest.total_value is None
    assert digest.top_segments
    assert all(entry.share is None for entry in digest.top_segments)
    assert digest.top_segments[0].value is not None


def test_observe_multi_dimension_segmented(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    mf = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        dimensions=[
            make_ref("region", SemanticKind.DIMENSION),
            make_ref("channel", SemanticKind.DIMENSION),
        ],
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
        make_ref("sales.failure_rate", SemanticKind.METRIC),
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
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
        make_ref("sales.failure_rate", SemanticKind.METRIC),
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
        session=s,
    )
    windowed = observe(
        make_ref("sales.failure_rate", SemanticKind.METRIC),
        time_scope={"start": "2026-07-02", "end": "2026-08-02"},
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
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
        "report_tz": s.report_tz_name,
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

    mf = observe(make_ref("sales.failure_rate", SemanticKind.METRIC), session=s)

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
        make_ref("sales.failure_rate", SemanticKind.METRIC),
        dimensions=[make_ref("tier", SemanticKind.DIMENSION)],
        session=s,
    )

    assert mf.meta.semantic_kind == "segmented"
    df = mf.to_pandas()
    assert set(df.columns) == {"tier", "failure_rate"}
    by_tier = df.set_index("tier")["failure_rate"].to_dict()
    assert by_tier == {"gold": pytest.approx(1 / 3), "silver": pytest.approx(1.0)}


def test_observe_empty_dimensions_list_returns_scalar_frame(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    frame = observe(make_ref("sales.revenue", SemanticKind.METRIC), dimensions=[], session=s)

    assert frame.meta.semantic_kind == "scalar"
    df = frame.to_pandas()
    assert set(df.columns) == {"revenue"}
    assert df.iloc[0, 0] == pytest.approx(100.0)


def test_observe_duplicate_dimensions_are_rejected(tmp_path):
    from marivo.analysis.errors import SemanticKindMismatchError

    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            make_ref("sales.revenue", SemanticKind.METRIC),
            dimensions=[
                make_ref("region", SemanticKind.DIMENSION),
                make_ref("region", SemanticKind.DIMENSION),
            ],
            session=s,
        )

    assert exc_info.value._context["expected_kind"] == "unique dimension ids"
    assert exc_info.value._context["duplicate_dimensions"] == ["sales.orders.region"]


def test_observe_segmented_multi_dataset_metric_with_root_dimension(tmp_path):
    """Cross-dataset base metric with root-dataset dimension now works through planner."""
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    frame = observe(
        make_ref("sales.revenue_plus_user_count", SemanticKind.METRIC),
        dimensions=[make_ref("channel", SemanticKind.DIMENSION)],
        session=s,
    )

    assert frame.meta.semantic_kind == "segmented"
    df = frame.to_pandas()
    assert set(df.columns) == {"channel", "revenue_plus_user_count"}


def test_observe_segmented_multi_dataset_missing_dimension_is_blocked(tmp_path):
    """Missing catalog dimension refs are rejected before planning."""
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_users=False)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            make_ref("sales.revenue_plus_user_count", SemanticKind.METRIC),
            dimensions=[make_ref("missing", SemanticKind.DIMENSION)],
            session=s,
        )

    assert exc_info.value._context["actual_kind"] == "not_found"


def test_observe_dimensions_are_persisted_in_job_params_and_digest(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    by_region = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
        session=s,
    )
    by_channel = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        dimensions=[make_ref("channel", SemanticKind.DIMENSION)],
        session=s,
    )

    region_job_summary = next(job for job in s.jobs() if job.output_frame_ref == by_region.ref)
    channel_job_summary = next(job for job in s.jobs() if job.output_frame_ref == by_channel.ref)
    region_job = s.job(region_job_summary.id)
    channel_job = s.job(channel_job_summary.id)

    assert region_job["params"]["dimensions"] == [{"semantic_id": "sales.orders.region"}]
    assert channel_job["params"]["dimensions"] == [{"semantic_id": "sales.orders.channel"}]
    assert (
        by_region.meta.lineage.steps[0].params_digest
        != by_channel.meta.lineage.steps[0].params_digest
    )


def test_observe_dimension_not_found(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            make_ref("sales.revenue", SemanticKind.METRIC),
            dimensions=[make_ref("not_a_real_field", SemanticKind.DIMENSION)],
            session=s,
        )

    assert exc_info.value._context["actual_kind"] == "not_found"
    assert exc_info.value._context["ref"] == "not_a_real_field"


def test_observe_dimension_rejects_bare_string(tmp_path):
    from marivo.analysis.errors import SemanticKindMismatchError

    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends=_backends(con))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        observe(
            make_ref("sales.revenue", SemanticKind.METRIC),
            dimensions=["region"],  # type: ignore[list-item]
            session=s,
        )

    assert exc_info.value._context["expected_kind"] == "dimension"


def test_observe_segmented_derived_ratio_links_aligned_component_frame(tmp_path):
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = session_attach.get_or_create(name="demo", backends=_backends(con))

    frame = observe(
        make_ref("sales.failure_rate", SemanticKind.METRIC),
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
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
