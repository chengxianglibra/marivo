"""Metadata inspection uses bound Ibis schema and discloses unavailable facts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
from marivo.datasource.authoring import DuckDBSpec, SQLiteSpec
from marivo.datasource.engines import ENGINE_PROFILES
from marivo.datasource.engines.base import MetadataInspectRequest, schema_only_metadata_inspect
from marivo.datasource.errors import DatasourceMetadataError
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.metadata import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
    TablePhysicalProfile,
    UniqueConstraintMetadata,
    _inspect_source,
    inspect_table,
)
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, AgentResult


@pytest.mark.parametrize("backend_type", tuple(ENGINE_PROFILES))
def test_metadata_profiles_use_ibis_schema_without_text_submission(backend_type: str) -> None:
    class Backend:
        def raw_sql(self, _statement: str) -> None:
            raise AssertionError("metadata submitted text SQL")

    datasource = DatasourceIR(
        semantic_id="warehouse",
        name="warehouse",
        backend_type=backend_type,
        fields={},
        env_refs={},
        ai_context=AiContextIR(),
        python_symbol="warehouse",
        location=DatasourceSourceLocation(file="/tmp/datasource.py", line=1),
    )
    request = MetadataInspectRequest(
        datasource="warehouse",
        backend=Backend(),  # type: ignore[arg-type]
        table="orders",
        database=None,
        table_expr=ibis.table({"id": "int64", "amount": "decimal(18, 2)"}, name="orders"),
        include_partitions=True,
        datasource_ir=datasource,
    )
    profile = ENGINE_PROFILES[backend_type]
    assert profile.metadata.inspect_table is schema_only_metadata_inspect
    metadata = profile.metadata.inspect_table(request)
    assert tuple(column.name for column in metadata.columns) == ("id", "amount")
    assert all(column.nullable is None and column.comment is None for column in metadata.columns)
    assert metadata.partition_state == "unknown"
    assert metadata.is_view is None
    assert metadata.primary_keys == ()
    assert {warning.kind for warning in metadata.warnings} >= {
        "comments_unavailable",
        "partitions_unavailable",
        "primary_keys_unavailable",
        "view_unavailable",
        "nullable_unavailable",
        "physical_profile_unavailable",
        "schema_only_fallback",
    }


def test_duckdb_inspect_reads_schema_and_discloses_optional_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount DECIMAL(18, 2))")
    con.disconnect()
    md.register(DuckDBSpec(name="warehouse", path=str(db_path)), project_root=tmp_path)

    metadata = inspect_table("warehouse", table="orders", project_root=tmp_path)

    assert tuple(column.name for column in metadata.columns) == ("id", "amount")
    assert metadata.primary_keys == ()
    assert metadata.physical_profile is None
    assert metadata.is_view is None
    assert any(warning.kind == "primary_keys_unavailable" for warning in metadata.warnings)
    assert {warning.kind for warning in metadata.warnings} >= {
        "view_unavailable",
        "nullable_unavailable",
        "physical_profile_unavailable",
    }


def test_sqlite_inspect_reads_schema_without_catalog_query(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.sqlite"
    with sqlite3.connect(db_path) as con:
        con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL)")
    md.register(SQLiteSpec(name="warehouse", path=str(db_path)), project_root=tmp_path)

    metadata = inspect_table("warehouse", table="orders", project_root=tmp_path)

    assert tuple(column.name for column in metadata.columns) == ("id", "amount")
    assert metadata.partition_state == "unknown"
    assert any(warning.kind == "schema_only_fallback" for warning in metadata.warnings)


def test_file_inspection_knows_it_is_not_a_view(tmp_path: Path) -> None:
    source_path = tmp_path / "orders.csv"
    source_path.write_text("id,amount\n1,2\n")
    md.register(DuckDBSpec(name="warehouse", path=":memory:"), project_root=tmp_path)

    metadata = _inspect_source("warehouse", source=md.csv(str(source_path)), project_root=tmp_path)

    assert metadata.is_view is False
    assert "view_unavailable" not in {warning.kind for warning in metadata.warnings}


def test_missing_datasource_rejects_metadata_inspection(tmp_path: Path) -> None:
    with pytest.raises(DatasourceMetadataError, match="not configured"):
        inspect_table("missing", table="orders", project_root=tmp_path)


def test_table_metadata_json_and_render_are_bounded() -> None:
    metadata = TableMetadata(
        datasource="warehouse",
        table="orders",
        database=("analytics", "public"),
        backend_type="duckdb",
        comment="Order fact table",
        columns=(ColumnMetadata("id", "int64", False, None, 1),),
        partitions=(PartitionMetadata("date", "date", "identity", None),),
        partition_state="known",
        warnings=(MetadataWarning("comments_unavailable", "No comments"),),
        primary_keys=("id",),
        unique_constraints=(UniqueConstraintMetadata(None, ("id",), "primary"),),
        physical_profile=TablePhysicalProfile(12, "estimate", 128, "on_disk", "test"),
    )
    payload = metadata.to_dict()
    assert payload["primary_keys"] == ["id"]
    assert payload["unique_constraints"][0]["kind"] == "primary"
    assert json.loads(json.dumps(payload))["ref"] == "warehouse.analytics.public.orders"
    assert "partitions=1" in metadata.render()
    assert "Order fact table" in metadata.render()
    assert isinstance(metadata, AgentResult)
    assert "call .show() to inspect" in repr(metadata)


def test_table_metadata_default_render_cap() -> None:
    metadata = TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment="x" * (_DEFAULT_MAX_OUTPUT_BYTES * 2),
        columns=(),
        partitions=(),
        warnings=(),
    )
    rendered = metadata.render()
    assert len(rendered.encode("utf-8")) <= _DEFAULT_MAX_OUTPUT_BYTES
    assert "output truncated" in rendered
