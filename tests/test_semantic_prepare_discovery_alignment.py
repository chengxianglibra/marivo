"""Semantic prepare alignment with datasource discovery public APIs."""

from __future__ import annotations

from pathlib import Path

import ibis

import marivo.datasource as md
from marivo.datasource.authoring import _DuckDBSpec
from marivo.semantic.reader import SemanticProject


def _register_orders(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table(
        "orders",
        {
            "order_id": [1, 2, 3, 4],
            "customer_id": [10, 20, 20, 30],
            "status": ["paid", "paid", "void", ""],
            "dt": ["20260610", "20260611", "20260612", "20260613"],
            "amount": [10.0, -5.0, 0.0, None],
        },
    )
    con.disconnect()
    md.register(_DuckDBSpec(name="warehouse", path=str(db_path)), project_root=project_root)


def _write_project(project_root: Path, body: str) -> Path:
    semantic_dir = project_root / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True, exist_ok=True)
    domain_file = semantic_dir / "_domain.py"
    domain_file.write_text(body, encoding="utf-8")
    return domain_file


def test_prepare_entity_matches_discover_entity_core_scan_evidence(tmp_path: Path) -> None:
    _register_orders(tmp_path)
    _write_project(
        tmp_path,
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales')\n",
    )
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    source = md.table("orders")
    scope = md.unpruned(max_rows=20)

    discovery = md.discover_entity(
        md.ref("warehouse"),
        source,
        scope=scope,
        project_root=tmp_path,
    )
    brief = project.prepare_entity(
        datasource="warehouse",
        source=source,
        domain="sales",
        scope=scope,
    )

    assert brief.status == "sufficient"
    assert brief.table.table == discovery.table_metadata.table
    assert brief.scan.rows_scanned == discovery.scan.rows_scanned
    assert brief.scan.partition_resolution == discovery.scan.partition_resolution
    assert [profile.name for profile in brief.column_profiles] == [
        profile.name for profile in discovery.column_profiles
    ]


def test_prepare_field_briefs_match_discovery_profiles_after_entity_verify(
    tmp_path: Path,
) -> None:
    _register_orders(tmp_path)
    domain_file = _write_project(
        tmp_path,
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales')\n"
        "warehouse = md.ref('warehouse')\n"
        "orders = ms.entity("
        "name='orders', datasource=warehouse, source=ms.table('orders'), "
        "primary_key=['order_id'])\n",
    )
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    verify = project.verify_object("sales.orders", scope=md.unpruned(max_rows=20))
    assert verify.status == "passed"

    source = md.table("orders")
    scope = md.unpruned(max_rows=20)
    dimensions = md.discover_dimensions(
        md.ref("warehouse"),
        source,
        columns=("status",),
        scope=scope,
        project_root=tmp_path,
    )
    times = md.discover_time_dimensions(
        md.ref("warehouse"),
        source,
        columns=("dt",),
        scope=scope,
        project_root=tmp_path,
    )
    measures = md.discover_measures(
        md.ref("warehouse"),
        source,
        columns=("amount",),
        scope=scope,
        project_root=tmp_path,
    )

    dim_brief = project.prepare_dimension(entity="sales.orders", column="status", scope=scope)
    time_brief = project.prepare_time_dimension(entity="sales.orders", column="dt", scope=scope)
    measure_brief = project.prepare_measure(entity="sales.orders", column="amount", scope=scope)

    assert dim_brief.profile.name == dimensions.columns[0].profile.name
    assert dim_brief.profile.distinct_count == dimensions.columns[0].profile.distinct_count
    assert time_brief.profile.name == times.columns[0].profile.name
    assert bool(time_brief.detected_formats) == bool(times.columns[0].detected_formats)
    assert measure_brief.profile.name == measures.columns[0].profile.name
    assert measure_brief.profile.negative_count == measures.columns[0].profile.negative_count

    domain_file.write_text(domain_file.read_text(encoding="utf-8"), encoding="utf-8")


def test_public_removed_names_stay_absent_while_prepare_still_works(tmp_path: Path) -> None:
    _register_orders(tmp_path)
    _write_project(
        tmp_path,
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales')\n",
    )
    project = SemanticProject(workspace_dir=tmp_path)
    project.load()

    for removed in (
        "inspect_table",
        "inspect_source",
        "inspect_columns",
        "probe_join_keys",
        "ColumnInspection",
        "JoinKeyProbe",
    ):
        assert not hasattr(md, removed), removed

    brief = project.prepare_entity(
        datasource="warehouse",
        source=md.table("orders"),
        domain="sales",
        scope=md.unpruned(max_rows=20),
    )
    assert brief.status == "sufficient"
