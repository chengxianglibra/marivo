"""Phase 1 cross-dataset observe semantic contracts."""

from __future__ import annotations

import pytest

from marivo.semantic.catalog import EntityDetails, MetricDetails, SemanticCatalog
from marivo.semantic.errors import SemanticLoadFailed


def test_base_metric_requires_additivity(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), primary_key=['order_id'], source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], name='revenue', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        },
    )

    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).get("metric.sales.revenue")

    error = exc_info.value.errors[0]
    assert error.kind == "organization_error"


def test_single_dataset_metric_defaults_root_dataset(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), primary_key=['order_id'], source=ms.table('orders'))\n"
                "@ms.metric(\n"
                "    entities=[orders],\n"
                "    additivity='additive',\n"
                "    name='revenue',\n"
                "    )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )

    metric = SemanticCatalog(project).get("metric.sales.revenue").details()
    assert isinstance(metric, MetricDetails)
    assert metric.additivity == "additive"
    assert metric.root_entity is not None
    assert metric.root_entity.id == "sales.orders"


def test_multi_dataset_metric_requires_explicit_root_dataset(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), primary_key=['order_id'], source=ms.table('orders'))\n"
                "users = ms.entity(name='users', datasource=md.ref('datasource.warehouse'), primary_key=['user_id'], source=ms.table('users'))\n"
                "@ms.metric(\n"
                "    entities=[orders, users],\n"
                "    additivity='additive',\n"
                "    name='revenue',\n"
                "    )\n"
                "def revenue(orders, users):\n"
                "    return orders.amount.sum()\n"
            ),
        },
    )

    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).get("metric.sales.revenue")

    error = exc_info.value.errors[0]
    assert error.kind == "missing_metric_root_entity"
    assert error.constraint_id == "metric_root_entity_required"


def test_multi_dataset_metric_accepts_root_dataset_ref(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), primary_key=['order_id'], source=ms.table('orders'))\n"
                "users = ms.entity(name='users', datasource=md.ref('datasource.warehouse'), primary_key=['user_id'], source=ms.table('users'))\n"
                "@ms.metric(\n"
                "    entities=[orders, users],\n"
                "    root_entity=orders,\n"
                "    additivity='additive',\n"
                "    name='revenue',\n"
                "    )\n"
                "def revenue(orders, users):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )

    metric = SemanticCatalog(project).get("metric.sales.revenue").details()
    assert isinstance(metric, MetricDetails)
    assert metric.root_entity is not None
    assert metric.root_entity.id == "sales.orders"


def test_multi_dataset_metric_rejects_non_root_aggregate_receiver(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), primary_key=['order_id'], source=ms.table('orders'))\n"
                "users = ms.entity(name='users', datasource=md.ref('datasource.warehouse'), primary_key=['user_id'], source=ms.table('users'))\n"
                "@ms.metric(\n"
                "    entities=[orders, users],\n"
                "    root_entity=orders,\n"
                "    additivity='additive',\n"
                "    name='bad_user_sum',\n"
                "    )\n"
                "def bad_user_sum(orders, users):\n"
                "    return users.score.sum()\n"
            ),
        },
        load=False,
    )
    project.load()

    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).get("metric.sales.bad_user_sum")

    error = exc_info.value.errors[0]
    assert error.kind == "non_root_metric_aggregate"
    assert error.details["metric"] == "sales.bad_user_sum"
    assert error.details["root_entity"] == "sales.orders"
    assert error.details["offending_entity"] == "sales.users"


def test_snapshot_versioning_is_stored_on_dataset(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "user_profile_daily = ms.entity(\n"
                "    name='user_profile_daily',\n"
                "    datasource=md.ref('datasource.warehouse'),\n"
                "    source=ms.table('user_profile_daily'),\n"
                "    primary_key=['user_id', 'dt'],\n"
                "    versioning=ms.snapshot(\n"
                "        partition_field=ms.ref('dimension.sales.user_profile_daily.dt'),\n"
                "        grain='day',\n"
                "        timezone='Asia/Shanghai',\n"
                "        format='%Y%m%d',\n"
                "    ),\n"
                ")\n"
                "@ms.dimension(entity=user_profile_daily)\n"
                "def dt(user_profile_daily):\n"
                "    return user_profile_daily.dt\n"
            ),
        }
    )

    dataset = SemanticCatalog(project).get("entity.sales.user_profile_daily").details()
    assert isinstance(dataset, EntityDetails)
    assert dataset.versioning is not None
    assert dataset.versioning.kind == "snapshot"
    assert dataset.versioning.partition_field == "sales.user_profile_daily.dt"
    assert dataset.versioning.grain == "day"
    assert dataset.versioning.timezone == "Asia/Shanghai"
    assert dataset.versioning.format == "%Y%m%d"
