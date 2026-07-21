"""Dependency and IR tests for marivo.datasource."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import marivo.semantic as ms

# A fresh interpreter is required for import-isolation checks: the test worker
# already has marivo.semantic/analysis loaded via conftest, so the probe cannot
# run in-process. Both isolation assertions share this single subprocess to
# avoid paying the marivo.datasource import (~4s) twice.
_ISOLATION_PROBE_CODE = """
import json, sys
for name in list(sys.modules):
    if (name == "marivo.datasource" or name.startswith("marivo.datasource.")
            or name == "marivo.semantic" or name.startswith("marivo.semantic.")
            or name == "marivo.analysis" or name.startswith("marivo.analysis.")
            or name == "marivo.skills" or name.startswith("marivo.skills.")):
        del sys.modules[name]

import marivo.datasource as md

after_import = {
    "duckdb_present": md.duckdb is not None,
    "semantic_loaded": "marivo.semantic" in sys.modules,
    "analysis_loaded": "marivo.analysis" in sys.modules,
    "packaged_skills_loaded": "marivo.skills" in sys.modules,
}
help_text = md.help_text()
after_help = {
    "help_mentions_datasource": "marivo.datasource" in help_text,
    "semantic_loaded": "marivo.semantic" in sys.modules,
    "analysis_loaded": "marivo.analysis" in sys.modules,
    "packaged_skills_loaded": "marivo.skills" in sys.modules,
}
print(json.dumps({"after_import": after_import, "after_help": after_help}))
"""


@pytest.fixture(scope="module")
def _datasource_isolation_probe() -> dict:
    """Fresh-process probe of marivo.datasource import isolation (shared)."""
    proc = subprocess.run(
        [sys.executable, "-c", _ISOLATION_PROBE_CODE],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def test_datasource_import_does_not_load_semantic_analysis_or_packaged_skills(
    _datasource_isolation_probe: dict,
) -> None:
    probe = _datasource_isolation_probe["after_import"]
    assert probe["duckdb_present"]
    assert not probe["semantic_loaded"]
    assert not probe["analysis_loaded"]
    assert not probe["packaged_skills_loaded"]


def test_datasource_help_does_not_load_semantic_analysis_or_packaged_skills(
    _datasource_isolation_probe: dict,
) -> None:
    probe = _datasource_isolation_probe["after_help"]
    assert probe["help_mentions_datasource"]
    assert not probe["semantic_loaded"]
    assert not probe["analysis_loaded"]
    assert not probe["packaged_skills_loaded"]


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
    import marivo.datasource as md

    spec = md.TrinoSpec(
        name="warehouse",
        host="trino.example",
        catalog="hive",
        auth_env="TRINO_AUTH",
    )

    assert spec.name == "warehouse"
    assert spec.backend_type == "trino"
    assert spec.fields == {"host": "trino.example", "catalog": "hive"}
    assert spec.env_refs == {"auth": "TRINO_AUTH"}


def test_datasource_ref_uses_kind_qualified_identity() -> None:
    ref = ms.ref.datasource("warehouse")

    assert ref.kind is ms.SemanticKind.DATASOURCE
    assert ref.path == "warehouse"
    assert ref.name == "warehouse"
    assert repr(ref) == "Ref[datasource](datasource:warehouse)"


def test_datasource_surface_does_not_own_ref_identity() -> None:
    import marivo.datasource as md

    assert not hasattr(md, "Ref")
    assert not hasattr(md, "DatasourceRef")
    assert not hasattr(md, "ref")


def test_datasource_public_exports() -> None:
    import marivo.datasource as md

    for name in (
        "DatasourceCatalog",
        "DatasourceSpec",
        "DuckDBSpec",
        "TrinoSpec",
        "MySQLSpec",
        "PostgresSpec",
        "SQLiteSpec",
        "ClickHouseSpec",
        "DiscoverySnapshot",
        "SourceInspection",
        "PartitionScope",
        "TableSource",
        "UnprunedScope",
        "inspect",
        "raw_sql",
        "partition",
        "unpruned",
        "load",
        "table",
        "parquet",
        "csv",
        "json",
        "duckdb",
        "trino",
        "mysql",
        "postgres",
        "sqlite",
        "clickhouse",
    ):
        assert hasattr(md, name), f"marivo.datasource missing export: {name}"

    for removed in (
        "file",
        "inspect_source",
        "inspect_columns",
        "probe_join_keys",
        "ColumnInspection",
        "JoinKeyProbe",
        "DiscoveryResult",
        "RawSqlResult",
        "EntityDiscoveryResult",
        "DimensionDiscoveryResult",
        "TimeDimensionDiscoveryResult",
        "MeasureDiscoveryResult",
        "RelationshipDiscoveryResult",
        "DimensionValueDiscoveryResult",
        "ColumnDiscovery",
        "TimeColumnDiscovery",
        "DimensionValueFact",
        "DiscoveryEvidenceEntry",
        "DiscoveryIssue",
        "DiscoverySignal",
        "FormatCandidate",
        "PrimaryKeyCandidate",
        "TimeValueRange",
        "DatasourceResult",
        "JoinSide",
        "ScanScope",
        "preview",
        "inspect_table",
        "inspect_partitions",
        "discover_entity",
        "discover_dimensions",
        "discover_time_dimensions",
        "discover_measures",
        "discover_relationship",
        "discover_dimension_values",
    ):
        assert not hasattr(md, removed), (
            f"marivo.datasource still exposes removed public name: {removed}"
        )
