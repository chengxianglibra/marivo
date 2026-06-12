"""Phase 3: aggregate_then_join planner branch and repair contract."""

from __future__ import annotations

from pathlib import Path

import ibis
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.intents.observe import observe
from marivo.analysis.intents.observe_errors import (
    ObservePlanningError,
    RepairSafety,
)
from marivo.semantic.catalog import SemanticKind, SemanticRef


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _bootstrap_one_to_many(tmp_path: Path, *, fanout_policy: str = "block") -> None:
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / ".marivo" / "datasource"
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
        "def qty(order_items):\n"
        "    return order_items.qty\n"
        f"@ms.metric(\n"
        "    entities=[orders, order_items],\n"
        "    root_entity=orders,\n"
        "    additivity='additive',\n"
        "    decomposition=ms.sum(),\n"
        f"    fanout_policy='{fanout_policy}',\n"
        "    name='gmv_with_items',\n"
        "    verification_mode='python_native',\n"
        "    )\n"
        "def gmv_with_items(orders, order_items):\n"
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
    con.raw_sql("CREATE TABLE order_items (item_id INTEGER, order_id INTEGER, qty INTEGER)")
    con.raw_sql("INSERT INTO order_items VALUES (1, 1, 2), (2, 1, 1), (3, 2, 5), (4, 3, 3)")


def _session(con):
    return session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})


def test_unsafe_fanout_repair_payload_lists_root_then_policy(tmp_path):
    _bootstrap_one_to_many(tmp_path, fanout_policy="block")
    con = ibis.duckdb.connect(":memory:")
    _seed(con)

    with pytest.raises(ObservePlanningError) as exc_info:
        observe(
            SemanticRef("sales.gmv_with_items", kind=SemanticKind.METRIC),
            dimensions=[SemanticRef("sales.order_items.qty", kind=SemanticKind.DIMENSION)],
            session=_session(con),
        )

    details = exc_info.value.details
    assert details["code"] == "unsafe-fanout"
    repair = details["repair"]
    assert [r["action"] for r in repair] == ["set_metric_root", "set_fanout_policy"]
    assert all(r["safety"] == RepairSafety.MODELING_DECISION.value for r in repair)
    candidates = details["candidates"]
    assert "safe_roots" in candidates and "fanout_policies" in candidates
    assert candidates["fanout_policies"] == ["aggregate_then_join"]


def test_aggregate_then_join_executes_one_to_many(tmp_path):
    _bootstrap_one_to_many(tmp_path, fanout_policy="aggregate_then_join")
    con = ibis.duckdb.connect(":memory:")
    _seed(con)

    frame = observe(
        SemanticRef("sales.gmv_with_items", kind=SemanticKind.METRIC),
        dimensions=[SemanticRef("sales.order_items.qty", kind=SemanticKind.DIMENSION)],
        session=_session(con),
    )

    df = frame.to_pandas()
    # Hand-rolled expected: each (order, qty) pair contributes one order amount.
    # order 1 (10): items qty=2, qty=1
    # order 2 (20): qty=5
    # order 3 (30): qty=3
    # Group by qty: qty=1 -> 10, qty=2 -> 10, qty=3 -> 30, qty=5 -> 20
    expected = {1: 10.0, 2: 10.0, 3: 30.0, 5: 20.0}
    assert dict(zip(df["qty"], df["gmv_with_items"], strict=True)) == expected


def test_aggregate_then_join_records_lineage(tmp_path):
    _bootstrap_one_to_many(tmp_path, fanout_policy="aggregate_then_join")
    con = ibis.duckdb.connect(":memory:")
    _seed(con)

    frame = observe(
        SemanticRef("sales.gmv_with_items", kind=SemanticKind.METRIC),
        dimensions=[SemanticRef("sales.order_items.qty", kind=SemanticKind.DIMENSION)],
        session=_session(con),
    )
    params = frame.meta.lineage.steps[0].params
    assert params["fanout_policy"] == "aggregate_then_join"
    fanouts = params["fanouts"]
    assert len(fanouts) == 1
    assert fanouts[0]["policy"] == "aggregate_then_join"
    assert fanouts[0]["unsafe_dataset"] == "sales.order_items"
    grain_columns = {col["name"] for col in fanouts[0]["merge_grain"]}
    assert "order_id" in grain_columns
    assert "qty" in grain_columns


def test_observe_does_not_accept_fanout_policy_kwarg(tmp_path):
    _bootstrap_one_to_many(tmp_path, fanout_policy="block")
    con = ibis.duckdb.connect(":memory:")
    _seed(con)

    with pytest.raises(TypeError):
        observe(
            SemanticRef("sales.gmv_with_items", kind=SemanticKind.METRIC),
            dimensions=[SemanticRef("sales.order_items.qty", kind=SemanticKind.DIMENSION)],
            session=_session(con),
            fanout_policy="aggregate_then_join",  # type: ignore[call-arg]
        )


def test_relationship_does_not_accept_fanout_kwargs():
    import marivo.semantic as ms

    with pytest.raises(TypeError):
        ms.relationship(  # type: ignore[call-arg]
            name="orders_to_order_items",
            from_entity="sales.orders",
            to_entity="sales.order_items",
            from_dimensions=[],
            to_dimensions=[],
            fanout_policy="aggregate_then_join",
        )


def test_unsafe_fanout_error_payload_schema_version():
    from marivo.analysis.intents.observe_errors import (
        ObservePlanningError,
        RepairAction,
        RepairSafety,
        raise_observe_planning_error,
    )

    with pytest.raises(ObservePlanningError) as exc_info:
        raise_observe_planning_error(
            code="unsafe-fanout",
            message="x",
            candidates={"safe_roots": ["a"], "fanout_policies": ["aggregate_then_join"]},
            repair=[
                RepairAction(
                    action="set_metric_root",
                    target="m",
                    arg="root_dataset",
                    value="a",
                    safety=RepairSafety.MODELING_DECISION,
                    why="why-1",
                ),
                RepairAction(
                    action="set_fanout_policy",
                    target="m",
                    arg="fanout_policy",
                    value="aggregate_then_join",
                    safety=RepairSafety.MODELING_DECISION,
                    why="why-2",
                ),
            ],
        )
    details = exc_info.value.details
    assert details["schema_version"] == "observe-error/v1"
    assert details["code"] == "unsafe-fanout"
    assert [r["action"] for r in details["repair"]] == [
        "set_metric_root",
        "set_fanout_policy",
    ]
