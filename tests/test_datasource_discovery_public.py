"""Public datasource discovery entry point tests."""

from __future__ import annotations

from pathlib import Path

import ibis

import marivo.datasource as md
from marivo.datasource.authoring import _DuckDBSpec


def _register_orders(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table(
        "orders",
        {
            "order_id": [1, 2, 3, 4],
            "customer_id": [10, 20, 20, 30],
            "status": ["paid", "paid", "", "void"],
            "amount": [10.0, -5.0, 0.0, None],
            "created_at": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
        },
    )
    con.create_table("customers", {"customer_id": [10, 20, 20, 40]})
    con.disconnect()
    md.register(_DuckDBSpec(name="warehouse", path=str(db_path)), project_root=project_root)


def test_public_discover_column_families_return_display_results(tmp_path: Path) -> None:
    _register_orders(tmp_path)
    warehouse = md.ref("warehouse")
    source = md.table("orders")
    scope = md.unpruned(max_rows=10)

    entity = md.discover_entity(warehouse, source, scope=scope, project_root=tmp_path)
    assert isinstance(entity, md.DiscoveryResult)
    entity_render = entity.render()
    assert "primary key evidence:" in entity_render
    assert "order_id" in entity_render
    assert "sampled_unique" in entity_render
    assert "created_at" in entity_render
    assert "distinct=" in entity_render
    assert "nulls=" in entity_render

    dimensions = md.discover_dimensions(
        warehouse,
        source,
        columns=("status",),
        scope=scope,
        project_root=tmp_path,
    )
    assert isinstance(dimensions, md.DiscoveryResult)
    dimensions_render = dimensions.render()
    assert "status" in dimensions_render
    assert "dimension_empty_values_present" in dimensions_render
    assert ".columns" not in dimensions_render
    assert ".profile" not in dimensions_render

    times = md.discover_time_dimensions(
        warehouse,
        source,
        columns=("created_at",),
        scope=scope,
        project_root=tmp_path,
    )
    assert isinstance(times, md.DiscoveryResult)
    times_render = times.render()
    assert "time column evidence:" in times_render
    assert "created_at" in times_render
    assert "%Y-%m-%d" in times_render
    assert "range=" in times_render
    assert ".columns" not in times_render

    measures = md.discover_measures(
        warehouse,
        source,
        columns=("amount",),
        scope=scope,
        project_root=tmp_path,
    )
    assert isinstance(measures, md.DiscoveryResult)
    measures_render = measures.render()
    assert "amount" in measures_render
    assert "measure_numeric_type" in measures_render
    assert ".columns" not in measures_render


def test_public_discover_measures_reports_missing_column_not_type_mismatch(
    tmp_path: Path,
) -> None:
    _register_orders(tmp_path)

    result = md.discover_measures(
        md.ref("warehouse"),
        md.table("orders"),
        columns=("elapsed_time_millis",),
        scope=md.unpruned(max_rows=10),
        project_root=tmp_path,
    )

    rendered = result.render()
    assert "elapsed_time_millis" in rendered
    assert "column_not_found" in rendered
    assert "measure_non_numeric_type" not in rendered


def test_public_discover_relationship_replaces_probe_join_keys(tmp_path: Path) -> None:
    _register_orders(tmp_path)
    warehouse = md.ref("warehouse")

    result = md.discover_relationship(
        from_side=md.JoinSide(warehouse, md.table("orders"), columns=("customer_id",)),
        to_side=md.JoinSide(warehouse, md.table("customers"), columns=("customer_id",)),
        scope=md.unpruned(max_rows=100),
        key_sample_size=10,
        project_root=tmp_path,
    )

    assert isinstance(result, md.DiscoveryResult)
    rendered = result.render()
    assert "sampled_keys=3" in rendered
    assert "matched=2" in rendered
    assert "cardinality=many_to_one" in rendered
    assert "key type evidence:" in rendered
    assert ".evidence" not in rendered


def test_public_discover_dimension_values_are_bounded_runtime_evidence(tmp_path: Path) -> None:
    _register_orders(tmp_path)

    result = md.discover_dimension_values(
        md.ref("warehouse"),
        md.table("orders"),
        column="status",
        scope=md.unpruned(max_rows=10),
        limit=2,
        project_root=tmp_path,
    )

    assert isinstance(result, md.DiscoveryResult)
    rendered = result.render()
    assert "paid" in rendered
    assert "not_exhaustive" in rendered
    assert "dimension_values_truncated" in rendered
    assert ".values" not in rendered
    assert ".issues" not in rendered


def test_datasource_public_surface_exposes_discovery_not_inspection() -> None:
    public_names = set(md.__all__)
    assert {
        "discover_entity",
        "discover_dimensions",
        "discover_time_dimensions",
        "discover_measures",
        "discover_relationship",
        "discover_dimension_values",
        "raw_sql",
        "latest_partition",
        "partition",
        "unpruned",
    }.issubset(public_names)
    assert (
        not {
            "inspect_table",
            "inspect_source",
            "inspect_columns",
            "probe_join_keys",
            "ColumnInspection",
            "JoinKeyProbe",
        }
        & public_names
    )

    text = md.help_text()
    assert "md.discover_entity" in text
    assert "md.raw_sql" in text
    assert "md.inspect_columns" not in text
