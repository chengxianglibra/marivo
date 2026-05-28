"""Dependency and IR tests for marivo.datasource_py."""

from __future__ import annotations

import subprocess
import sys


def test_datasource_py_import_does_not_load_semantic_or_analysis() -> None:
    code = """
import sys
for name in list(sys.modules):
    if name == "marivo.datasource_py" or name.startswith("marivo.datasource_py."):
        del sys.modules[name]
    if name == "marivo.semantic_py" or name.startswith("marivo.semantic_py."):
        del sys.modules[name]
    if name == "marivo.analysis_py" or name.startswith("marivo.analysis_py."):
        del sys.modules[name]

import marivo.datasource_py as md

assert md.datasource is not None
assert "marivo.semantic_py" not in sys.modules
assert "marivo.analysis_py" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_load_datasources_returns_datasource_py_ir(tmp_path) -> None:
    import marivo.datasource_py as md
    from marivo.datasource_py.ir import DatasourceIR

    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource_py as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )

    result = md.load_datasources(datasource_dir)

    assert result.errors == ()
    assert isinstance(result.datasources[0], DatasourceIR)
    assert result.datasources[0].name == "warehouse"
