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

assert md.datasource is not None
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

assert md.help(format="json")["surface"] == "marivo.datasource"
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
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )

    result = load_datasources(datasource_dir)

    assert result.errors == ()
    assert isinstance(result.datasources[0], DatasourceIR)
    assert result.datasources[0].name == "warehouse"


def test_datasource_spec_splits_literal_fields_and_env_refs() -> None:
    import marivo.datasource as md

    spec = md.DatasourceSpec(
        name="warehouse",
        backend_type="trino",
        host="trino.example",
        catalog="hive",
        password_env="TRINO_PASSWORD",
    )

    assert spec.name == "warehouse"
    assert spec.backend_type == "trino"
    assert spec.fields == {"host": "trino.example", "catalog": "hive"}
    assert spec.env_refs == {"password": "TRINO_PASSWORD"}


def test_datasource_ref_uses_global_short_name() -> None:
    import marivo.datasource as md

    ref = md.ref("warehouse")

    assert ref.semantic_id == "warehouse"
    assert ref.name == "warehouse"
    assert repr(ref) == "DatasourceRef('warehouse')"


def test_datasource_public_exports() -> None:
    import marivo.datasource as md

    for name in (
        "ScanScope",
        "ScanReport",
        "ColumnInspection",
        "ColumnProfile",
        "DatasourceCatalog",
        "JoinSide",
        "JoinKeyProbe",
        "load",
        "table",
        "parquet",
        "csv",
    ):
        assert hasattr(md, name), f"marivo.datasource missing export: {name}"

    # md.file has been removed from the public surface
    assert not hasattr(md, "file")
