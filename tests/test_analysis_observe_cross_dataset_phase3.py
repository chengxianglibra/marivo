"""Phase 3: aggregate_then_join end-to-end through observe()."""

from __future__ import annotations

from pathlib import Path

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


def _bootstrap(tmp_path: Path) -> None:
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
        "orders = ms.dataset(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "order_items = ms.dataset(name='order_items', datasource='warehouse', primary_key=['item_id'], source=ms.table('order_items'))\n"
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "@ms.field(dataset=orders)\n"
        "def order_id(orders):\n"
        "    return orders.order_id\n"
        "@ms.field(dataset=order_items)\n"
        "def item_order_id(order_items):\n"
        "    return order_items.order_id\n"
        "@ms.field(dataset=order_items)\n"
        "def category(order_items):\n"
        "    return order_items.category\n"
        "@ms.metric(\n"
        "    datasets=[orders, order_items],\n"
        "    root_dataset=orders,\n"
        "    additivity='additive',\n"
        "    decomposition=ms.sum(),\n"
        "    fanout_policy='aggregate_then_join',\n"
        "    name='gmv_by_category',\n"
        "    verification_mode='python_native',\n"
        "    )\n"
        "def gmv_by_category(orders, order_items):\n"
        "    return orders.amount.sum()\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.semantic as ms\n"
        "from .datasets import orders, order_items, order_id, item_order_id\n"
        "ms.relationship(\n"
        "    name='orders_to_order_items',\n"
        "    from_dataset=orders,\n"
        "    to_dataset=order_items,\n"
        "    from_fields=[order_id],\n"
        "    to_fields=[item_order_id],\n"
        ")\n"
    )


def _seed(con):
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, user_id INTEGER, channel VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 100, 'web'),"
        "(2, DATE '2026-07-02', 20.0, 100, 'app'),"
        "(3, DATE '2026-07-03', 30.0, 200, 'web')"
    )
    con.raw_sql("CREATE TABLE order_items (item_id INTEGER, order_id INTEGER, category VARCHAR)")
    con.raw_sql(
        "INSERT INTO order_items VALUES "
        "(1, 1, 'shirt'), (2, 1, 'pants'), (3, 2, 'shirt'), (4, 3, 'pants')"
    )


def _session(con):
    return session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})


def test_segmented_observe_aggregate_then_join(tmp_path):
    _bootstrap(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)

    frame = observe(
        MetricRef("sales.gmv_by_category"),
        dimensions=[DimensionRef("sales.category")],
        session=_session(con),
    )
    df = frame.to_pandas().set_index("category")
    # order 1 (10): shirt + pants -> shirt=10, pants=10
    # order 2 (20): shirt -> shirt += 20 -> shirt=30
    # order 3 (30): pants -> pants += 30 -> pants=40
    assert df.loc["shirt", "gmv_by_category"] == 30.0
    assert df.loc["pants", "gmv_by_category"] == 40.0


def test_panel_observe_aggregate_then_join(tmp_path):
    _bootstrap(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)

    frame = observe(
        MetricRef("sales.gmv_by_category"),
        timescope={"start": "2026-07-01", "end": "2026-07-05"},
        grain="day",
        dimensions=[DimensionRef("sales.category")],
        session=_session(con),
    )
    df = frame.to_pandas()
    assert frame.meta.semantic_kind == "panel"
    # Spot-check: order 1 on 2026-07-01 contributes 10 to both shirt and pants.
    row = df[(df["bucket_start"].astype(str) == "2026-07-01") & (df["category"] == "shirt")]
    assert float(row["gmv_by_category"].iloc[0]) == 10.0
