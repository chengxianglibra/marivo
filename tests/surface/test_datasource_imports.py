"""Dependency and IR tests for marivo.datasource."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from ibis.common.deferred import Deferred

import marivo.semantic as ms
from tests.support.refs import as_ref

# A fresh interpreter is required: the test worker already has other marivo
# surfaces loaded via conftest.
_ISOLATION_PROBE_CODE = """
import contextlib
import io
import json
import sys
for name in list(sys.modules):
    if any(name == root or name.startswith(root + ".") for root in (
        "marivo.datasource", "marivo.semantic", "marivo.analysis", "marivo.skills"
    )):
        del sys.modules[name]

import marivo
import marivo.datasource as md

def loaded():
    return {
        "semantic_loaded": "marivo.semantic" in sys.modules,
        "analysis_loaded": "marivo.analysis" in sys.modules,
        "packaged_skills_loaded": "marivo.skills" in sys.modules,
    }

after_import = {"duckdb_present": md.duckdb is not None, **loaded()}
with contextlib.redirect_stdout(io.StringIO()):
    marivo.help("datasource")
print(json.dumps({"after_import": after_import, "after_help": loaded()}))
"""


@pytest.fixture(scope="module")
def _datasource_isolation_probe() -> dict[str, dict[str, bool]]:
    """Fresh-process probe of marivo.datasource import isolation (shared)."""
    proc = subprocess.run(
        [sys.executable, "-c", _ISOLATION_PROBE_CODE],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict)
    result: dict[str, dict[str, bool]] = {}
    for name, raw_values in payload.items():
        assert isinstance(name, str) and isinstance(raw_values, dict)
        values: dict[str, bool] = {}
        for key, value in raw_values.items():
            assert isinstance(key, str) and isinstance(value, bool)
            values[key] = value
        result[name] = values
    return result


def test_datasource_import_does_not_load_semantic_analysis_or_packaged_skills(
    _datasource_isolation_probe: dict[str, dict[str, bool]],
) -> None:
    probe = _datasource_isolation_probe["after_import"]
    assert probe["duckdb_present"]
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


def test_load_datasources_rejects_required_trino_user_none(tmp_path: Path) -> None:
    from marivo.datasource.errors import DatasourceFieldInvalidError
    from marivo.datasource.loader import load_datasources

    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.trino(name='warehouse', host='trino.example', catalog='hive', user=None)\n",
        encoding="utf-8",
    )

    result = load_datasources(datasource_dir)

    assert result.datasources == ()
    assert len(result.errors) == 1
    error = result.errors[0]
    assert isinstance(error, DatasourceFieldInvalidError)
    assert error.location is not None
    assert error.location.endswith("field 'user'")
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "env"


def test_trino_spec_splits_literal_fields_and_env_refs() -> None:
    import marivo.datasource as md

    spec = md.TrinoSpec(
        name="warehouse",
        host="trino.example",
        catalog="hive",
        user=md.env("TRINO_USER"),
        auth=md.env("TRINO_AUTH"),
    )

    assert spec.name == "warehouse"
    assert spec.backend_type == "trino"
    assert spec.fields == {"host": "trino.example", "catalog": "hive"}
    assert spec.env_refs == {"user": "TRINO_USER", "auth": "TRINO_AUTH"}


def test_datasource_ref_uses_kind_qualified_identity() -> None:
    ref = ms.ref.datasource("warehouse")

    reference = as_ref(ref)
    assert reference is not None
    assert reference.kind is ms.SemanticKind.DATASOURCE
    assert reference.path == "warehouse"
    assert reference.name == "warehouse"
    assert isinstance(ref, Deferred)


def test_datasource_public_exports() -> None:
    import marivo.datasource as md

    for name in (
        "DatasourceCatalog",
        "DatasourceSpec",
        "DuckDBSpec",
        "TrinoSpec",
        "MySQLSpec",
        "PostgresSpec",
        "ClickHouseSpec",
        "DiscoverySnapshot",
        "SourceInspection",
        "TableSource",
        "inspect",
        "raw_sql",
        "partition",
        "time_range",
        "unpruned",
        "load",
        "table",
        "parquet",
        "csv",
        "json",
        "source_param",
        "duckdb",
        "trino",
        "mysql",
        "postgres",
        "clickhouse",
    ):
        assert hasattr(md, name), f"marivo.datasource missing export: {name}"


def test_datasource_help_does_not_load_semantic_analysis_or_packaged_skills(
    _datasource_isolation_probe: dict[str, dict[str, bool]],
) -> None:
    probe = _datasource_isolation_probe["after_help"]
    assert not probe["semantic_loaded"]
    assert not probe["analysis_loaded"]
    assert not probe["packaged_skills_loaded"]


def test_datasource_surface_does_not_own_ref_identity() -> None:
    import marivo.datasource as md

    assert not hasattr(md, "Ref")
    assert not hasattr(md, "DatasourceRef")
    assert not hasattr(md, "ref")
