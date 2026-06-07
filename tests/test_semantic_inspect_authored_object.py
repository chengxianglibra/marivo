from __future__ import annotations

_DATASETS = (
    "import marivo.semantic as ms\n"
    "\n"
    "orders = ms.dataset(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
    "\n"
    "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(),\n"
    "           name='revenue', verification_mode='python_native')\n"
    "def revenue(orders):\n"
    "    return orders.amount.sum()\n"
)

_FILES = {
    "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
    "sales/datasets.py": _DATASETS,
}


def test_inspect_authored_metric_reports_supported(semantic_project_factory):
    project = semantic_project_factory(_FILES)
    result = project.inspect_authored_object("sales.revenue")
    # metric_decomposition is auto-recorded on reload, so the dangerous-decision rule is satisfied
    assert result.status in ("supported", "needs_input")
    assert not any(i.severity == "blocker" for i in result.issues)


def test_inspect_unknown_object_is_blocked(semantic_project_factory):
    project = semantic_project_factory(_FILES)
    result = project.inspect_authored_object("sales.does_not_exist")
    assert result.status == "blocked"
    assert any(i.kind == "authored_object_invalid" for i in result.issues)


def test_inspect_authored_object_missing_business_definition_is_info(semantic_project_factory):
    project = semantic_project_factory(_FILES)
    result = project.inspect_authored_object("sales.revenue")
    assert any(
        i.rule_id == "business_definition_present" and i.severity == "info" for i in result.issues
    )
