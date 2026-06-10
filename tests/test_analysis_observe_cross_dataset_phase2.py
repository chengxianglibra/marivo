"""Cross-dataset base + derived observe Phase 2 end-to-end tests."""

from __future__ import annotations

import datetime as dt

import ibis
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis.intents.observe import observe
from marivo.analysis.intents.observe_errors import ObservePlanningError
from marivo.analysis.refs import DimensionRef, MetricRef


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _session(con):
    return session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})


def _bootstrap_snapshot_as_of(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
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
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "user_profile_daily = ms.entity(\n"
        "    name='user_profile_daily',\n"
        "    datasource='warehouse',\n"
        "    source=ms.table('user_profile_daily'),\n"
        "    primary_key=['user_id', 'dt'],\n"
        "    versioning=ms.snapshot(partition_field='dt', grain='day', timezone='UTC', format='%Y%m%d'),\n"
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


def _seed_snapshot_as_of(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 100),"
        "(2, DATE '2026-07-05', 20.0, 100)"
    )
    con.raw_sql("CREATE TABLE user_profile_daily (user_id INTEGER, dt VARCHAR, tier VARCHAR)")
    con.raw_sql(
        "INSERT INTO user_profile_daily VALUES "
        "(100, '20260701', 'old_gold'),"
        "(100, '20260705', 'new_gold')"
    )


def test_snapshot_as_of_root_time_picks_per_row_partition(tmp_path):
    _bootstrap_snapshot_as_of(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_snapshot_as_of(con)
    frame = observe(
        MetricRef("sales.revenue_by_profile"),
        timescope={"start": "2026-07-01", "end": "2026-07-06"},
        dimensions=[DimensionRef("sales.user_profile_daily.tier")],
        session=_session(con),
    )

    df = frame.to_pandas().set_index("tier")["revenue_by_profile"].to_dict()
    assert df == {"old_gold": pytest.approx(10.0), "new_gold": pytest.approx(20.0)}

    versions = frame.meta.lineage.steps[0].params.get("version_resolutions") or []
    assert any(v["mode"] == "as_of_root_time" for v in versions)
    snapshot_version = next(v for v in versions if v["dataset"] == "sales.user_profile_daily")
    assert snapshot_version["anchor_to_partition_mapping_digest"].startswith("sha256:")
    assert snapshot_version["resolved_partition_summary"]["partition_count"] == 2


def test_snapshot_as_of_root_time_partition_missing(tmp_path):
    _bootstrap_snapshot_as_of(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_snapshot_as_of(con)
    con.raw_sql("DELETE FROM user_profile_daily WHERE dt = '20260701'")

    with pytest.raises(ObservePlanningError) as exc_info:
        observe(
            MetricRef("sales.revenue_by_profile"),
            timescope={"start": "2026-07-01", "end": "2026-07-06"},
            dimensions=[DimensionRef("sales.user_profile_daily.tier")],
            session=_session(con),
        )

    details = exc_info.value.details
    assert details["code"] == "snapshot-partition-missing"
    assert details["candidates"]["dataset"] == "sales.user_profile_daily"
    assert "2026-07-01" in details["candidates"]["missing_anchors"]


def _bootstrap_snapshot_latest_no_root_time(tmp_path):
    """Fixture where orders has NO time field, so _derive_version_mode falls back to latest."""
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
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
    # orders has NO @ms.time_dimension — forces latest mode in _derive_version_mode
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "user_profile_daily = ms.entity(\n"
        "    name='user_profile_daily',\n"
        "    datasource='warehouse',\n"
        "    source=ms.table('user_profile_daily'),\n"
        "    primary_key=['user_id', 'dt'],\n"
        "    versioning=ms.snapshot(partition_field='dt', grain='day', timezone='UTC', format='%Y%m%d'),\n"
        ")\n"
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


def _seed_snapshot_latest_no_root_time(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, amount DOUBLE, user_id INTEGER)")
    con.raw_sql("INSERT INTO orders VALUES (1, 10.0, 100),(2, 20.0, 100)")
    con.raw_sql("CREATE TABLE user_profile_daily (user_id INTEGER, dt VARCHAR, tier VARCHAR)")
    con.raw_sql(
        "INSERT INTO user_profile_daily VALUES "
        "(100, '20260701', 'old_gold'),"
        "(100, '20260705', 'new_gold')"
    )


def test_snapshot_latest_when_root_has_no_time_field(tmp_path, monkeypatch):
    """When root dataset has no time field, _derive_version_mode falls back to latest.

    With no timescope, the anchor is as_of_current_time.  We monkeypatch _utc_now
    to 2026-07-01 so the resolved partition is '20260701' (old_gold), not the later
    '20260705' (new_gold).
    """
    _bootstrap_snapshot_latest_no_root_time(tmp_path)
    monkeypatch.setattr(
        "marivo.analysis.intents.observe_planner._utc_now",
        lambda: dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.UTC),
    )
    con = ibis.duckdb.connect(":memory:")
    _seed_snapshot_latest_no_root_time(con)

    frame = observe(
        MetricRef("sales.revenue_by_profile"),
        dimensions=[DimensionRef("sales.user_profile_daily.tier")],
        session=_session(con),
    )

    df = frame.to_pandas()
    assert set(df["tier"].dropna().tolist()) == {"old_gold"}
    by_tier = df.set_index("tier")["revenue_by_profile"].to_dict()
    assert by_tier["old_gold"] == pytest.approx(30.0)

    versions = frame.meta.lineage.steps[0].params.get("version_resolutions") or []
    assert len(versions) >= 1
    snapshot_version = next(v for v in versions if v["dataset"] == "sales.user_profile_daily")
    assert snapshot_version["mode"] == "latest"
    assert snapshot_version["anchor_source"] == "as_of_current_time"
    assert snapshot_version["resolved_partition"] == "20260701"


def _bootstrap_validity(tmp_path, *, root_with_time: bool):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
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
    time_dimension = (
        "@ms.time_dimension(entity=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        if root_with_time
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "user_history = ms.entity(\n"
        "    name='user_history',\n"
        "    datasource='warehouse',\n"
        "    source=ms.table('user_history'),\n"
        "    primary_key=['user_id', 'valid_from'],\n"
        "    versioning=ms.validity(valid_from='sales.user_history.valid_from', valid_to='sales.user_history.valid_to', interval='closed_open', open_end=(None,)),\n"
        ")\n" + time_dimension + "@ms.dimension(entity=orders)\n"
        "def order_user_id(orders):\n"
        "    return orders.user_id\n"
        "@ms.dimension(entity=user_history)\n"
        "def user_id(user_history):\n"
        "    return user_history.user_id\n"
        "@ms.dimension(entity=user_history)\n"
        "def valid_from(user_history):\n"
        "    return user_history.valid_from\n"
        "@ms.dimension(entity=user_history)\n"
        "def valid_to(user_history):\n"
        "    return user_history.valid_to\n"
        "@ms.dimension(entity=user_history)\n"
        "def tier(user_history):\n"
        "    return user_history.tier\n"
        "@ms.metric(\n"
        "    entities=[orders, user_history],\n"
        "    root_entity=orders,\n"
        "    additivity='additive',\n"
        "    decomposition=ms.sum(),\n"
        "    name='revenue_by_tier',\n"
        "    verification_mode='python_native',\n"
        "    )\n"
        "def revenue_by_tier(orders, user_history):\n"
        "    return orders.amount.sum()\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.semantic as ms\n"
        "from .datasets import orders, user_history, order_user_id, user_id\n"
        "ms.relationship(\n"
        "    name='orders_to_history',\n"
        "    from_entity=orders,\n"
        "    to_entity=user_history,\n"
        "    from_dimensions=[order_user_id],\n"
        "    to_dimensions=[user_id],\n"
        ")\n"
    )


def _seed_validity(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 100),"
        "(2, DATE '2026-07-05', 20.0, 100),"
        "(3, DATE '2026-07-04', 5.0, 100)"
    )
    con.raw_sql(
        "CREATE TABLE user_history (user_id INTEGER, valid_from DATE, valid_to DATE, tier VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO user_history VALUES "
        "(100, DATE '2026-01-01', DATE '2026-07-04', 'silver'),"
        "(100, DATE '2026-07-04', NULL, 'gold')"
    )


def test_validity_latest_uses_open_end_predicate(tmp_path):
    _bootstrap_validity(tmp_path, root_with_time=False)
    con = ibis.duckdb.connect(":memory:")
    _seed_validity(con)
    frame = observe(
        MetricRef("sales.revenue_by_tier"),
        dimensions=[DimensionRef("sales.user_history.tier")],
        session=_session(con),
    )

    result = frame.to_pandas().set_index("tier")["revenue_by_tier"].to_dict()
    assert result == {"gold": pytest.approx(35.0)}

    versions = frame.meta.lineage.steps[0].params.get("version_resolutions") or []
    assert any(v["mode"] == "latest" and v["kind"] == "validity" for v in versions)


def test_validity_as_of_root_time_closed_open_boundary(tmp_path):
    _bootstrap_validity(tmp_path, root_with_time=True)
    con = ibis.duckdb.connect(":memory:")
    _seed_validity(con)
    frame = observe(
        MetricRef("sales.revenue_by_tier"),
        timescope={"start": "2026-07-01", "end": "2026-07-06"},
        dimensions=[DimensionRef("sales.user_history.tier")],
        session=_session(con),
    )

    by_tier = frame.to_pandas().set_index("tier")["revenue_by_tier"].to_dict()
    # 2026-07-01 falls in [2026-01-01, 2026-07-04) -> silver
    # 2026-07-04 is the boundary day: excluded from [2026-01-01, 2026-07-04) -> gold [2026-07-04, +inf)
    # 2026-07-05 falls in [2026-07-04, +inf)       -> gold
    assert by_tier == {"silver": pytest.approx(10.0), "gold": pytest.approx(25.0)}

    versions = frame.meta.lineage.steps[0].params.get("version_resolutions") or []
    validity_meta = next(v for v in versions if v["dataset"] == "sales.user_history")
    assert validity_meta["mode"] == "as_of_root_time"
    assert validity_meta["resolved_interval_predicate"] == "closed_open"

    warnings = frame.meta.lineage.steps[0].params.get("warnings") or []
    assert any(w.get("code") == "validity_overlap_unverified" for w in warnings)


def test_validity_as_of_root_time_closed_closed_boundary(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
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
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "user_history = ms.entity(\n"
        "    name='user_history',\n"
        "    datasource='warehouse',\n"
        "    source=ms.table('user_history'),\n"
        "    primary_key=['user_id', 'valid_from'],\n"
        "    versioning=ms.validity(valid_from='sales.user_history.valid_from', valid_to='sales.user_history.valid_to', interval='closed_closed', open_end=(None,)),\n"
        ")\n"
        "@ms.time_dimension(entity=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "@ms.dimension(entity=orders)\n"
        "def order_user_id(orders):\n"
        "    return orders.user_id\n"
        "@ms.dimension(entity=user_history)\n"
        "def user_id(user_history):\n"
        "    return user_history.user_id\n"
        "@ms.dimension(entity=user_history)\n"
        "def valid_from(user_history):\n"
        "    return user_history.valid_from\n"
        "@ms.dimension(entity=user_history)\n"
        "def valid_to(user_history):\n"
        "    return user_history.valid_to\n"
        "@ms.dimension(entity=user_history)\n"
        "def tier(user_history):\n"
        "    return user_history.tier\n"
        "@ms.metric(\n"
        "    entities=[orders, user_history],\n"
        "    root_entity=orders,\n"
        "    additivity='additive',\n"
        "    decomposition=ms.sum(),\n"
        "    name='revenue_by_tier',\n"
        "    verification_mode='python_native',\n"
        "    )\n"
        "def revenue_by_tier(orders, user_history):\n"
        "    return orders.amount.sum()\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.semantic as ms\n"
        "from .datasets import orders, user_history, order_user_id, user_id\n"
        "ms.relationship(\n"
        "    name='orders_to_history',\n"
        "    from_entity=orders,\n"
        "    to_entity=user_history,\n"
        "    from_dimensions=[order_user_id],\n"
        "    to_dimensions=[user_id],\n"
        ")\n"
    )

    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, user_id INTEGER)"
    )
    con.raw_sql("INSERT INTO orders VALUES (1, DATE '2026-07-04', 10.0, 100)")
    con.raw_sql(
        "CREATE TABLE user_history (user_id INTEGER, valid_from DATE, valid_to DATE, tier VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO user_history VALUES (100, DATE '2026-01-01', DATE '2026-07-04', 'silver')"
    )

    frame = observe(
        MetricRef("sales.revenue_by_tier"),
        timescope={"start": "2026-07-01", "end": "2026-07-06"},
        dimensions=[DimensionRef("sales.user_history.tier")],
        session=_session(con),
    )

    by_tier = frame.to_pandas().set_index("tier")["revenue_by_tier"].to_dict()
    # 2026-07-04 falls in [2026-01-01, 2026-07-04] -> silver (boundary-inclusive)
    assert by_tier == {"silver": pytest.approx(10.0)}


# ---------------------------------------------------------------------------
# Task 7: derived ratio + comparability tests
# ---------------------------------------------------------------------------


def _bootstrap_derived_ratio(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
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
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "sessions = ms.entity(name='sessions', datasource='warehouse', primary_key=['session_id'], source=ms.table('sessions'))\n"
        "users = ms.entity(name='users', datasource='warehouse', primary_key=['user_id'], source=ms.table('users'))\n"
        "@ms.dimension(entity=orders)\n"
        "def order_user_id(orders):\n"
        "    return orders.user_id\n"
        "@ms.dimension(entity=sessions)\n"
        "def session_user_id(sessions):\n"
        "    return sessions.user_id\n"
        "@ms.dimension(entity=users)\n"
        "def user_id(users):\n"
        "    return users.user_id\n"
        "@ms.dimension(entity=users)\n"
        "def country(users):\n"
        "    return users.country\n"
        "@ms.metric(entities=[orders, users], root_entity=orders, additivity='additive', decomposition=ms.sum(), name='gmv', verification_mode='python_native',)\n"
        "def gmv(orders, users):\n"
        "    return orders.amount.sum()\n"
        "@ms.metric(entities=[sessions, users], root_entity=sessions, additivity='additive', decomposition=ms.sum(), name='session_count', verification_mode='python_native',)\n"
        "def session_count(sessions, users):\n"
        "    return sessions.session_id.count()\n"
        "ms.derived_metric(\n"
        "    name='gmv_per_session',\n"
        "    decomposition=ms.ratio(numerator='sales.gmv', denominator='sales.session_count'),\n"
        ")\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.semantic as ms\n"
        "from .datasets import orders, sessions, users, order_user_id, session_user_id, user_id\n"
        "ms.relationship(name='orders_to_users', from_entity=orders, to_entity=users, from_dimensions=[order_user_id], to_dimensions=[user_id])\n"
        "ms.relationship(name='sessions_to_users', from_entity=sessions, to_entity=users, from_dimensions=[session_user_id], to_dimensions=[user_id])\n"
    )


def _seed_derived_ratio(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, user_id INTEGER, amount DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (1, 100, 30.0), (2, 200, 70.0)")
    con.raw_sql("CREATE TABLE sessions (session_id INTEGER, user_id INTEGER)")
    con.raw_sql("INSERT INTO sessions VALUES (1, 100), (2, 100), (3, 200)")
    con.raw_sql("CREATE TABLE users (user_id INTEGER, country VARCHAR)")
    con.raw_sql("INSERT INTO users VALUES (100, 'US'), (200, 'CA')")


def test_derived_ratio_multi_dataset_components_with_country_dimension(tmp_path):
    _bootstrap_derived_ratio(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_derived_ratio(con)
    frame = observe(
        MetricRef("sales.gmv_per_session"),
        dimensions=[DimensionRef("sales.users.country")],
        session=_session(con),
    )

    by_country = frame.to_pandas().set_index("country")["gmv_per_session"].to_dict()
    assert by_country["US"] == pytest.approx(15.0)  # 30 / 2
    assert by_country["CA"] == pytest.approx(70.0)  # 70 / 1


def _bootstrap_axis_unreachable(tmp_path):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
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
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "sessions = ms.entity(name='sessions', datasource='warehouse', primary_key=['session_id'], source=ms.table('sessions'))\n"
        "users = ms.entity(name='users', datasource='warehouse', primary_key=['user_id'], source=ms.table('users'))\n"
        "@ms.dimension(entity=orders)\n"
        "def order_user_id(orders):\n"
        "    return orders.user_id\n"
        "@ms.dimension(entity=users)\n"
        "def user_id(users):\n"
        "    return users.user_id\n"
        "@ms.dimension(entity=users)\n"
        "def country(users):\n"
        "    return users.country\n"
        "@ms.metric(entities=[orders, users], root_entity=orders, additivity='additive', decomposition=ms.sum(), name='gmv', verification_mode='python_native',)\n"
        "def gmv(orders, users):\n"
        "    return orders.amount.sum()\n"
        "@ms.metric(entities=[sessions], additivity='additive', decomposition=ms.sum(), name='session_count', verification_mode='python_native',)\n"
        "def session_count(sessions):\n"
        "    return sessions.session_id.count()\n"
        "ms.derived_metric(\n"
        "    name='gmv_per_session',\n"
        "    decomposition=ms.ratio(numerator='sales.gmv', denominator='sales.session_count'),\n"
        ")\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.semantic as ms\n"
        "from .datasets import orders, users, order_user_id, user_id\n"
        "ms.relationship(name='orders_to_users', from_entity=orders, to_entity=users, from_dimensions=[order_user_id], to_dimensions=[user_id])\n"
    )


def test_component_axis_unreachable_raises(tmp_path):
    _bootstrap_axis_unreachable(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, user_id INTEGER, amount DOUBLE)")
    con.raw_sql("CREATE TABLE sessions (session_id INTEGER)")
    con.raw_sql("CREATE TABLE users (user_id INTEGER, country VARCHAR)")

    with pytest.raises(ObservePlanningError) as exc_info:
        observe(
            MetricRef("sales.gmv_per_session"),
            dimensions=[DimensionRef("sales.users.country")],
            session=_session(con),
        )

    details = exc_info.value.details
    assert details["code"] == "component-axis-unreachable"
    assert "sales.session_count" in details["candidates"]["missing_components"]
    assert any(c["metric"] == "sales.gmv" for c in details["candidates"]["resolved_components"])


def test_component_filter_unreachable_raises(tmp_path):
    _bootstrap_axis_unreachable(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, user_id INTEGER, amount DOUBLE)")
    con.raw_sql("CREATE TABLE sessions (session_id INTEGER)")
    con.raw_sql("CREATE TABLE users (user_id INTEGER, country VARCHAR)")

    with pytest.raises(ObservePlanningError) as exc_info:
        observe(
            MetricRef("sales.gmv_per_session"),
            where={DimensionRef("sales.country"): "US"},
            session=_session(con),
        )
    details = exc_info.value.details
    assert details["code"] == "component-filter-unreachable"
    assert details["candidates"]["filter_key"] == "sales.country"
    assert "sales.session_count" in details["candidates"]["missing_components"]


def test_component_version_mismatch_raises_on_mode_difference(tmp_path):
    # Build a metric where numerator uses as_of_root_time against
    # user_profile_daily and denominator (sessions, no time field) falls back
    # to latest. The mode difference should raise component-version-mismatch.
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
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
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "sessions = ms.entity(name='sessions', datasource='warehouse', primary_key=['session_id'], source=ms.table('sessions'))\n"
        "user_profile_daily = ms.entity(\n"
        "    name='user_profile_daily',\n"
        "    datasource='warehouse',\n"
        "    source=ms.table('user_profile_daily'),\n"
        "    primary_key=['user_id', 'dt'],\n"
        "    versioning=ms.snapshot(partition_field='dt', grain='day', timezone='UTC', format='%Y%m%d'),\n"
        ")\n"
        "@ms.time_dimension(entity=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "@ms.dimension(entity=orders)\n"
        "def order_user_id(orders):\n"
        "    return orders.user_id\n"
        "@ms.dimension(entity=sessions)\n"
        "def session_user_id(sessions):\n"
        "    return sessions.user_id\n"
        "@ms.dimension(entity=user_profile_daily)\n"
        "def profile_user_id(user_profile_daily):\n"
        "    return user_profile_daily.user_id\n"
        "@ms.dimension(entity=user_profile_daily)\n"
        "def dt(user_profile_daily):\n"
        "    return user_profile_daily.dt\n"
        "@ms.dimension(entity=user_profile_daily)\n"
        "def tier(user_profile_daily):\n"
        "    return user_profile_daily.tier\n"
        "@ms.metric(entities=[orders, user_profile_daily], root_entity=orders, additivity='additive', decomposition=ms.sum(), name='gmv_by_tier', verification_mode='python_native',)\n"
        "def gmv_by_tier(orders, user_profile_daily):\n"
        "    return orders.amount.sum()\n"
        "@ms.metric(entities=[sessions, user_profile_daily], root_entity=sessions, additivity='additive', decomposition=ms.sum(), name='sessions_by_tier', verification_mode='python_native',)\n"
        "def sessions_by_tier(sessions, user_profile_daily):\n"
        "    return sessions.session_id.count()\n"
        "ms.derived_metric(\n"
        "    name='gmv_per_session',\n"
        "    decomposition=ms.ratio(numerator='sales.gmv_by_tier', denominator='sales.sessions_by_tier'),\n"
        ")\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.semantic as ms\n"
        "from .datasets import orders, sessions, user_profile_daily, order_user_id, session_user_id, profile_user_id\n"
        "ms.relationship(name='orders_to_profile', from_entity=orders, to_entity=user_profile_daily, from_dimensions=[order_user_id], to_dimensions=[profile_user_id])\n"
        "ms.relationship(name='sessions_to_profile', from_entity=sessions, to_entity=user_profile_daily, from_dimensions=[session_user_id], to_dimensions=[profile_user_id])\n"
    )

    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, user_id INTEGER)"
    )
    con.raw_sql("INSERT INTO orders VALUES (1, DATE '2026-07-01', 30.0, 100)")
    con.raw_sql("CREATE TABLE sessions (session_id INTEGER, user_id INTEGER)")
    con.raw_sql("INSERT INTO sessions VALUES (1, 100)")
    con.raw_sql("CREATE TABLE user_profile_daily (user_id INTEGER, dt VARCHAR, tier VARCHAR)")
    con.raw_sql("INSERT INTO user_profile_daily VALUES (100, '20260701', 'gold')")

    with pytest.raises(ObservePlanningError) as exc_info:
        observe(
            MetricRef("sales.gmv_per_session"),
            timescope={"start": "2026-07-01", "end": "2026-07-05"},
            dimensions=[DimensionRef("sales.user_profile_daily.tier")],
            session=_session(con),
        )

    details = exc_info.value.details
    assert details["code"] == "component-version-mismatch"
    assert details["candidates"]["versioned_dataset"] == "sales.user_profile_daily"


def test_derived_observe_populates_known_datasources(tmp_path):
    """Regression: known_datasources must be updated for each component datasource."""
    _bootstrap_derived_ratio(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_derived_ratio(con)
    session = _session(con)
    observe(MetricRef("sales.gmv_per_session"), session=session)
    # Both component metrics (gmv -> orders/users, session_count -> sessions/users)
    # share the same "warehouse" datasource in this fixture.
    assert "warehouse" in session.known_datasources


def test_derived_components_can_span_datasources(tmp_path):
    """Cross-datasource derived metric: gmv on warehouse, session_count on analytics."""
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(warehouse)\n"
    )
    (datasource_dir / "analytics.py").write_text(
        "import marivo.datasource as md\n"
        "analytics = md.DatasourceSpec(name='analytics', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(analytics)\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "sessions = ms.entity(name='sessions', datasource='analytics', primary_key=['session_id'], source=ms.table('sessions'))\n"
        "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='gmv', verification_mode='python_native',)\n"
        "def gmv(orders):\n"
        "    return orders.amount.sum()\n"
        "@ms.metric(entities=[sessions], additivity='additive', decomposition=ms.sum(), name='session_count', verification_mode='python_native',)\n"
        "def session_count(sessions):\n"
        "    return sessions.session_id.count()\n"
        "ms.derived_metric(\n"
        "    name='gmv_per_session',\n"
        "    decomposition=ms.ratio(numerator=gmv, denominator=session_count),\n"
        ")\n"
    )
    warehouse = ibis.duckdb.connect(":memory:")
    warehouse.raw_sql("CREATE TABLE orders (order_id INTEGER, amount DOUBLE)")
    warehouse.raw_sql("INSERT INTO orders VALUES (1, 30.0), (2, 70.0)")
    analytics = ibis.duckdb.connect(":memory:")
    analytics.raw_sql("CREATE TABLE sessions (session_id INTEGER)")
    analytics.raw_sql("INSERT INTO sessions VALUES (1), (2), (3), (4)")
    session = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: warehouse, "analytics": lambda: analytics},
    )
    frame = observe(MetricRef("sales.gmv_per_session"), session=session)
    assert frame.to_pandas().iloc[0, 0] == pytest.approx(25.0)  # 100 / 4
    component_datasources = frame.meta.lineage.steps[0].params["lineage_metadata"][
        "component_datasources"
    ]
    assert {"warehouse", "analytics"} == {ds for _cid, ds in component_datasources}
