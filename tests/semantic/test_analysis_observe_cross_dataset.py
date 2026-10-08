"""Ontology reverse index retains all governed computation entities."""

from pathlib import Path

import marivo.analysis as mv
from marivo.semantic.catalog import SemanticKind
from tests.support.refs import make_ref


def _bootstrap(tmp_path: Path) -> None:
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
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), primary_key=['order_id'], source=md.table('orders'))\n"
        "order_items = ms.entity(name='order_items', datasource=ms.ref.datasource('warehouse'), primary_key=['item_id'], source=md.table('order_items'))\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "order_id = ms.dimension_column(name='order_id', entity=orders, column='order_id')\n"
        "item_order_id = ms.dimension_column(name='item_order_id', entity=order_items, column='order_id')\n"
        "@ms.dimension(entity=order_items)\n"
        "def category(order_items):\n"
        "    return order_items.category\n"
        "@ms.metric(\n"
        "    entities=[orders, order_items],\n"
        "    root_entity=orders,\n"
        "    additivity=ms.additive_all(),\n"
        "    fanout_policy='aggregate_then_join',\n"
        "    name='gmv_by_category',\n"
        "    )\n"
        "def gmv_by_category(orders, order_items):\n"
        "    return orders.amount.sum()\n"
    )
    (semantic_dir / "relationships.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "from .datasets import orders, order_items, order_id, item_order_id\n"
        "ms.relationship(\n"
        "    name='orders_to_order_items',\n"
        "    from_entity=orders,\n"
        "    to_entity=order_items,\n"
        "    keys=[ms.join_on(order_id, item_order_id)],\n"
        ")\n"
    )


def test_ontology_reverse_index_includes_every_effective_entity(tmp_path, monkeypatch):
    _bootstrap(tmp_path)
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("ontology")

    metric_refs = session.catalog._ontology_metrics_for_endpoint(
        make_ref("sales.order_items", SemanticKind.ENTITY)
    )

    assert metric_refs == (make_ref("sales.gmv_by_category", SemanticKind.METRIC),)
