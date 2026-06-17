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
    assert brief.authoring_template is not None
    assert "ms.ratio(" in brief.authoring_template


def test_prepare_derived_metric_ratio_includes_authoring_template(
    semantic_project_factory,
) -> None:
    model = (
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales')\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "@ms.metric(entities=[orders], additivity='additive',)\n"
        "def revenue(t):\n"
        "    return t.amount.sum()\n"
        "@ms.metric(entities=[orders], additivity='additive',)\n"
        "def orders_count(t):\n"
        "    return t.order_id.nunique()\n"
    )
    project = semantic_project_factory({"sales/_domain.py": model})
    project.load()

    brief = project.prepare_derived_metric(
        numerator="sales.revenue", denominator="sales.orders_count"
    )

    assert brief.status == "sufficient"
    assert brief.composition_kind == "ratio"
    assert brief.authoring_template is not None
    assert "ms.ratio(" in brief.authoring_template
    assert "sales.revenue" in brief.authoring_template
    assert "sales.orders_count" in brief.authoring_template


def test_prepare_derived_metric_weighted_average_includes_authoring_template(
    semantic_project_factory,
) -> None:
    model = (
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales')\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "@ms.metric(entities=[orders], additivity='additive',)\n"
        "def revenue(t):\n"
        "    return t.amount.sum()\n"
        "@ms.metric(entities=[orders], additivity='additive',)\n"
        "def count_metric(t):\n"
        "    return t.count()\n"
    )
    project = semantic_project_factory({"sales/_domain.py": model})
    project.load()

    brief = project.prepare_derived_metric(numerator="sales.revenue", weight="sales.count_metric")

    assert brief.status == "sufficient"
    assert brief.composition_kind == "weighted_average"
    assert brief.authoring_template is not None
    assert "ms.weighted_average(" in brief.authoring_template
    assert "sales.revenue" in brief.authoring_template
    assert "sales.count_metric" in brief.authoring_template


def test_module_prepare_domain_uses_loaded_project(tmp_path, monkeypatch) -> None:
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
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
    assert [profile.name for profile in brief.column_profiles] == ["order_id", "dt"]
    assert "dt" in brief.time_like_columns
    assert brief.scan.partition_resolution == "unpruned"


def test_prepare_entity_warns_on_shadowing_column(tmp_path: Path, semantic_project_factory) -> None:
    import ibis

    import marivo.datasource as md

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.create_table(
        "queries",
        {"query_id": [1, 2], "schema": ["web", "mobile"], "region": ["US", "EU"]},
    )
    con.disconnect()

    md.register(
        md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )
    project = semantic_project_factory(
        {"analytics/_domain.py": "import marivo.semantic as ms\nms.domain(name='analytics')\n"},
        workspace_dir=tmp_path,
    )
    project.load()

    brief = project.prepare_entity(
        datasource="warehouse",
        source=md.table("queries"),
        domain="analytics",
        scope=md.ScanScope(partition=None, max_rows=10),
    )

    shadow_issues = [i for i in brief.issues if i.kind == "ibis_attribute_shadowing"]
    assert len(shadow_issues) == 1
    assert shadow_issues[0].severity == "warning"
    assert shadow_issues[0].refs == ("analytics.queries.schema",)
    assert 'table["schema"]' in shadow_issues[0].message


def test_prepare_dimension_blocks_unknown_column(tmp_path: Path, semantic_project_factory) -> None:
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

    brief = project.prepare_dimension(
        entity="sales.orders",
        column="missing_col",
        scope=md.ScanScope(partition=None),
    )

    assert brief.status == "blocked"
    assert brief.issues[0].kind == "missing_column"


def test_prepare_dimension_warns_on_shadowing_column(
    tmp_path: Path, semantic_project_factory
) -> None:
    import ibis

    import marivo.datasource as md

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.create_table(
        "orders",
        {"order_id": [1, 2], "schema": ["web", "mobile"], "region": ["US", "EU"]},
    )
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

    # schema column should have a shadowing warning
    schema_brief = project.prepare_dimension(
        entity="sales.orders",
        column="schema",
        scope=md.ScanScope(partition=None),
    )
    assert schema_brief.column == "schema"
    assert schema_brief.status == "sufficient"  # warning does not block
    shadow_issues = [i for i in schema_brief.issues if i.kind == "ibis_attribute_shadowing"]
    assert len(shadow_issues) == 1
    assert shadow_issues[0].severity == "warning"
    assert 'table["schema"]' in shadow_issues[0].message

    # region column should have no shadowing warning
    region_brief = project.prepare_dimension(
        entity="sales.orders",
        column="region",
        scope=md.ScanScope(partition=None),
    )
    assert region_brief.column == "region"
    assert region_brief.status == "sufficient"
    shadow_issues = [i for i in region_brief.issues if i.kind == "ibis_attribute_shadowing"]
    assert len(shadow_issues) == 0


def test_prepare_time_dimension_warns_on_shadowing_column(
    tmp_path: Path, semantic_project_factory
) -> None:
    import ibis

    import marivo.datasource as md

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.create_table(
        "events",
        {"event_id": [1, 2], "count": [10, 20]},
    )
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
                "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
            )
        },
        workspace_dir=tmp_path,
    )
    project.load()
    project.verify_object("sales.events")

    brief = project.prepare_time_dimension(
        entity="sales.events",
        column="count",
        scope=md.ScanScope(partition=None),
    )

    assert brief.column == "count"
    assert brief.status == "sufficient"  # warning does not block
    shadow_issues = [i for i in brief.issues if i.kind == "ibis_attribute_shadowing"]
    assert len(shadow_issues) == 1
    assert shadow_issues[0].severity == "warning"
    assert 'table["count"]' in shadow_issues[0].message


def test_prepare_metric_warns_on_shadowing_measure_column(
    tmp_path: Path, semantic_project_factory
) -> None:
    import ibis

    import marivo.datasource as md

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.create_table(
        "orders",
        {"order_id": [1, 2], "amount": [100.0, 200.0], "info": ["a", "b"]},
    )
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

    brief = project.prepare_metric(
        entity="sales.orders",
        measure_columns=("info", "amount"),
        scope=md.ScanScope(partition=None),
    )

    assert brief.status == "sufficient"  # warning does not block
    shadow_issues = [i for i in brief.issues if i.kind == "ibis_attribute_shadowing"]
    assert len(shadow_issues) == 1
    assert shadow_issues[0].severity == "warning"
    assert 'table["info"]' in shadow_issues[0].message


# ---------------------------------------------------------------------------
# Relationship and cross-entity prepare APIs (Task 8)
# ---------------------------------------------------------------------------


def test_prepare_relationship_uses_keys_parameter(tmp_path: Path, semantic_project_factory) -> None:
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
        keys=[("sales.orders.customer_id", "sales.customers.customer_id")],
        scope=md.ScanScope(partition=None),
    )

    assert brief.status == "sufficient"
    assert brief.keys == (
        ms.JoinKey(from_key="sales.orders.customer_id", to_key="sales.customers.customer_id"),
    )
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


def test_prepare_derived_metric_ratio_unit_hint_is_one(
    semantic_project_factory,
) -> None:
    model = (
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales')\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "@ms.metric(entities=[orders], additivity='additive', unit='CNY')\n"
        "def revenue(t):\n"
        "    return t.amount.sum()\n"
        "@ms.metric(entities=[orders], additivity='additive', unit='CNY')\n"
        "def cost(t):\n"
        "    return t.cost.sum()\n"
    )
    project = semantic_project_factory({"sales/_domain.py": model})
    project.load()

    brief = project.prepare_derived_metric(numerator="sales.revenue", denominator="sales.cost")

    assert brief.unit_hint == "1"


def test_prepare_derived_metric_weighted_average_unit_hint_keeps_value(
    semantic_project_factory,
) -> None:
    model = (
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales')\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "@ms.metric(entities=[orders], additivity='additive', unit='CNY')\n"
        "def price(t):\n"
        "    return t.price.mean()\n"
        "@ms.metric(entities=[orders], additivity='additive')\n"
        "def qty(t):\n"
        "    return t.qty.sum()\n"
    )
    project = semantic_project_factory({"sales/_domain.py": model})
    project.load()

    brief = project.prepare_derived_metric(numerator="sales.price", weight="sales.qty")

    assert brief.unit_hint == "CNY"


def test_prepare_domain_includes_measure_in_object_counts(
    semantic_project_factory,
):
    project = semantic_project_factory(
        {"sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n"}
    )
    project.load()

    brief = project.prepare_domain(name="sales")

    assert "measure" in brief.existing_domains[0].object_counts


def test_prepare_measure_profiles_numeric_column(tmp_path: Path, semantic_project_factory) -> None:
    import ibis

    import marivo.datasource as md

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.create_table("orders", {"order_id": [1, 2], "amount": [100.0, 200.0]})
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

    brief = project.prepare_measure(
        entity="sales.orders",
        column="amount",
        scope=md.ScanScope(partition=None),
    )

    assert brief.entity == "sales.orders"
    assert brief.column == "amount"
    assert brief.additivity_hint in ("additive", "non_additive", "semi_additive", "unknown")
    assert brief.status == "sufficient"


def test_prepare_time_dimension_emits_variant_candidates(
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

    brief = project.prepare_time_dimension(
        entity="sales.orders",
        column="dt",
        scope=md.ScanScope(partition=None),
    )

    assert len(brief.detected_formats) > 0
    first = brief.detected_formats[0]
    assert first.variant == "strptime"
    assert first.strptime_format is not None
    assert first.match_rate == 1.0


def test_prepare_measure_blocks_unknown_column(tmp_path: Path, semantic_project_factory) -> None:
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

    brief = project.prepare_measure(
        entity="sales.orders",
        column="missing_col",
        scope=md.ScanScope(partition=None),
    )

    assert brief.status == "blocked"
    assert brief.issues[0].kind == "missing_column"
