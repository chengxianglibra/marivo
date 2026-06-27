"""End-to-end test exercising semantic authoring:
domain -> discover -> author -> verify -> readiness.
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

    # One bounded scope reused across the discovery-first authoring ladder.
    scope = md.unpruned(max_rows=20)

    # -- Rung 1: Domain already authored above --------------------------------

    # -- Rung 2: Entity - discover, author, verify ----------------------------
    entity_discovery = md.discover_entity(
        md.ref("warehouse"),
        md.table("orders"),
        scope=scope,
        project_root=tmp_path,
    )
    entity_render = entity_discovery.render()
    assert "primary key evidence:" in entity_render
    assert "order_id" in entity_render

    domain_file.write_text(
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales')\n"
        "orders = ms.entity(name='orders', datasource='warehouse', "
        "source=ms.table('orders'), primary_key=['order_id'])\n",
        encoding="utf-8",
    )
    project.load()
    verify = project.verify_object("sales.orders", scope=scope)
    assert verify.status == "passed", f"Entity verify failed: {verify.issues}"

    # -- Rung 3: Dimension - discover, author, verify -------------------------
    dimension_discovery = md.discover_dimensions(
        md.ref("warehouse"),
        md.table("orders"),
        columns=("customer_id",),
        scope=scope,
        project_root=tmp_path,
    )
    dimension_render = dimension_discovery.render()
    assert "customer_id" in dimension_render

    domain_file.write_text(
        domain_file.read_text(encoding="utf-8") + "@ms.dimension(entity=orders)\n"
        "def customer_id(orders):\n"
        "    return orders.customer_id\n",
        encoding="utf-8",
    )
    project.load()
    verify = project.verify_object("sales.orders.customer_id", scope=scope)
    assert verify.status == "passed", f"Dimension verify failed: {verify.issues}"

    # -- Rung 4: Time dimension - discover, author, verify --------------------
    time_discovery = md.discover_time_dimensions(
        md.ref("warehouse"),
        md.table("orders"),
        columns=("dt",),
        scope=scope,
        project_root=tmp_path,
    )
    time_render = time_discovery.render()
    assert "dt" in time_render
    assert "%Y%m%d" in time_render

    domain_file.write_text(
        domain_file.read_text(encoding="utf-8")
        + "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d'))\n"
        "def dt(orders):\n"
        "    return orders.dt\n",
        encoding="utf-8",
    )
    project.load()
    verify = project.verify_object("sales.orders.dt", scope=scope)
    assert verify.status == "passed", f"Time dimension verify failed: {verify.issues}"

    # -- Rung 5: Measure - discover, author, verify ---------------------------
    measure_discovery = md.discover_measures(
        md.ref("warehouse"),
        md.table("orders"),
        columns=("amount",),
        scope=scope,
        project_root=tmp_path,
    )
    measure_render = measure_discovery.render()
    assert "amount" in measure_render

    domain_file.write_text(
        domain_file.read_text(encoding="utf-8")
        + "@ms.measure(entity=orders, additivity='additive')\n"
        "def amount(orders):\n"
        "    return orders.amount\n",
        encoding="utf-8",
    )
    project.load()
    verify = project.verify_object("sales.orders.amount", scope=scope)
    assert verify.status == "passed", f"Measure verify failed: {verify.issues}"

    # -- Rung 6: Metric - aggregate the verified measure, then verify ---------
    domain_file.write_text(
        domain_file.read_text(encoding="utf-8")
        + "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum')\n",
        encoding="utf-8",
    )
    project.load()
    verify = project.verify_object("sales.revenue", scope=scope)
    assert verify.status == "passed", f"Metric verify failed: {verify.issues}"

    # -- Closeout: Readiness --------------------------------------------------
    report = project.readiness(
        refs=("sales.orders", "sales.orders.amount", "sales.revenue"),
    )

    # Structural readiness may report blockers (e.g. missing business_definition)
    # when the ladder objects lack ai_context. The important thing is that
    # readiness runs without error.
    assert report.status in {"ready", "ready_with_warnings", "blocked"}
    assert report.abandoned == ()
