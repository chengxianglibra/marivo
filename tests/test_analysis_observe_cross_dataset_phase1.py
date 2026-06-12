"""Cross-dataset base observe Phase 1 end-to-end tests."""

from __future__ import annotations

import ibis
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.intents.observe import observe
from marivo.analysis.intents.observe_errors import ObservePlanningError
from marivo.semantic.catalog import SemanticKind, SemanticRef


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _seed(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, user_id INTEGER, channel VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 100, 'web'),"
        "(2, DATE '2026-07-02', 20.0, 100, 'app'),"
        "(3, DATE '2026-07-03', 30.0, 200, 'web'),"
        "(4, DATE '2026-07-04', 40.0, 999, 'app')"
    )
    con.raw_sql("CREATE TABLE users (user_id INTEGER, tier VARCHAR, country VARCHAR)")
    con.raw_sql("INSERT INTO users VALUES (100, 'gold', 'US'), (200, 'silver', 'CA')")


def _bootstrap(tmp_path, *, root: str = "orders"):
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
    root_line = "root_entity=orders" if root == "orders" else "root_entity=users"
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "users = ms.entity(name='users', datasource='warehouse', primary_key=['user_id'], source=ms.table('users'))\n"
        "@ms.time_dimension(entity=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "@ms.dimension(entity=orders)\n"
        "def order_user_id(orders):\n"
        "    return orders.user_id\n"
        "@ms.dimension(entity=orders)\n"
        "def channel(orders):\n"
        "    return orders.channel\n"
        "@ms.dimension(entity=users)\n"
        "def user_id(users):\n"
        "    return users.user_id\n"
        "@ms.dimension(entity=users)\n"
        "def tier(users):\n"
        "    return users.tier\n"
        "@ms.dimension(entity=users)\n"
        "def country(users):\n"
        "    return users.country\n"
        "@ms.metric(\n"
        "    entities=[orders, users],\n"
        f"    {root_line},\n"
        "    additivity='additive',\n"
        "    decomposition=ms.sum(),\n"
        "    name='revenue_by_user',\n"
        "    verification_mode='python_native',\n"
        "    )\n"
        "def revenue_by_user(orders, users):\n"
        "    return orders.amount.sum()\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.semantic as ms\n"
        "from .datasets import orders, users, order_user_id, user_id\n"
        "ms.relationship(\n"
        "    name='orders_to_users',\n"
        "    from_entity=orders,\n"
        "    to_entity=users,\n"
        "    from_dimensions=[order_user_id],\n"
        "    to_dimensions=[user_id],\n"
        ")\n"
    )


def _session(con):
    return session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})


def test_segmented_cross_dataset_dimension_preserves_unmatched_root_rows(tmp_path):
    _bootstrap(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    frame = observe(
        SemanticRef("sales.revenue_by_user", kind=SemanticKind.METRIC),
        dimensions=[SemanticRef("sales.users.tier", kind=SemanticKind.DIMENSION)],
        session=_session(con),
    )

    assert frame.meta.semantic_kind == "segmented"
    df = frame.to_pandas()
    assert set(df.columns) == {"tier", "revenue_by_user"}
    by_tier = {row.tier: row.revenue_by_user for row in df.itertuples()}
    assert by_tier["gold"] == pytest.approx(30.0)
    assert by_tier["silver"] == pytest.approx(30.0)
    # Unmatched root rows (user_id=999) produce NULL tier after left join,
    # which pandas represents as NaN (float nan).  Find the null key.
    null_key = next(k for k in by_tier if k is None or (isinstance(k, float) and k != k))
    assert by_tier[null_key] == pytest.approx(40.0)


def test_cross_dataset_where_filters_after_left_join(tmp_path):
    _bootstrap(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    frame = observe(
        SemanticRef("sales.revenue_by_user", kind=SemanticKind.METRIC),
        where={SemanticRef("sales.users.country", kind=SemanticKind.DIMENSION): "US"},
        session=_session(con),
    )

    assert frame.meta.semantic_kind == "scalar"
    assert frame.to_pandas().iloc[0, 0] == pytest.approx(30.0)
    # normalize_slice_for_storage compresses simple == to just the value
    assert frame.meta.where == {"sales.users.country": "US"}


def test_panel_cross_dataset_dimension_uses_root_time_axis(tmp_path):
    _bootstrap(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    frame = observe(
        SemanticRef("sales.revenue_by_user", kind=SemanticKind.METRIC),
        timescope={"start": "2026-07-01", "end": "2026-07-05"},
        grain="day",
        dimensions=[SemanticRef("sales.users.tier", kind=SemanticKind.DIMENSION)],
        session=_session(con),
    )

    assert frame.meta.semantic_kind == "panel"
    assert frame.meta.axes["time"]["time_dimension"] == "order_date"
    assert set(frame.to_pandas().columns) == {"bucket_start", "tier", "revenue_by_user"}


def test_one_to_many_traversal_is_blocked(tmp_path):
    """When root=orders but a dimension requires a one-to-many join,
    the planner must block the traversal with unsafe-fanout."""
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
    # Use field names that match primary_key columns for join safety detection:
    # orders.primary_key=['order_id'] -> need field.name='order_id'
    # order_items.primary_key=['item_id'] -> need field.name='item_id', and
    #   order_items.order_id is NOT a key -> one-to-many from orders
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "order_items = ms.entity(name='order_items', datasource='warehouse', primary_key=['item_id'], source=ms.table('order_items'))\n"
        "@ms.time_dimension(entity=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "@ms.dimension(entity=orders)\n"
        "def order_id(orders):\n"
        "    return orders.order_id\n"
        "@ms.dimension(entity=order_items)\n"
        "def item_order_id(order_items):\n"
        "    return order_items.order_id\n"
        "@ms.dimension(entity=order_items)\n"
        "def item_name(order_items):\n"
        "    return order_items.name\n"
        "@ms.metric(\n"
        "    entities=[orders, order_items],\n"
        "    root_entity=orders,\n"
        "    additivity='additive',\n"
        "    decomposition=ms.sum(),\n"
        "    name='order_total_with_items',\n"
        "    verification_mode='python_native',\n"
        "    )\n"
        "def order_total_with_items(orders, order_items):\n"
        "    return orders.amount.sum()\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.semantic as ms\n"
        "from .datasets import orders, order_items, order_id, item_order_id\n"
        "ms.relationship(\n"
        "    name='orders_to_order_items',\n"
        "    from_entity=orders,\n"
        "    to_entity=order_items,\n"
        "    from_dimensions=[order_id],\n"
        "    to_dimensions=[item_order_id],\n"
        ")\n"
    )

    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, user_id INTEGER, channel VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 100, 'web'),"
        "(2, DATE '2026-07-02', 20.0, 100, 'app'),"
        "(3, DATE '2026-07-03', 30.0, 200, 'web')"
    )
    con.raw_sql("CREATE TABLE order_items (item_id INTEGER, order_id INTEGER, name VARCHAR)")
    con.raw_sql("INSERT INTO order_items VALUES (1, 1, 'shirt'), (2, 1, 'pants'), (3, 2, 'shirt')")

    with pytest.raises(ObservePlanningError) as exc_info:
        observe(
            SemanticRef("sales.order_total_with_items", kind=SemanticKind.METRIC),
            dimensions=[SemanticRef("sales.order_items.item_name", kind=SemanticKind.DIMENSION)],
            session=_session(con),
        )

    assert exc_info.value.details["code"] == "unsafe-fanout"


def _bootstrap_snapshot(tmp_path):
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
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "user_profile_daily = ms.entity(\n"
        "    name='user_profile_daily',\n"
        "    datasource='warehouse',\n"
        "    source=ms.table('user_profile_daily'),\n"
        "    primary_key=['user_id', 'dt'],\n"
        "    versioning=ms.snapshot(partition_field='dt', grain='day', timezone='Asia/Shanghai', format='%Y%m%d'),\n"
        ")\n"
        "@ms.time_dimension(entity=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "@ms.dimension(entity=orders)\n"
        "def order_user_id(orders):\n"
        "    return orders.user_id\n"
        "@ms.dimension(entity=user_profile_daily)\n"
        "def user_id(user_profile_daily):\n"
        "    return user_profile_daily.user_id\n"
        "@ms.dimension(entity=user_profile_daily)\n"
        "def dt(user_profile_daily):\n"
        "    return user_profile_daily.dt\n"
        "@ms.dimension(entity=user_profile_daily)\n"
        "def tier(user_profile_daily):\n"
        "    return user_profile_daily.tier\n"
        "@ms.metric(\n"
        "    entities=[orders, user_profile_daily],\n"
        "    root_entity=orders,\n"
        "    additivity='additive',\n"
        "    decomposition=ms.sum(),\n"
        "    name='revenue_by_profile',\n"
        "    verification_mode='python_native',\n"
        "    )\n"
        "def revenue_by_profile(orders, user_profile_daily):\n"
        "    return orders.amount.sum()\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.semantic as ms\n"
        "from .datasets import orders, user_profile_daily, order_user_id, user_id\n"
        "ms.relationship(\n"
        "    name='orders_to_profile',\n"
        "    from_entity=orders,\n"
        "    to_entity=user_profile_daily,\n"
        "    from_dimensions=[order_user_id],\n"
        "    to_dimensions=[user_id],\n"
        ")\n"
    )


def _seed_snapshot(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 100),"
        "(2, DATE '2026-07-02', 20.0, 200)"
    )
    con.raw_sql("CREATE TABLE user_profile_daily (user_id INTEGER, dt VARCHAR, tier VARCHAR)")
    con.raw_sql(
        "INSERT INTO user_profile_daily VALUES "
        "(100, '20260630', 'old_gold'),"
        "(100, '20260701', 'gold'),"
        "(200, '20260630', 'silver'),"
        "(200, '20260701', 'new_silver')"
    )


def test_snapshot_as_of_root_time_per_row_partition(tmp_path):
    _bootstrap_snapshot(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_snapshot(con)
    frame = observe(
        SemanticRef("sales.revenue_by_profile", kind=SemanticKind.METRIC),
        timescope={"start": "2026-07-01", "end": "2026-07-03"},
        dimensions=[SemanticRef("sales.user_profile_daily.tier", kind=SemanticKind.DIMENSION)],
        session=_session(con),
    )

    by_tier = frame.to_pandas().set_index("tier")["revenue_by_profile"].to_dict()
    # With as_of_root_time: order 1 (2026-07-01) -> partition 20260701 -> tier 'gold'
    # order 2 (2026-07-02) -> partition 20260701 -> tier 'new_silver'
    assert by_tier == {"gold": pytest.approx(10.0), "new_silver": pytest.approx(20.0)}
    assert frame.meta.lineage.steps[0].params_digest.startswith("sha256:")


def test_relationships_lineage_records_distinct_from_and_to_dataset(tmp_path):
    _bootstrap(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    frame = observe(
        SemanticRef("sales.revenue_by_user", kind=SemanticKind.METRIC),
        dimensions=[SemanticRef("sales.users.tier", kind=SemanticKind.DIMENSION)],
        session=_session(con),
    )

    relationships = frame.meta.lineage.steps[0].params.get("relationships") or []
    assert len(relationships) == 1
    edge = relationships[0]
    assert edge["relationship"] == "sales.orders_to_users"
    assert edge["from_dataset"] == "sales.orders"
    assert edge["to_dataset"] == "sales.users"
    assert edge["from_dataset"] != edge["to_dataset"]
