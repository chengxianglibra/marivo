"""Tests for SemanticProject.verify_object."""

from pathlib import Path

import marivo.datasource as md


def test_verify_object_static_domain_passes(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {"sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n"}
    )

    result = project.verify_object("sales")

    assert result.status == "passed"
    assert result.kind == "domain"
    assert result.scan is None


def test_verify_object_blocks_missing_datasource(tmp_path: Path, semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales')\n"
                "orders = ms.entity(name='orders', datasource='missing', source=ms.table('orders'))\n"
            )
        },
        workspace_dir=tmp_path,
    )

    result = project.verify_object("sales.orders")

    assert result.status == "failed"
    assert result.issues[0].kind == "datasource_unreachable"


def test_verify_object_scoped_entity_preview_passes(
    tmp_path: Path, semantic_project_factory
) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    import ibis

    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"order_id": [1], "dt": ["20260612"]})
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

    result = project.verify_object("sales.orders", scope=md.ScanScope(partition=None, max_rows=5))

    assert result.status == "passed"
    assert result.kind == "entity"
    assert result.scan is not None
    assert result.scan.partition_resolution == "unpruned"
