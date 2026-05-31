"""Phase 1 cross-dataset observe semantic contracts."""

from __future__ import annotations

import pytest

from marivo.semantic.errors import SemanticLoadFailed


def test_base_metric_requires_additivity(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "@ms.dataset(name='orders', datasource='warehouse', primary_key=['order_id'])\n"
                "def orders(backend):\n"
                "    return backend.table('orders')\n"
                "@ms.metric(datasets=[orders], decomposition=ms.sum(), name='revenue')\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        },
    )

    with pytest.raises(SemanticLoadFailed) as exc_info:
        project.get_metric("sales.revenue")

    error = exc_info.value.errors[0]
    assert error.kind == "missing_metric_additivity"
    assert error.details == {"metric": "sales.revenue"}


def test_single_dataset_metric_defaults_root_dataset(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "@ms.dataset(name='orders', datasource='warehouse', primary_key=['order_id'])\n"
                "def orders(backend):\n"
                "    return backend.table('orders')\n"
                "@ms.metric(\n"
                "    datasets=[orders],\n"
                "    additivity='additive',\n"
                "    decomposition=ms.sum(),\n"
                "    name='revenue',\n"
                ")\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )

    metric = project.get_metric("sales.revenue")
    assert metric is not None
    assert metric.additivity == "additive"
    assert metric.root_dataset == "sales.orders"


def test_multi_dataset_metric_requires_explicit_root_dataset(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "@ms.dataset(name='orders', datasource='warehouse', primary_key=['order_id'])\n"
                "def orders(backend):\n"
                "    return backend.table('orders')\n"
                "@ms.dataset(name='users', datasource='warehouse', primary_key=['user_id'])\n"
                "def users(backend):\n"
                "    return backend.table('users')\n"
                "@ms.metric(\n"
                "    datasets=[orders, users],\n"
                "    additivity='additive',\n"
                "    decomposition=ms.sum(),\n"
                "    name='revenue',\n"
                ")\n"
                "def revenue(orders, users):\n"
                "    return orders.amount.sum()\n"
            ),
        },
    )

    with pytest.raises(SemanticLoadFailed) as exc_info:
        project.get_metric("sales.revenue")

    error = exc_info.value.errors[0]
    assert error.kind == "missing_metric_root_dataset"
    assert error.details == {"metric": "sales.revenue", "datasets": ["sales.orders", "sales.users"]}


def test_multi_dataset_metric_accepts_root_dataset_ref(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "@ms.dataset(name='orders', datasource='warehouse', primary_key=['order_id'])\n"
                "def orders(backend):\n"
                "    return backend.table('orders')\n"
                "@ms.dataset(name='users', datasource='warehouse', primary_key=['user_id'])\n"
                "def users(backend):\n"
                "    return backend.table('users')\n"
                "@ms.metric(\n"
                "    datasets=[orders, users],\n"
                "    root_dataset=orders,\n"
                "    additivity='additive',\n"
                "    decomposition=ms.sum(),\n"
                "    name='revenue',\n"
                ")\n"
                "def revenue(orders, users):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )

    metric = project.get_metric("sales.revenue")
    assert metric is not None
    assert metric.root_dataset == "sales.orders"


def test_multi_dataset_metric_rejects_non_root_aggregate_receiver(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "@ms.dataset(name='orders', datasource='warehouse', primary_key=['order_id'])\n"
                "def orders(backend):\n"
                "    return backend.table('orders')\n"
                "@ms.dataset(name='users', datasource='warehouse', primary_key=['user_id'])\n"
                "def users(backend):\n"
                "    return backend.table('users')\n"
                "@ms.metric(\n"
                "    datasets=[orders, users],\n"
                "    root_dataset=orders,\n"
                "    additivity='additive',\n"
                "    decomposition=ms.sum(),\n"
                "    name='bad_user_sum',\n"
                ")\n"
                "def bad_user_sum(orders, users):\n"
                "    return users.score.sum()\n"
            ),
        },
        load=False,
    )
    project.load()

    with pytest.raises(SemanticLoadFailed) as exc_info:
        project.get_metric("sales.bad_user_sum")

    error = exc_info.value.errors[0]
    assert error.kind == "non_root_metric_aggregate"
    assert error.details["metric"] == "sales.bad_user_sum"
    assert error.details["root_dataset"] == "sales.orders"
    assert error.details["offending_dataset"] == "sales.users"


def test_snapshot_versioning_is_stored_on_dataset(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "@ms.dataset(\n"
                "    name='user_profile_daily',\n"
                "    datasource='warehouse',\n"
                "    primary_key=['user_id', 'dt'],\n"
                "    versioning=ms.snapshot(\n"
                "        partition_field='dt',\n"
                "        grain='day',\n"
                "        timezone='Asia/Shanghai',\n"
                "        format='%Y%m%d',\n"
                "    ),\n"
                ")\n"
                "def user_profile_daily(backend):\n"
                "    return backend.table('user_profile_daily')\n"
            ),
        }
    )

    dataset = project.get_dataset("sales.user_profile_daily")
    assert dataset is not None
    assert dataset.versioning is not None
    assert dataset.versioning.kind == "snapshot"
    assert dataset.versioning.partition_field == "dt"
    assert dataset.versioning.grain == "day"
    assert dataset.versioning.timezone == "Asia/Shanghai"
    assert dataset.versioning.format == "%Y%m%d"
