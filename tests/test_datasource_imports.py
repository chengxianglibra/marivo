"""Dependency and IR tests for marivo.datasource."""

from __future__ import annotations

import subprocess
import sys


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


def test_load_datasources_returns_datasource_ir(tmp_path) -> None:
    import marivo.datasource as md
    from marivo.datasource.ir import DatasourceIR

    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )

    result = md.load_datasources(datasource_dir)

    assert result.errors == ()
    assert isinstance(result.datasources[0], DatasourceIR)
    assert result.datasources[0].name == "warehouse"
