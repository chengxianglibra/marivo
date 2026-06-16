"""Phase 1 base observe planner tests."""

from __future__ import annotations

from types import SimpleNamespace

import ibis
import pytest

from marivo.analysis.intents.observe_errors import (
    ObservePlanningError,
    RepairAction,
    RepairSafety,
    raise_observe_planning_error,
)
from marivo.analysis.intents.observe_planner import (
    JoinSafety,
    _field_fn,
    plan_base_observe,
    resolve_metric_root,
    resolve_observe_fields,
    resolved_edge_safety,
    unique_shortest_relationship_path,
)
from marivo.semantic._registry_bridge import get_metric_ir
from marivo.semantic.catalog import SemanticCatalog, SemanticKind, SemanticRef
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError


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


def test_plan_base_observe_rejects_legacy_project_kwarg():
    metric = SimpleNamespace(
        semantic_id="sales.revenue",
        entities=("sales.orders",),
        root_entity=None,
        additivity=None,
    )
    with pytest.raises(TypeError, match="unexpected keyword argument 'project'"):
        plan_base_observe(
            project=object(),
            catalog=object(),
            session=object(),
            metric_ir=metric,
            dataset_irs={},
            dataset_fns={},
            dimensions=None,
            where=None,
            resolved_window=None,
            time_dimension=None,
        )


def test_resolve_metric_root_defaults_single_dataset(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    metric = get_metric_ir(project, "sales.revenue")
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
                "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    metric = get_metric_ir(project, "sales.revenue")
    assert metric is not None
    catalog = SemanticCatalog(project)

    resolved = resolve_observe_fields(
        catalog,
        metric,
        dimensions=[SemanticRef("region", kind=SemanticKind.DIMENSION)],
        where=None,
        time_dimension=None,
    )
    assert [field.semantic_id for field in resolved.dimensions] == ["sales.orders.region"]

    with pytest.raises(ObservePlanningError) as exc_info:
        resolve_observe_fields(
            catalog,
            metric,
            dimensions=[SemanticRef("tier", kind=SemanticKind.DIMENSION)],
            where=None,
            time_dimension=None,
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
                "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    metric = get_metric_ir(project, "sales.revenue")
    assert metric is not None
    catalog = SemanticCatalog(project)

    with pytest.raises(ObservePlanningError) as exc_info:
        resolve_observe_fields(
            catalog,
            metric,
            dimensions=[SemanticRef("regn", kind=SemanticKind.DIMENSION)],
            where=None,
            time_dimension=None,
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
        catalog,
        metric,
        dimensions=[SemanticRef("sales.users.tier", kind=SemanticKind.DIMENSION)],
        where=None,
        time_dimension=None,
    )
    assert [field.semantic_id for field in resolved.dimensions] == ["sales.users.tier"]


def test_field_ref_not_found_adds_ibis_hint_for_builtin_names(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
                "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    metric = get_metric_ir(project, "sales.revenue")
    catalog = SemanticCatalog(project)

    with pytest.raises(ObservePlanningError) as exc_info:
        resolve_observe_fields(
            catalog,
            metric,
            dimensions=[SemanticRef("desc", kind=SemanticKind.DIMENSION)],
            where=None,
            time_dimension=None,
        )
    details = exc_info.value.details
    assert details["code"] == "field-ref-not-found"
    assert "ibis_builtin_hint" in details["candidates"]
    assert "ibis.desc()" in details["candidates"]["ibis_builtin_hint"]
    assert "ibis.desc()" in exc_info.value.message


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
                "@ms.metric(entities=[orders, users], root_entity=orders, additivity='additive', name='revenue', )\n"
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
                "    keys=[ms.join_on(order_user_id, user_id)],\n"
                ")\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    path = unique_shortest_relationship_path(catalog, "sales.orders", "sales.users")
    assert [rel.semantic_id for rel in path] == ["sales.orders_to_users"]
    assert (
        resolved_edge_safety(catalog, path[0], from_entity="sales.orders") == JoinSafety.MANY_TO_ONE
    )
    assert (
        resolved_edge_safety(catalog, path[0], from_entity="sales.users") == JoinSafety.ONE_TO_MANY
    )


def test_unique_shortest_path_finds_cross_domain_relationship_from_non_owner(
    semantic_project_factory,
):
    project = semantic_project_factory(
        {
            "sales/_domain.py": ("import marivo.semantic as ms\nsales = ms.domain(name='sales')\n"),
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "from ._domain import sales\n"
                "identity = ms.domain(name='identity')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'), domain=sales)\n"
                "users = ms.entity(name='users', datasource='warehouse', primary_key=['user_id'], source=ms.table('users'), domain=identity)\n"
                "@ms.dimension(entity=orders, domain=sales)\n"
                "def order_user_id(orders):\n"
                "    return orders.user_id\n"
                "@ms.dimension(entity=users, domain=identity)\n"
                "def user_id(users):\n"
                "    return users.user_id\n"
            ),
            "sales/relationships.py": (
                "import marivo.semantic as ms\n"
                "from ._domain import sales\n"
                "from .datasets import orders, users, order_user_id, user_id\n"
                "ms.relationship(\n"
                "    name='orders_to_identity_users',\n"
                "    from_entity=orders,\n"
                "    to_entity=users,\n"
                "    keys=[ms.join_on(order_user_id, user_id)],\n"
                "    domain=sales,\n"
                ")\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    path = unique_shortest_relationship_path(catalog, "identity.users", "sales.orders")

    assert [rel.semantic_id for rel in path] == ["sales.orders_to_identity_users"]


def test_field_fn_invalid_expression_preserves_observe_error_code(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
                "@ms.dimension(entity=orders)\n"
                "def bad_dimension(orders):\n"
                "    return 42\n"
            ),
        }
    )
    table = ibis.table({"order_id": "int64"}, name="orders")
    catalog = SemanticCatalog(project)

    with pytest.raises(ObservePlanningError) as exc_info:
        _field_fn(catalog, "sales.orders.bad_dimension")(table)

    assert exc_info.value.details["code"] == "field-expr-type-error"


def test_field_fn_converts_typed_missing_dimension_kind(monkeypatch, semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
            ),
        }
    )
    table = ibis.table({"order_id": "int64"}, name="orders")
    catalog = SemanticCatalog(project)

    class MissingDimensionResolver:
        def dimension_on(self, semantic_id, _table):
            raise SemanticRuntimeError(
                kind=ErrorKind.DIMENSION_NOT_FOUND,
                message="dimension lookup missed registry entry",
                refs=(semantic_id,),
            )

    monkeypatch.setattr(
        SemanticCatalog,
        "_resolver",
        lambda self, connections=None: MissingDimensionResolver(),
    )

    with pytest.raises(ObservePlanningError) as exc_info:
        _field_fn(catalog, "sales.orders.missing")(table)

    assert exc_info.value.details["code"] == "field-ref-not-found"


def test_field_fn_does_not_misclassify_callable_not_found_failures(
    semantic_project_factory,
):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
                "@ms.dimension(entity=orders)\n"
                "def warehouse_status(orders):\n"
                "    return (_ for _ in ()).throw(\n"
                "        RuntimeError('warehouse file not found during expression build')\n"
                "    )\n"
            ),
        }
    )
    table = ibis.table({"order_id": "int64"}, name="orders")
    catalog = SemanticCatalog(project)

    with pytest.raises(Exception) as exc_info:
        _field_fn(catalog, "sales.orders.warehouse_status")(table)

    exc = exc_info.value
    if isinstance(exc, ObservePlanningError):
        assert exc.details["code"] != "field-ref-not-found"
    else:
        assert isinstance(exc, SemanticRuntimeError)
        assert exc.kind == ErrorKind.MATERIALIZE_FAILED
        assert "warehouse file not found during expression build" in str(exc)
