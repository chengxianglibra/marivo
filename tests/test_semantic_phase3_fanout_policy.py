"""Phase 3: fanout_policy authoring on @ms.metric."""

from __future__ import annotations

from marivo.semantic.ir import MetricIR


def test_metric_ir_has_fanout_policy_default_block():
    # Construct a minimal MetricIR-like dict via dataclasses.fields to assert
    # the new field exists with the documented default.
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(MetricIR)}
    assert "fanout_policy" in fields
    assert fields["fanout_policy"].default == "block"


def test_metric_authoring_accepts_fanout_policy(tmp_path, monkeypatch):
    from marivo.semantic.loader import load_project

    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "orders = ms.dataset(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "order_items = ms.dataset(name='order_items', datasource='warehouse', primary_key=['item_id'], source=ms.table('order_items'))\n"
        "@ms.field(dataset=orders)\n"
        "def order_id(orders):\n"
        "    return orders.order_id\n"
        "@ms.metric(\n"
        "    datasets=[orders, order_items],\n"
        "    root_dataset=orders,\n"
        "    additivity='additive',\n"
        "    decomposition=ms.sum(),\n"
        "    fanout_policy='aggregate_then_join',\n"
        "    name='gmv_with_items',\n"
        "    verification_mode='python_native',\n"
        "    )\n"
        "def gmv_with_items(orders, order_items):\n"
        "    return orders.amount.sum()\n"
    )
    monkeypatch.chdir(tmp_path)
    project = load_project(tmp_path / ".marivo" / "semantic")
    assert project.status == "ready", project.errors
    assert project.registry is not None
    metric = project.registry.metrics["sales.gmv_with_items"]
    assert metric.fanout_policy == "aggregate_then_join"


def test_error_kinds_and_constraint_id_present():
    from marivo.semantic.constraints import ConstraintId
    from marivo.semantic.errors import ErrorKind

    assert ErrorKind.INVALID_METRIC_FANOUT_POLICY == "invalid_metric_fanout_policy"
    assert ErrorKind.DERIVED_METRIC_FANOUT_POLICY == "derived_metric_fanout_policy"
    assert ConstraintId.METRIC_FANOUT_POLICY_VALID == "metric_fanout_policy_valid"


def _bootstrap_min(tmp_path):
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
    return semantic_dir


def test_validator_rejects_fanout_policy_on_non_additive_metric(tmp_path, monkeypatch):
    from marivo.semantic.errors import ErrorKind
    from marivo.semantic.loader import load_project

    semantic_dir = _bootstrap_min(tmp_path)
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "orders = ms.dataset(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "order_items = ms.dataset(name='order_items', datasource='warehouse', primary_key=['item_id'], source=ms.table('order_items'))\n"
        "@ms.metric(\n"
        "    datasets=[orders, order_items],\n"
        "    root_dataset=orders,\n"
        "    additivity='non_additive',\n"
        "    decomposition=ms.sum(),\n"
        "    fanout_policy='aggregate_then_join',\n"
        "    name='non_additive_bad',\n"
        "    verification_mode='python_native',\n"
        "    )\n"
        "def non_additive_bad(orders, order_items):\n"
        "    return orders.user_id.nunique()\n"
    )
    monkeypatch.chdir(tmp_path)
    project = load_project(tmp_path / ".marivo" / "semantic")
    assert project.status == "errored"
    kinds = {err.kind for err in project.errors}
    assert ErrorKind.INVALID_METRIC_FANOUT_POLICY in kinds


def test_derived_metric_keeps_default_fanout_policy(tmp_path, monkeypatch):
    from marivo.semantic.loader import load_project

    semantic_dir = _bootstrap_min(tmp_path)
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "orders = ms.dataset(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='gmv', verification_mode='python_native',)\n"
        "def gmv(orders):\n"
        "    return orders.amount.sum()\n"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), name='cnt', verification_mode='python_native',)\n"
        "def cnt(orders):\n"
        "    return orders.count()\n"
        "aov = ms.derived_metric(\n"
        "    name='aov',\n"
        "    decomposition=ms.ratio(numerator='sales.gmv', denominator='sales.cnt'),\n"
        ")\n"
    )
    monkeypatch.chdir(tmp_path)
    project = load_project(tmp_path / ".marivo" / "semantic")
    assert project.status == "ready", project.errors
    assert project.registry is not None
    metric = project.registry.metrics["sales.aov"]
    assert metric.is_derived is True
    assert metric.fanout_policy == "block"
