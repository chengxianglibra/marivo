"""Phase 1 base observe planner tests."""

from __future__ import annotations

import pytest

from marivo.analysis.intents.observe_errors import (
    ObservePlanningError,
    RepairAction,
    RepairSafety,
    raise_observe_planning_error,
)
from marivo.analysis.intents.observe_planner import (
    JoinSafety,
    resolve_metric_root,
    resolve_observe_fields,
    resolved_edge_safety,
    unique_shortest_relationship_path,
)
from marivo.analysis.refs import DimensionRef


def test_observe_planning_error_payload_is_stable():
    with pytest.raises(ObservePlanningError) as exc_info:
        raise_observe_planning_error(
            code="missing-root",
            message="Multi-dataset base metric 'sales.revenue' must declare root_dataset.",
            candidates={"datasets": ["sales.orders", "sales.users"]},
            repair=[
                RepairAction(
                    action="set_metric_root",
                    target="sales.revenue",
                    arg="root_dataset",
                    value="sales.orders",
                    safety=RepairSafety.MODELING_DECISION,
                    why="the root defines preserved rows and the observe time axis",
                )
            ],
        )

    error = exc_info.value
    assert error.details["schema_version"] == "observe-error/v1"
    assert error.details["code"] == "missing-root"
    assert error.details["candidates"] == {"datasets": ["sales.orders", "sales.users"]}
    assert error.details["repair"] == [
        {
            "action": "set_metric_root",
            "target": "sales.revenue",
            "arg": "root_dataset",
            "value": "sales.orders",
            "safety": "modeling_decision",
            "why": "the root defines preserved rows and the observe time axis",
        }
    ]


def test_resolve_metric_root_defaults_single_dataset(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native',)\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    metric = project.get_metric("sales.revenue")
    assert metric is not None
    assert resolve_metric_root(metric) == "sales.orders"


def test_short_field_resolution_is_limited_to_metric_datasets(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
                "users = ms.entity(name='users', datasource='warehouse', primary_key=['user_id'], source=ms.table('users'))\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
                "@ms.dimension(entity=users)\n"
                "def tier(users):\n"
                "    return users.tier\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native',)\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    metric = project.get_metric("sales.revenue")
    assert metric is not None

    resolved = resolve_observe_fields(
        project, metric, dimensions=[DimensionRef("region")], where=None, time_dimension=None
    )
    assert [field.semantic_id for field in resolved.dimensions] == ["sales.orders.region"]

    with pytest.raises(ObservePlanningError) as exc_info:
        resolve_observe_fields(
            project, metric, dimensions=[DimensionRef("tier")], where=None, time_dimension=None
        )
    assert exc_info.value.details["code"] == "field-ref-not-found"
    assert "tier" in exc_info.value.details["candidates"].get("did_you_mean", [])


def test_field_ref_not_found_populates_did_you_mean_and_repair(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
                "users = ms.entity(name='users', datasource='warehouse', primary_key=['user_id'], source=ms.table('users'))\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
                "@ms.dimension(entity=users)\n"
                "def tier(users):\n"
                "    return users.tier\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native',)\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    metric = project.get_metric("sales.revenue")
    assert metric is not None

    with pytest.raises(ObservePlanningError) as exc_info:
        resolve_observe_fields(
            project, metric, dimensions=[DimensionRef("regn")], where=None, time_dimension=None
        )
    details = exc_info.value.details
    assert details["code"] == "field-ref-not-found"
    assert "region" in details["candidates"].get("did_you_mean", [])
    assert isinstance(details["candidates"].get("available_field_ids"), list)
    repair = details.get("repair", [])
    assert len(repair) >= 1
    assert repair[0]["action"] == "replace_field_ref"
    assert repair[0]["value"] == "region"

    resolved = resolve_observe_fields(
        project,
        metric,
        dimensions=[DimensionRef("sales.users.tier")],
        where=None,
        time_dimension=None,
    )
    assert [field.semantic_id for field in resolved.dimensions] == ["sales.users.tier"]


def test_unique_shortest_path_and_join_safety(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
                "users = ms.entity(name='users', datasource='warehouse', primary_key=['user_id'], source=ms.table('users'))\n"
                "@ms.dimension(entity=orders)\n"
                "def order_user_id(orders):\n"
                "    return orders.user_id\n"
                "@ms.dimension(entity=users)\n"
                "def user_id(users):\n"
                "    return users.user_id\n"
                "@ms.metric(entities=[orders, users], root_entity=orders, additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native',)\n"
                "def revenue(orders, users):\n"
                "    return orders.amount.sum()\n"
            ),
            "sales/relationships.py": (
                "import marivo.semantic as ms\n"
                "from .datasets import orders, users, order_user_id, user_id\n"
                "ms.relationship(\n"
                "    name='orders_to_users',\n"
                "    from_entity=orders,\n"
                "    to_entity=users,\n"
                "    from_dimensions=[order_user_id],\n"
                "    to_dimensions=[user_id],\n"
                ")\n"
            ),
        }
    )

    path = unique_shortest_relationship_path(project, "sales.orders", "sales.users")
    assert [rel.semantic_id for rel in path] == ["sales.orders_to_users"]
    assert (
        resolved_edge_safety(project, path[0], from_entity="sales.orders") == JoinSafety.MANY_TO_ONE
    )
    assert (
        resolved_edge_safety(project, path[0], from_entity="sales.users") == JoinSafety.ONE_TO_MANY
    )
