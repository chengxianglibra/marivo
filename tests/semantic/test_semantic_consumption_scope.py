"""Receiver scope owns named definitions while anonymous calculations remain fixed."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.deferred import inspect_deferred
from marivo.analysis.errors import AnalysisError
from marivo.semantic._consumption import bind_field, bind_metric
from marivo.semantic._expression_values import MetricExpression, RowExpression, _as_deferred
from marivo.semantic.errors import SemanticError
from tests.shared_fixtures import write_minimal_catalog_project


def _project(path: Path) -> None:
    write_minimal_catalog_project(path)
    database = path / "facts.duckdb"
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            "CREATE TABLE orders(order_id BIGINT, status VARCHAR, paid_at TIMESTAMP, amount DOUBLE)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1, 'paid', TIMESTAMP '2026-10-01', 10), (2, 'paid', TIMESTAMP '2026-10-02', 10)"
        )
    finally:
        connection.close()
    (path / "models/datasources/warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n"
    )


def test_named_formula_rebinds_but_anonymous_operation_and_prior_frame_remain_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    original_session = mv.session.get_or_create(name="scope_original", timezone="UTC")
    original = original_session.catalog.get("metric:sales.revenue")
    assert original.kind == "metric"
    original_formula = original.definition.formula
    original_frame = original_session.observe({"revenue": original})
    expression = original / 2
    source = tmp_path / "models/semantic/sales/objects.py"
    source.write_text(
        source.read_text().replace("return amount_value.sum()", "return amount_value.sum() * 2")
    )
    receiving_session = mv.session.get_or_create(name="scope_receiving", timezone="UTC")
    current = receiving_session.catalog.get("metric:sales.revenue")
    assert current.kind == "metric"
    assert current.definition.formula != original_formula
    assert receiving_session.observe({"revenue": expression}).compute().to_pandas()[
        "revenue"
    ].tolist() == [20.0]
    assert original_frame.compute().to_pandas()["revenue"].tolist() == [20.0]
    assert original.definition.formula == original_formula


def test_bound_scope_requires_exact_kind_path_and_preserves_cross_project_constraints(
    tmp_path: Path,
) -> None:
    first_root, other_root = tmp_path / "first", tmp_path / "other"
    _project(first_root)
    _project(other_root)
    first = ms.load(workspace_dir=first_root)
    reloaded = ms.load(workspace_dir=first_root)
    other = ms.load(workspace_dir=other_root)
    amount = first.get("measure:sales.orders.amount")
    revenue = first.get("metric:sales.revenue")
    assert amount.kind == "measure" and revenue.kind == "metric"
    assert bind_metric(revenue / 2, reloaded) is not None
    with pytest.raises(SemanticError, match="projects"):
        bind_metric(revenue / 2, other)
    with pytest.raises(SemanticError, match="projects"):
        bind_field(amount, other)
    with pytest.raises(SemanticError, match="projects"):
        _ = revenue + other.get("metric:sales.revenue")
    objects = first_root / "models/semantic/sales/objects.py"
    objects.write_text(objects.read_text().replace('name="amount"', 'name="gross"'))
    # The declaration still uses the renamed physical symbol, but the original field id is gone.
    changed = ms.load(workspace_dir=first_root)
    with pytest.raises(SemanticError, match="not found"):
        bind_field(amount, changed)


def test_typed_condition_and_frozen_collection_keep_one_stage(tmp_path: Path) -> None:
    _project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    amount = catalog.get("measure:sales.orders.amount")
    revenue = catalog.get("metric:sales.revenue")
    status = catalog.get("dimension:sales.orders.status")
    assert amount.kind == "measure" and revenue.kind == "metric" and status.kind == "dimension"
    row_condition = amount > 0
    metric_condition = revenue > 0
    assert isinstance(row_condition.ifelse(amount, 0), RowExpression)
    assert isinstance(metric_condition.ifelse(revenue, 0), MetricExpression)
    with pytest.raises(SemanticError, match="truth value"):
        bool(row_condition)
    with pytest.raises(SemanticError, match="same stage"):
        _ = amount + revenue
    with pytest.raises(SemanticError, match="numeric"):
        _ = status + 1
    values = ["paid"]
    condition = status.isin(values)
    captured = inspect_deferred(_as_deferred(condition)).fingerprint
    values.append("refund")
    assert inspect_deferred(_as_deferred(condition)).fingerprint == captured
    assert not hasattr(revenue / 2, "id")
    assert not hasattr(revenue, "value") and not hasattr(revenue, "ref")


def test_decorators_keep_fixed_stage_for_loaded_and_forward_inputs(tmp_path: Path) -> None:
    from marivo.semantic._consumption import bind_row
    from marivo.semantic._deferred_semantics import consume_row

    _project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    amount = catalog.get("measure:sales.orders.amount")
    status = catalog.get("dimension:sales.orders.status")
    assert amount.kind == "measure" and status.kind == "dimension"

    @ms.metric()
    def local_total():
        return amount.sum()

    future = ms.ref.measure("sales.orders.amount")

    @ms.metric()
    def forward_total():
        return future.sum()

    @ms.measure(additivity=ms.additive_over(status))
    def protected_amount():
        return amount

    @ms.measure()
    def forward_amount():
        return future

    assert type(local_total) is type(forward_total) is ms.MetricExpression
    assert type(protected_amount) is type(forward_amount) is ms.RowExpression
    assert not hasattr(local_total, "id")
    assert bind_metric(local_total, catalog) is not None
    permission = consume_row(bind_row(protected_amount, catalog)).permission
    assert tuple(axis.ref.key for axis in permission.axes) == (status.id,)
    with pytest.raises(SemanticError, match="authoring symbols"):
        bind_metric(forward_total, catalog)
    source = tmp_path / "models/semantic/sales/objects.py"
    source.write_text(
        source.read_text().replace(
            "orders = ms.entity",
            'future = ms.ref.measure("sales.orders.amount")\n'
            "@ms.metric()\ndef forward_total():\n    return future.sum()\n"
            "@ms.measure()\ndef forward_amount():\n    return future\n"
            "orders = ms.entity",
        )
    )
    loaded = ms.load(workspace_dir=tmp_path)
    assert loaded.get("metric:sales.forward_total").kind == "metric"
    assert loaded.get("measure:sales.orders.forward_amount").kind == "measure"


def test_local_loaded_authorization_preserves_project_and_rechecks_receiving_axes(
    tmp_path: Path,
) -> None:
    from marivo.semantic._consumption import bind_row

    first_root, other_root = tmp_path / "first", tmp_path / "other"
    _project(first_root)
    _project(other_root)
    first, other = ms.load(workspace_dir=first_root), ms.load(workspace_dir=other_root)
    amount = first.get("measure:sales.orders.amount")
    status = first.get("dimension:sales.orders.status")
    foreign_status = other.get("dimension:sales.orders.status")
    relationship = first.get("relationship:sales.orders_self")
    assert amount.kind == "measure" and status.kind == foreign_status.kind == "dimension"
    assert relationship.kind == "relationship"

    @ms.measure(additivity=ms.additive_over(status))
    def protected_amount():
        return amount

    with pytest.raises(SemanticError, match="projects"):

        @ms.measure(additivity=ms.additive_over(foreign_status))
        def foreign_authorization():
            return amount

    with pytest.raises(SemanticError):
        ms.additive_over(status.via((relationship,)))
    with pytest.raises(SemanticError, match="tuple"):
        status.via([relationship])
    source = first_root / "models/semantic/sales/objects.py"
    source.write_text(source.read_text().replace('name="status"', 'name="state"'))
    changed = ms.load(workspace_dir=first_root)
    with pytest.raises(SemanticError, match="unknown or foreign axes"):
        bind_row(protected_amount, changed)


def test_local_declaration_defers_captured_units_to_actual_receiving_scope(tmp_path: Path) -> None:
    from marivo.semantic._consumption import bind_row
    from marivo.semantic._deferred_semantics import consume_metric, consume_row

    _project(tmp_path)
    source = tmp_path / "models/semantic/sales/objects.py"
    source.write_text(
        source.read_text()
        + '\ncost = ms.measure_column(name="cost", entity=orders, column="amount", unit="USD", additivity=ms.additive_all())\n'
    )
    old = ms.load(workspace_dir=tmp_path)
    old_amount = old.get("measure:sales.orders.amount")
    old_revenue = old.get("metric:sales.revenue")
    assert old_amount.kind == "measure" and old_revenue.kind == "metric"
    source.write_text(
        source.read_text().replace('unit="USD"', 'unit="EUR"') + "\n# receiving definition update\n"
    )
    current = ms.load(workspace_dir=tmp_path)
    current_cost = current.get("measure:sales.orders.cost")
    current_revenue = current.get("metric:sales.revenue")
    assert current_cost.kind == "measure" and current_revenue.kind == "metric"

    @ms.measure()
    def local_rows():
        return old_amount + current_cost

    @ms.metric()
    def local_metric():
        return old_revenue + current_revenue

    assert consume_row(bind_row(local_rows, current)).unit == "EUR"
    assert consume_metric(bind_metric(local_metric, current)).unit == "EUR"
    with pytest.raises(SemanticError, match="completed Metric"):

        @ms.metric()
        def wrong_stage():
            return old_amount


@pytest.mark.parametrize("kind", ["measure", "metric"])
@pytest.mark.parametrize("side", ["lower", "upper"])
def test_clip_preserves_each_loaded_bound_project(
    tmp_path: Path,
    kind: str,
    side: str,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    _project(first)
    _project(second)
    identity = "measure:sales.orders.amount" if kind == "measure" else "metric:sales.revenue"
    left, right = (
        ms.load(workspace_dir=first).get(identity),
        ms.load(workspace_dir=second).get(identity),
    )
    assert isinstance(left, (ms.Measure, ms.Metric)) and isinstance(right, (ms.Measure, ms.Metric))
    with pytest.raises(SemanticError, match="different projects"):
        if side == "lower":
            left.clip(lower=right)
        else:
            left.clip(upper=right)


def test_local_entity_count_rebinds_default_time_in_receiving_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    old = ms.load(workspace_dir=tmp_path)
    orders = old.get("entity:sales.orders")
    assert orders.kind == "entity"

    @ms.metric()
    def local_count():
        return orders.count()

    source = tmp_path / "models/semantic/sales/objects.py"
    source.write_text(source.read_text().replace("is_default=True", "is_default=False"))
    current = mv.session.get_or_create(name="local_count_new_scope", timezone="UTC")
    period = current.time_scope(start="2026-10-01", end="2026-11-01")
    for expression in (orders.count(), local_count):
        with pytest.raises(AnalysisError, match="business time role"):
            current.observe({"count": expression}, by=(period,)).compute()


def test_local_relationship_dependency_matches_direct_routed_formula(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(tmp_path)
    source = tmp_path / "models/semantic/sales/objects.py"
    source.write_text(
        source.read_text()
        .replace(
            "ms.relationship(name=",
            'order_id = ms.dimension_column(name="order_id", entity=orders, column="order_id")\nms.relationship(name=',
        )
        .replace("ms.join_on(status, status)", "ms.join_on(order_id, customer_id)")
        .replace(
            "ms.relationship(name=",
            'customers = ms.entity(name="customers", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["order_id"])\ncustomer_id = ms.dimension_column(name="order_id", entity=customers, column="order_id")\ncustomer_status = ms.dimension_column(name="status", entity=customers, column="status")\nms.relationship(name=',
        )
        .replace("to_entity=orders,", "to_entity=customers,")
    )
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create(name="local_named_relationship", timezone="UTC")
    amount = session.catalog.get("measure:sales.orders.amount")
    relationship = session.catalog.get("relationship:sales.orders_self")
    status = session.catalog.get("dimension:sales.customers.status")
    assert (
        amount.kind == "measure"
        and relationship.kind == "relationship"
        and status.kind == "dimension"
    )

    @ms.metric()
    def local_routed():
        return amount.sum().where(status.via((relationship,)) == "paid")

    rows = (
        session.observe(
            {
                "direct": amount.sum().where(status.via((relationship,)) == "paid"),
                "local": local_routed,
            }
        )
        .compute()
        .to_pandas()
    )
    assert rows["direct"].tolist() == rows["local"].tolist() == [20.0]


def test_local_native_functions_defer_unit_facts_to_receiving_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import math

    _project(tmp_path)
    revenue = ms.load(workspace_dir=tmp_path).get("metric:sales.revenue")
    assert revenue.kind == "metric" and revenue.definition.unit == "USD"

    @ms.metric()
    def local_log():
        return revenue.log()

    @ms.metric()
    def local_sqrt():
        return revenue.sqrt()

    source = tmp_path / "models/semantic/sales/objects.py"
    source.write_text(source.read_text().replace('unit="USD"', 'unit="1"'))
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    current = mv.session.get_or_create(name="local_current_unit", timezone="UTC")
    rows = current.observe({"log": local_log, "sqrt": local_sqrt}).compute().to_pandas()
    assert rows["log"].tolist() == pytest.approx([math.log(20)])
    assert rows["sqrt"].tolist() == pytest.approx([math.sqrt(20)])
    assert revenue.definition.unit == "USD"


def test_get_object_repair_explains_direct_use_and_explicit_reacquisition(tmp_path: Path) -> None:
    _project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    revenue = catalog.get("metric:sales.revenue")
    with pytest.raises(SemanticError) as caught:
        catalog.get(revenue)
    assert caught.value.repair is not None
    assert "directly" in caught.value.repair.action
    assert "object.id" in caught.value.repair.action
    assert caught.value.hint is not None and "object directly" in caught.value.hint
    assert "ms.ref" not in caught.value.hint
    assert caught.value.repair.help_target.canonical_id == "semantic_catalog.get"
