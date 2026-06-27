"""Dependency and IR tests for marivo.datasource."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_datasource_import_does_not_load_semantic_or_analysis() -> None:
    code = """
import sys
for name in list(sys.modules):
    if name == "marivo.datasource" or name.startswith("marivo.datasource."):
        del sys.modules[name]
    if name == "marivo.semantic" or name.startswith("marivo.semantic."):
        del sys.modules[name]
    if name == "marivo.analysis" or name.startswith("marivo.analysis."):
        del sys.modules[name]

import marivo.datasource as md

assert md.duckdb is not None
assert "marivo.semantic" not in sys.modules
assert "marivo.analysis" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_datasource_help_import_does_not_load_semantic_or_analysis() -> None:
    code = """
import sys
for name in list(sys.modules):
    if name == "marivo.datasource" or name.startswith("marivo.datasource."):
        del sys.modules[name]
    if name == "marivo.semantic" or name.startswith("marivo.semantic."):
        del sys.modules[name]
    if name == "marivo.analysis" or name.startswith("marivo.analysis."):
        del sys.modules[name]

import marivo.datasource as md

assert "marivo.datasource" in md.help_text()
assert "marivo.semantic" not in sys.modules
assert "marivo.analysis" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_load_datasources_returns_datasource_ir(tmp_path: Path) -> None:
    from marivo.datasource.ir import DatasourceIR
    from marivo.datasource.loader import load_datasources

    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
        encoding="utf-8",
    )

    result = load_datasources(datasource_dir)

    assert result.errors == ()
    assert isinstance(result.datasources[0], DatasourceIR)
    assert result.datasources[0].name == "warehouse"


def test_trino_spec_splits_literal_fields_and_env_refs() -> None:
    from marivo.datasource.authoring import _TrinoSpec

    spec = _TrinoSpec(
        name="warehouse",
        host="trino.example",
        catalog="hive",
        auth_env="TRINO_AUTH",
    )

    assert spec.name == "warehouse"
    assert spec.backend_type == "trino"
    assert spec.fields == {"host": "trino.example", "catalog": "hive"}
    assert spec.env_refs == {"auth": "TRINO_AUTH"}


def test_datasource_ref_uses_global_short_name() -> None:
    import marivo.datasource as md

    ref = md.ref("warehouse")

    assert ref.id == "warehouse"
    assert repr(ref) == "DatasourceRef('warehouse')"


def test_datasource_public_exports() -> None:
    import marivo.datasource as md

    for name in (
        "DatasourceCatalog",
        "DatasourceRef",
        "JoinSide",
        "ScanScope",
        "TableSource",
        "EntityDiscoveryResult",
        "DimensionDiscoveryResult",
        "TimeDimensionDiscoveryResult",
        "MeasureDiscoveryResult",
        "RelationshipDiscoveryResult",
        "DimensionValueDiscoveryResult",
        "RawSqlResult",
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
        "load",
        "table",
        "parquet",
        "csv",
        "duckdb",
        "trino",
        "mysql",
        "postgres",
        "clickhouse",
    ):
        assert hasattr(md, name), f"marivo.datasource missing export: {name}"

    for removed in (
        "file",
        "inspect_table",
        "inspect_source",
        "inspect_columns",
        "probe_join_keys",
        "ColumnInspection",
        "JoinKeyProbe",
    ):
        assert not hasattr(md, removed), (
            f"marivo.datasource still exposes removed public name: {removed}"
        )
