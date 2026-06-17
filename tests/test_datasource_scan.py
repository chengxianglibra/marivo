"""Tests for marivo.datasource scan DTOs and source constructors."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import _DuckDBSpec
from marivo.datasource.ir import CsvSourceIR, ParquetSourceIR, TableSourceIR


def test_scan_scope_defaults_are_agent_safe() -> None:
    scope = md.ScanScope()

    assert scope.partition == "latest"
    assert scope.max_rows == 1000
    assert scope.max_columns == 100
    assert scope.timeout_seconds == 30
    with pytest.raises(FrozenInstanceError):
        scope.max_rows = 10  # type: ignore[misc]


def test_datasource_source_constructors_match_semantic_aliases() -> None:
    table = md.table("orders", database="sales_mart")
    csv_file = md.csv("/tmp/orders.csv", delimiter=",")
    parquet_file = md.parquet("/tmp/orders.parquet", hive_partitioning=True)

    assert isinstance(table, TableSourceIR)
    assert table == ms.table("orders", database="sales_mart")
    assert isinstance(csv_file, CsvSourceIR)
    assert csv_file == ms.csv("/tmp/orders.csv", delimiter=",")
    assert isinstance(parquet_file, ParquetSourceIR)
    assert parquet_file == ms.parquet("/tmp/orders.parquet", hive_partitioning=True)


def test_scan_report_render_is_bounded() -> None:
    report = md.ScanReport(
        partition_used={"dt": "20260612"},
        partition_resolution="explicit",
        rows_scanned=20,
        columns_scanned=("status", "amount"),
        truncated=False,
        elapsed_seconds=0.02,
        warnings=(),
    )

    assert repr(report) == (
        "<ScanReport rows=20 columns=2 partition=explicit; call .show() to inspect>"
    )
    assert "dt=20260612" in report.render()


def test_inspect_table_accepts_structured_source(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    import ibis

    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"id": [1, 2], "status": ["paid", "void"]})
    con.disconnect()

    md.register(
        _DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )

    metadata = md.inspect_table("warehouse", md.table("orders"), project_root=tmp_path)

    assert metadata.table == "orders"
    assert [column.name for column in metadata.columns] == ["id", "status"]
    assert metadata.partitions is None or metadata.partitions == ()


def test_inspect_columns_profiles_selected_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    import ibis

    con = ibis.duckdb.connect(db_path)
    con.create_table(
        "orders",
        {"id": [1, 2, 3], "status": ["paid", "paid", ""], "amount": [10, 20, None]},
    )
    con.disconnect()
    md.register(
        _DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )

    inspection = md.inspect_columns(
        "warehouse",
        md.table("orders"),
        columns=("status", "amount"),
        scope=md.ScanScope(partition=None, max_rows=2, max_columns=5),
        project_root=tmp_path,
    )

    assert inspection.scan.partition_resolution == "unpruned"
    assert inspection.scan.rows_scanned == 2
    assert inspection.scan.columns_scanned == ("status", "amount")
    status = inspection.profiles[0]
    assert status.name == "status"
    assert status.distinct_count == 1
    assert status.top_values == (("paid", 2),)


def test_probe_join_keys_reports_match_and_cardinality(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    import ibis

    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"customer_id": [1, 2, 3, 4]})
    con.create_table("customers", {"customer_id": [1, 2, 2, 5]})
    con.disconnect()
    md.register(
        _DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )

    probe = md.probe_join_keys(
        from_side=md.JoinSide("warehouse", md.table("orders"), columns=("customer_id",)),
        to_side=md.JoinSide("warehouse", md.table("customers"), columns=("customer_id",)),
        scope=md.ScanScope(partition=None, max_rows=100),
        project_root=tmp_path,
        key_sample_size=10,
    )

    assert probe.sampled_key_count == 4
    assert probe.matched_key_count == 2
    assert probe.match_rate == 0.5
    assert probe.max_rows_per_key == 2
    assert probe.cardinality_estimate == "many_to_one"


def test_inspect_columns_warns_on_column_truncation(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    import ibis

    con = ibis.duckdb.connect(db_path)
    # Create a wide table with 105 columns to exceed the default max_columns=100.
    data: dict[str, list[int]] = {f"col_{i:03d}": [1, 2] for i in range(105)}
    con.create_table("wide_table", data)
    con.disconnect()
    md.register(
        _DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )

    inspection = md.inspect_columns(
        "warehouse",
        md.table("wide_table"),
        scope=md.ScanScope(partition=None, max_rows=2),
        project_root=tmp_path,
    )

    # Only the first 100 columns are profiled.
    assert len(inspection.profiles) == 100
    # The scan report carries a truncation warning.
    assert len(inspection.scan.warnings) >= 1
    warning = inspection.scan.warnings[0]
    assert "max_columns=100" in warning
    assert "5 columns not profiled" in warning
    assert "col_100" in warning  # first omitted column
    assert "ScanScope(max_columns=105)" in warning
