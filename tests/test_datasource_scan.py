"""Tests for marivo.datasource scan DTOs and source constructors."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import DuckDBSpec
from marivo.datasource.ir import CsvSourceIR, ParquetSourceIR, TableSourceIR
from marivo.datasource.manage import (
    _inspect_columns,
    _probe_join_keys,
)
from marivo.datasource.metadata import (
    _inspect_source,
)
from marivo.datasource.scan import ColumnInspection, ColumnProfile, ScanReport
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES


def test_scan_scope_defaults_are_agent_safe() -> None:
    scope = md.ScanScope()

    assert scope.partition is None
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
    report = ScanReport(
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
    assert report.render() == "\n".join(
        [
            "ScanReport rows=20 columns=2 partition=explicit",
            "status: partition=dt=20260612 truncated=False warnings=none",
            "columns: status | amount",
            "available:",
            "- .render()",
            "- .show()",
        ]
    )


def test_scan_report_long_warnings_default_render_is_bounded() -> None:
    report = ScanReport(
        partition_used=None,
        partition_resolution="none",
        rows_scanned=10,
        columns_scanned=("status",),
        truncated=False,
        elapsed_seconds=0.02,
        warnings=tuple(f"warning {index}: {'x' * 1000}" for index in range(20)),
    )

    rendered = report.render()

    assert len(rendered.encode("utf-8")) <= _DEFAULT_MAX_OUTPUT_BYTES
    assert "status: partition=none truncated=False warnings=20" in rendered
    assert "output truncated" in rendered
    assert "available:" in rendered


def test_column_profile_and_inspection_render_shared_card_shape() -> None:
    profile = ColumnProfile(
        name="status",
        data_type="string",
        nullable=True,
        comment="Order status",
        null_count=1,
        empty_count=0,
        distinct_count=2,
        top_values=(("paid", 2),),
        sample_values=("paid", "void"),
        min_value=None,
        max_value=None,
        non_null_count=3,
        distinct_ratio=0.67,
        top_value_concentration=0.67,
        type_family="string",
    )

    assert profile.render() == "\n".join(
        [
            "ColumnProfile column=status type=string family=string",
            (
                "status: type=string family=string nullable=True nulls=1 empty=0 "
                "distinct=2 non_null=3"
            ),
            "columns: fact | value",
            "preview:",
            "comment | Order status",
            "range | none..none",
            "top_values | paid:2",
            "sample_values | paid, void",
            "distinct_ratio | 0.67",
            "top_value_concentration | 0.67",
            "available:",
            "- .render()",
            "- .show()",
        ]
    )

    inspection = ColumnInspection(
        datasource="warehouse",
        source=md.table("orders"),
        profiles=(profile,),
        scan=ScanReport(
            partition_used=None,
            partition_resolution="none",
            rows_scanned=3,
            columns_scanned=("status",),
            truncated=False,
            elapsed_seconds=0.01,
            warnings=(),
        ),
    )

    assert inspection.render() == "\n".join(
        [
            "ColumnInspection datasource=warehouse columns=1",
            "columns: status",
            "available:",
            "- .render()",
            "- .show()",
        ]
    )


def test_inspect_table_accepts_structured_source(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    import ibis

    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"id": [1, 2], "status": ["paid", "void"]})
    con.disconnect()

    md.register(
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )

    metadata = _inspect_source("warehouse", source=md.table("orders"), project_root=tmp_path)

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
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )

    inspection = _inspect_columns(
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
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )

    probe = _probe_join_keys(
        from_side=md.JoinSide(
            md.ref("datasource.warehouse"), md.table("orders"), columns=("customer_id",)
        ),
        to_side=md.JoinSide(
            md.ref("datasource.warehouse"), md.table("customers"), columns=("customer_id",)
        ),
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
        DuckDBSpec(name="warehouse", path=str(db_path)),
        project_root=tmp_path,
    )

    inspection = _inspect_columns(
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


def test_join_side_uses_datasource_ref_and_table_source() -> None:
    side = md.JoinSide(md.ref("datasource.warehouse"), md.table("orders"), columns=("customer_id",))

    assert side.datasource == md.ref("datasource.warehouse")
    assert side.source == md.table("orders")
    assert side.columns == ("customer_id",)


def test_json_source_constructor_matches_semantic_alias() -> None:
    from marivo.datasource.ir import JsonSourceIR

    default = md.json("data/events/*.json")
    explicit = md.json("data/events.json", format="array")

    assert isinstance(default, JsonSourceIR)
    assert default == ms.json("data/events/*.json")
    assert default.format == "auto"
    assert explicit.to_dict() == {
        "kind": "json",
        "path": "data/events.json",
        "format": "array",
    }
