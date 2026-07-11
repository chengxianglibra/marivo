"""Tests for semantic verification behavior."""

from pathlib import Path

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import DuckDBSpec


def _duckdb_project_with_entity(tmp_path: Path, semantic_project_factory):
    """Create a project with a single entity backed by DuckDB."""
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table(
        "orders",
        {
            "order_id": [1, 2],
            "amount": [100, 200],
            "region": ["US", "EU"],
            "dt": ["20260610", "20260611"],
        },
    )
    con.disconnect()
    md.register(
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )
    return semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=md.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day', parse=ms.strptime('%Y%m%d'))\n"
                "def dt(orders):\n"
                "    return orders.dt\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            )
        },
        workspace_dir=tmp_path,
    )


# -- Static entity verification tests -----------------------------------------


def test_verify_object_entity_is_static_without_audit_side_effects(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    result = project.verify_object(ms.ref("entity.sales.orders"))

    assert result.status == "passed"
    assert result.kind == "entity"
    assert result.validation_level == "static"
    assert result.runtime_checked is False
    assert not hasattr(result, "scan")
    assert not hasattr(result, "auto" + "_recorded")
    assert not (Path(project.state_root) / "evidence").exists()


def test_verify_object_entity_uses_current_source_declaration(
    tmp_path: Path, semantic_project_factory
) -> None:
    project = _duckdb_project_with_entity(tmp_path, semantic_project_factory)

    first = project.verify_object(ms.ref("entity.sales.orders"))
    assert first.status == "passed"
    assert first.runtime_checked is False

    # Rewrite the entity with a different source table name
    import ibis

    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders_v2", {"order_id": [1], "amount": [100], "region": ["US"]})
    con.disconnect()

    project2 = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang')\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), "
                "source=md.table('orders_v2'))\n"
            )
        },
        workspace_dir=tmp_path,
    )

    second = project2.verify_object(ms.ref("entity.sales.orders"))
    assert second.status == "passed"
    assert second.validation_level == "static"
    assert second.runtime_checked is False
    assert not hasattr(second, "scan")
    assert not (Path(project2.state_root) / "evidence").exists()


# -- verify_object with project load failure ----------------------------------


def test_verify_object_reports_project_load_failed(semantic_project_factory) -> None:
    """When a file fails to load, verify_object returns project_load_failed
    and preserves the requested typed-ref kind."""
    # Create a project whose metrics file calls a non-existent ms.max()
    project = semantic_project_factory(
        {
            "cdn/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='cdn', owner='Mina Zhang')\n"
            ),
            "cdn/broken.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.max()  # does not exist\n"
            ),
        },
        load=False,
    )

    result = project.verify_object(ms.ref("metric.cdn.total_billing_bandwidth"))

    assert result.status == "failed"
    assert result.kind == "metric"
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.kind == "project_load_failed"
    assert "project failed to load" in issue.message
    # The message should surface the real error, not "was not found"
    assert "was not found" not in issue.message
    # The real error mentions the broken file
    assert "broken.py" in issue.message


def test_verify_object_reports_load_errors_for_metric_ref(semantic_project_factory) -> None:
    """verify_object on a metric ref still gets project_load_failed when the
    project cannot load — not the old static_check_failed / 'not found'."""
    project = semantic_project_factory(
        {
            "cdn/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='cdn', owner='Mina Zhang')\n"
            ),
            "cdn/bad.py": "raise RuntimeError('intentional load error')\n",
        },
        load=False,
    )

    result = project.verify_object(ms.ref("metric.cdn.some_metric"))

    assert result.status == "failed"
    assert result.kind == "metric"
    assert result.issues[0].kind == "project_load_failed"
    assert "intentional load error" in result.issues[0].message


def test_verify_object_measure_returns_passed(semantic_project_factory) -> None:
    model = (
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Mina Zhang')\n"
        "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))\n"
        "@ms.measure(entity=orders, additivity='additive')\n"
        "def amount(orders):\n"
        "    return orders.amount\n"
    )
    project = semantic_project_factory({"sales/_domain.py": model})
    project.load()

    result = project.verify_object(ms.ref("measure.sales.orders.amount"))

    assert result.status == "passed"
    assert result.kind == "measure"


def test_verify_object_known_ref_still_not_found_when_loaded(
    semantic_project_factory,
) -> None:
    """When the project loads successfully but the ref doesn't exist,
    verify_object still uses static_check_failed (not project_load_failed)."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
        },
        load=True,
    )
    assert project.is_ready()

    result = project.verify_object(ms.ref("metric.sales.nonexistent_metric"))

    assert result.status == "failed"
    assert result.kind == "metric"
    assert result.issues[0].kind == "static_check_failed"
    assert "was not found" in result.issues[0].message
