"""Cumulative metric semantic authoring and load validation."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.semantic as ms


def _write_project(tmp_path: Path, body: str) -> Path:
    """Bootstrap a minimal semantic project for ms.load().

    Layout matches the real project conventions:
      <root>/marivo.toml
      <root>/models/datasources/warehouse.py
      <root>/models/semantic/sales/_domain.py
      <root>/models/semantic/sales/metrics.py
    """
    root = tmp_path / "project"
    root.mkdir(parents=True)
    (root / "marivo.toml").write_text('[project]\nname = "test"\n', encoding="utf-8")
    models = root / "models"
    semantic_dir = models / "semantic" / "sales"
    datasources_dir = models / "datasources"
    semantic_dir.mkdir(parents=True)
    datasources_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("", encoding="utf-8")
    (datasources_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
        encoding="utf-8",
    )
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
        encoding="utf-8",
    )
    (semantic_dir / "metrics.py").write_text(body, encoding="utf-8")
    return root


def test_cumulative_load_resolves_single_time_dimension_and_non_additive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(
        tmp_path,
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "event_time = ms.time_dimension_column("
        "name='event_time', entity=orders, column='created_at', granularity='day')\n"
        "user_id = ms.measure_column("
        "name='user_id', entity=orders, column='user_id', additivity='non_additive')\n"
        "active_users = ms.aggregate(name='active_users', measure=user_id, agg='count_distinct')\n"
        "cum_active_users = ms.cumulative(name='cum_active_users', base=active_users)\n",
    )
    monkeypatch.chdir(root)

    catalog = ms.load()
    details = catalog.get("metric.sales.cum_active_users").details()

    assert details.metric_type == "derived"
    assert details.composition == "cumulative"
    assert details.components[0][0] == "base"
    assert details.components[0][1].id == "sales.active_users"
    assert details.additivity == "non_additive"


def test_cumulative_rejects_derived_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_project(
        tmp_path,
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "event_time = ms.time_dimension_column("
        "name='event_time', entity=orders, column='created_at', granularity='day')\n"
        "amount = ms.measure_column("
        "name='amount', entity=orders, column='amount', additivity='additive')\n"
        "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum')\n"
        "orders_count = ms.count(name='orders_count', entity=orders)\n"
        "aov = ms.ratio(name='aov', numerator=revenue, denominator=orders_count)\n"
        "bad = ms.cumulative(name='bad', base=aov, over=event_time)\n",
    )
    monkeypatch.chdir(root)

    with pytest.raises(ms.errors.SemanticLoadFailed) as exc_info:
        ms.load()

    message = str(exc_info.value)
    assert "cumulative base" in message
    assert "derived" in message
    assert "ratio of two cumulative metrics" in message


def test_cumulative_rejects_unsupported_base_aggregation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(
        tmp_path,
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "event_time = ms.time_dimension_column("
        "name='event_time', entity=orders, column='created_at', granularity='day')\n"
        "amount = ms.measure_column("
        "name='amount', entity=orders, column='amount', additivity='additive')\n"
        "avg_amount = ms.aggregate(name='avg_amount', measure=amount, agg='mean')\n"
        "bad = ms.cumulative(name='bad', base=avg_amount, over=event_time)\n",
    )
    monkeypatch.chdir(root)

    with pytest.raises(ms.errors.SemanticLoadFailed) as exc_info:
        ms.load()

    message = str(exc_info.value)
    assert "mean" in message
    assert "cumulative sum over cumulative count" in message


def test_cumulative_omitted_over_rejects_multiple_time_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(
        tmp_path,
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "created_at = ms.time_dimension_column("
        "name='created_at', entity=orders, column='created_at', granularity='day', "
        "is_default=True)\n"
        "paid_at = ms.time_dimension_column("
        "name='paid_at', entity=orders, column='paid_at', granularity='day')\n"
        "amount = ms.measure_column("
        "name='amount', entity=orders, column='amount', additivity='additive')\n"
        "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum')\n"
        "bad = ms.cumulative(name='bad', base=revenue)\n",
    )
    monkeypatch.chdir(root)

    with pytest.raises(ms.errors.SemanticLoadFailed) as exc_info:
        ms.load()

    message = str(exc_info.value)
    assert "over=" in message
    assert "sales.orders.created_at" in message
    assert "sales.orders.paid_at" in message
    assert "default" in message


def test_cumulative_rejects_tier2_body_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(
        tmp_path,
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "event_time = ms.time_dimension_column("
        "name='event_time', entity=orders, column='created_at', granularity='day')\n"
        "amount = ms.measure_column("
        "name='amount', entity=orders, column='amount', additivity='additive')\n"
        "@ms.metric(entities=[orders], additivity='additive')\n"
        "def custom_revenue(orders):\n"
        "    return (orders.amount * orders.qty).sum()\n"
        "bad = ms.cumulative(name='bad', base=custom_revenue, over=event_time)\n",
    )
    monkeypatch.chdir(root)

    with pytest.raises(ms.errors.SemanticLoadFailed) as exc_info:
        ms.load()

    message = str(exc_info.value)
    assert "tier-2" in message
    assert "body" in message
    assert "ms.aggregate(...)" in message or "ms.count(...)" in message


def test_cumulative_rejects_unknown_over_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(
        tmp_path,
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "event_time = ms.time_dimension_column("
        "name='event_time', entity=orders, column='created_at', granularity='day')\n"
        "amount = ms.measure_column("
        "name='amount', entity=orders, column='amount', additivity='additive')\n"
        "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum')\n"
        "bogus_ref = ms.TimeDimensionRef('sales.orders.nonexistent')\n"
        "bad = ms.cumulative(name='bad', base=revenue, over=bogus_ref)\n",
    )
    monkeypatch.chdir(root)

    with pytest.raises(ms.errors.SemanticLoadFailed) as exc_info:
        ms.load()

    message = str(exc_info.value)
    assert "sales.orders.nonexistent" in message
    assert "not a known time dimension" in message

    load_errors = exc_info.value.errors
    matching = [e for e in load_errors if e.kind == ms.errors.ErrorKind.MISSING_DIMENSION_REF]
    assert matching, "expected at least one MISSING_DIMENSION_REF error"
    details = matching[0].details
    assert details["missing_ref"] == "sales.orders.nonexistent"
    assert "sales.orders.event_time" in details["did_you_mean"]


def test_cumulative_rejects_non_root_over_axis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(
        tmp_path,
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "shipments = ms.entity(name='shipments', datasource=warehouse, "
        "source=md.table('shipments'))\n"
        "order_time = ms.time_dimension_column("
        "name='order_time', entity=orders, column='created_at', granularity='day')\n"
        "ship_time = ms.time_dimension_column("
        "name='ship_time', entity=shipments, column='shipped_at', granularity='day')\n"
        "amount = ms.measure_column("
        "name='amount', entity=orders, column='amount', additivity='additive')\n"
        "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum')\n"
        "bad = ms.cumulative(name='bad', base=revenue, over=ship_time)\n",
    )
    monkeypatch.chdir(root)

    with pytest.raises(ms.errors.SemanticLoadFailed) as exc_info:
        ms.load()

    message = str(exc_info.value)
    assert "ship_time" in message
    assert "shipments" in message
    assert "orders" in message
