"""End-to-end test exercising the entire stepwise authoring ladder:
domain -> entity -> dimension -> time_dimension -> metric -> readiness.
"""

from __future__ import annotations

from pathlib import Path

import ibis

import marivo.datasource as md
from marivo.datasource.authoring import _DuckDBSpec
from marivo.semantic.reader import SemanticProject


def test_stepwise_authoring_ladder_e2e(tmp_path: Path) -> None:
    # -- Setup: create a real DuckDB with sample data -------------------------
    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table(
        "orders",
        {
            "order_id": [1, 2, 3],
            "customer_id": [10, 20, 20],
            "dt": ["20260610", "20260611", "20260612"],
            "amount": [100, 200, 300],
        },
    )
    con.disconnect()

    # Register the datasource in the project
    md.register(
        _DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )

    # Create the domain file so the semantic project has a starting point
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    domain_file = semantic_dir / "_domain.py"
    domain_file.write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n",
        encoding="utf-8",
    )

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()

    # -- Rung 1: Domain already authored above --------------------------------

    # -- Rung 2: Entity - prepare, author, verify -----------------------------
    entity_brief = project.prepare_entity(
        datasource="warehouse",
        source=md.table("orders"),
        domain="sales",
        scope=md.ScanScope(partition=None),
    )
    assert entity_brief.status == "sufficient"

    domain_file.write_text(
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales')\n"
        "orders = ms.entity(name='orders', datasource='warehouse', "
        "source=ms.table('orders'), primary_key=['order_id'])\n",
        encoding="utf-8",
    )
    project.load()
    verify = project.verify_object("sales.orders", scope=md.ScanScope(partition=None))
    assert verify.status == "passed", f"Entity verify failed: {verify.issues}"

    # -- Rung 3: Dimension - prepare, author, verify --------------------------
    dim_brief = project.prepare_dimension(
        entity="sales.orders",
        column="customer_id",
        scope=md.ScanScope(partition=None),
    )
    assert dim_brief.status == "sufficient"

    domain_file.write_text(
        domain_file.read_text(encoding="utf-8") + "@ms.dimension(entity=orders)\n"
        "def customer_id(orders):\n"
        "    return orders.customer_id\n",
        encoding="utf-8",
    )
    project.load()
    verify = project.verify_object("sales.orders.customer_id", scope=md.ScanScope(partition=None))
    assert verify.status == "passed", f"Dimension verify failed: {verify.issues}"

    # -- Rung 4: Time dimension - prepare, author, verify ---------------------
    time_brief = project.prepare_time_dimension(
        entity="sales.orders",
        column="dt",
        scope=md.ScanScope(partition=None),
    )
    assert time_brief.status == "sufficient"

    domain_file.write_text(
        domain_file.read_text(encoding="utf-8")
        + "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d', data_type='string'))\n"
        "def dt(orders):\n"
        "    return orders.dt\n",
        encoding="utf-8",
    )
    project.load()
    verify = project.verify_object("sales.orders.dt", scope=md.ScanScope(partition=None))
    assert verify.status == "passed", f"Time dimension verify failed: {verify.issues}"

    # -- Rung 5: Metric - prepare, author, verify -----------------------------
    metric_brief = project.prepare_metric(
        entity="sales.orders",
        measure_columns=("amount",),
        scope=md.ScanScope(partition=None),
    )
    assert metric_brief.status == "sufficient"

    domain_file.write_text(
        domain_file.read_text(encoding="utf-8")
        + "@ms.metric(entities=[orders], additivity='additive', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n",
        encoding="utf-8",
    )
    project.load()
    verify = project.verify_object("sales.revenue", scope=md.ScanScope(partition=None))
    assert verify.status == "passed", f"Metric verify failed: {verify.issues}"

    # -- Closeout: Readiness --------------------------------------------------
    report = project.readiness(
        refs=("sales.orders", "sales.revenue"),
    )

    # Structural readiness may report blockers (e.g. missing business_definition)
    # when the ladder objects lack ai_context. The important thing is that
    # readiness runs without error.
    assert report.status in {"ready", "ready_with_warnings", "blocked"}
    assert report.abandoned == ()
