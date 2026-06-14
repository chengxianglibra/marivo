"""Tests for registry-only and data-backed prepare APIs."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.semantic as ms
from marivo.semantic.errors import SemanticLoadFailed


def test_prepare_domain_reports_exact_registered_match(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {"sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n"}
    )
    project.load()

    brief = project.prepare_domain(name="sales")

    assert brief.status == "needs_input"
    assert brief.matches[0].ref == "sales"
    assert brief.matches[0].basis == "name_exact"


def test_prepare_domain_new_name_is_sufficient(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {"sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n"}
    )
    project.load()

    brief = project.prepare_domain(name="inventory")

    assert brief.status == "sufficient"
    assert brief.proposed_name == "inventory"
    assert len(brief.matches) == 0


def test_prepare_domain_requires_loaded_project(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {"sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n"},
        load=False,
    )

    with pytest.raises(SemanticLoadFailed):
        project.prepare_domain(name="inventory")


def test_prepare_derived_metric_blocks_missing_component(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {"sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n"}
    )
    project.load()

    brief = project.prepare_derived_metric(numerator="sales.revenue", denominator="sales.orders")

    assert brief.status == "blocked"
    assert brief.issues[0].kind == "missing_prerequisite"
    assert "sales.revenue" in brief.issues[0].refs


def test_module_prepare_domain_uses_loaded_project(tmp_path, monkeypatch) -> None:
    semantic_dir = tmp_path / "marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    brief = ms.prepare_domain(name="new_sales")

    assert brief.status == "sufficient"
    assert brief.proposed_name == "new_sales"


# ---------------------------------------------------------------------------
# Data-backed prepare APIs (Task 7)
# ---------------------------------------------------------------------------


def test_prepare_entity_collects_metadata_profiles_and_matches(
    tmp_path: Path, semantic_project_factory
) -> None:
    import ibis

    import marivo.datasource as md

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.create_table("orders", {"order_id": [1, 2], "dt": ["20260611", "20260612"]})
    con.disconnect()

    md.register(
        md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {"sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n"},
        workspace_dir=tmp_path,
    )
    project.load()

    brief = project.prepare_entity(
        datasource="warehouse",
        source=md.table("orders"),
        domain="sales",
        scope=md.ScanScope(partition=None, max_rows=10),
    )

    assert brief.status == "sufficient"
    assert brief.table.table == "orders"
    assert [profile.column for profile in brief.column_profiles] == ["order_id", "dt"]
    assert "dt" in brief.time_like_columns
    assert brief.scan.partition_resolution == "unpruned"


def test_prepare_dimensions_blocks_unknown_column(tmp_path: Path, semantic_project_factory) -> None:
    import ibis

    import marivo.datasource as md

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.create_table("orders", {"order_id": [1, 2], "status": ["open", "closed"]})
    con.disconnect()

    md.register(
        md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
            )
        },
        workspace_dir=tmp_path,
    )
    project.load()

    project.verify_object("sales.orders")

    brief = project.prepare_dimensions(
        entity="sales.orders",
        columns=("missing_col",),
        scope=md.ScanScope(partition=None),
    )[0]

    assert brief.status == "blocked"
    assert brief.issues[0].kind == "missing_column"


# ---------------------------------------------------------------------------
# Relationship and cross-entity prepare APIs (Task 8)
# ---------------------------------------------------------------------------


def test_prepare_relationship_uses_join_probe(tmp_path: Path, semantic_project_factory) -> None:
    import ibis

    import marivo.datasource as md

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.create_table("orders", {"customer_id": [1, 2, 3]})
    con.create_table("customers", {"customer_id": [1, 2]})
    con.disconnect()

    md.register(
        md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "customers = ms.entity(name='customers', datasource='warehouse', source=ms.table('customers'))\n"
                "@ms.dimension(entity=orders)\n"
                "def customer_id(orders):\n"
                "    return orders.customer_id\n"
                "@ms.dimension(entity=customers, name='customer_id')\n"
                "def customer_id_customers(customers):\n"
                "    return customers.customer_id\n"
            )
        },
        workspace_dir=tmp_path,
    )
    project.load()

    project.verify_object("sales.orders")
    project.verify_object("sales.customers")

    brief = project.prepare_relationship(
        from_entity="sales.orders",
        to_entity="sales.customers",
        from_dimensions=("sales.orders.customer_id",),
        to_dimensions=("sales.customers.customer_id",),
        scope=md.ScanScope(partition=None),
    )

    assert brief.status == "sufficient"
    assert brief.probe.sampled_key_count == 3
    assert brief.probe.matched_key_count == 2


def test_prepare_cross_entity_metric_blocks_unreachable_entity(
    tmp_path: Path, semantic_project_factory
) -> None:
    import ibis

    import marivo.datasource as md

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.create_table("orders", {"order_id": [1, 2]})
    con.create_table("customers", {"customer_id": [1, 2]})
    con.disconnect()

    md.register(
        md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "customers = ms.entity(name='customers', datasource='warehouse', source=ms.table('customers'))\n"
            )
        },
        workspace_dir=tmp_path,
    )
    project.load()

    project.verify_object("sales.orders")
    project.verify_object("sales.customers")

    brief = project.prepare_cross_entity_metric(
        root_entity="sales.orders",
        entities=("sales.customers",),
    )

    assert brief.status == "blocked"
    assert brief.unreachable_entities == ("sales.customers",)
    assert brief.issues[0].kind == "unreachable_entity"
