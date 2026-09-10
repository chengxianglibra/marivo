"""Built-in datasource discovery, ownership, and real file-source execution."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import ibis
import pandas as pd
import pytest

import marivo
import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource import store
from marivo.datasource.backends import build_backend
from marivo.datasource.errors import (
    DatasourceDuplicateError,
    DatasourceFieldInvalidError,
    DatasourceMissingError,
)
from marivo.datasource.loader import load_datasources
from marivo.semantic.loader import load_project
from tests.shared_fixtures import bootstrap_sales_project_from_template


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def test_empty_project_discovery_is_pure(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("discovery must not open a backend")

    monkeypatch.setattr(ibis.duckdb, "connect", forbidden_connect)
    assert md.list().ids() == ["default"]
    catalog = md.load(workspace_dir=project_root)
    assert catalog.list().ids() == ["default"]
    assert catalog.get("default").backend_type == "duckdb"
    description = catalog.describe("default")
    assert description.literal_fields == {"path": ":memory:", "read_only": False}
    assert description.env_refs == {}
    semantic = ms.load(workspace_dir=project_root)
    entry = semantic.require(ms.ref.datasource("default"))
    assert entry.details().fields == description.literal_fields
    for result in (catalog, md.list(), catalog.get("default"), description, entry.details()):
        assert "Built-in" in result.render()
        assert "non-persistent" in result.render()
    assert list(project_root.iterdir()) == []
    assert load_datasources(project_root / "models" / "datasources").datasources == ()


def test_definitions_are_not_shared_between_projects(project_root: Path) -> None:
    first = store.load_all(project_root)["default"]
    first.fields["path"] = "changed.duckdb"
    first.env_refs["password"] = "UNEXPECTED"
    for root in (project_root, project_root / "another"):
        fresh = store.load_all(root)["default"]
        assert fresh.fields["path"] == ":memory:"
        assert fresh.env_refs == {}


def test_layered_loading_injects_default_once(project_root: Path) -> None:
    external = project_root / "shared" / "models"
    (external / "datasources").mkdir(parents=True)
    (external / "semantic").mkdir()
    (external / "datasources" / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse')\n"
    )
    (project_root / "marivo.toml").write_text('[semantic]\nlayer_paths = ["shared/models"]\n')
    assert set(store.load_all_layered(project_root)) == {"default", "warehouse"}
    result = load_project(
        project_root / "models" / "semantic",
        models_roots=(project_root / "models", external),
    )
    assert result.errors == ()
    assert [item.name for item in result.datasource_irs].count("default") == 1
    catalog = ms.load(workspace_dir=project_root)
    assert catalog.require(ms.ref.datasource("default")).name == "default"
    assert catalog.require(ms.ref.datasource("warehouse")).name == "warehouse"


@pytest.mark.parametrize("backend", ["duckdb", "sqlite"])
def test_reserved_registration_fails_before_writes(project_root: Path, backend: str) -> None:
    spec = md.duckdb(name="default") if backend == "duckdb" else md.sqlite(name="default")
    with pytest.raises(DatasourceDuplicateError) as caught:
        md.register(spec)
    assert caught.value.received == "default"
    assert caught.value.repair is not None
    assert "Rename" in caught.value.repair.action
    assert list(project_root.iterdir()) == []


def test_builtin_cannot_be_removed(project_root: Path) -> None:
    with pytest.raises(DatasourceFieldInvalidError) as caught:
        md.remove("default")
    assert caught.value.received == "default"
    assert caught.value.repair is not None
    assert caught.value.repair.help_target.canonical_id == "remove"
    assert list(project_root.iterdir()) == []


@pytest.mark.parametrize("external", [False, True])
def test_authored_default_rejected_by_both_loaders(project_root: Path, external: bool) -> None:
    models = project_root / ("shared" if external else "models")
    declarations = models / "datasources"
    declarations.mkdir(parents=True)
    (models / "semantic").mkdir()
    authored = declarations / "custom.py"
    authored.write_text("import marivo.datasource as md\nmd.duckdb(name='default')\n")
    if external:
        (project_root / "marivo.toml").write_text('[semantic]\nlayer_paths = ["shared"]\n')
    with pytest.raises(DatasourceDuplicateError, match="reserved"):
        store.load_all_layered(project_root)
    result = load_project(
        project_root / "models" / "semantic",
        models_roots=(project_root / "models", models) if external else (models,),
    )
    assert any("reserved" in error.message and "Rename" in error.hint for error in result.errors)
    assert authored.read_text() == "import marivo.datasource as md\nmd.duckdb(name='default')\n"


def test_unknown_source_does_not_fall_back(project_root: Path) -> None:
    for operation in (md.describe, md.connect, md.load().get):
        with pytest.raises(DatasourceMissingError):
            operation("missing")


def test_managed_connections_have_independent_memory(project_root: Path) -> None:
    assert md.test(ms.ref.datasource("default")).ok
    with md.connect("default") as first, md.connect("default") as second:
        first.create_table("only_first", ibis.memtable({"value": [7]}))
        assert "only_first" in first.list_tables()
        assert "only_first" not in second.list_tables()
    with md.connect("default") as reopened:
        assert "only_first" not in reopened.list_tables()
    assert not (project_root / "models").exists()


@pytest.mark.parametrize("format", ["csv", "parquet", "json"])
def test_builtin_file_sources_and_semantic_observe(project_root: Path, format: str) -> None:
    bootstrap_sales_project_from_template(project_root, with_time=False)
    (project_root / "models" / "datasources" / "warehouse.py").unlink()
    (project_root / "models" / "datasources").rmdir()
    rows = pd.DataFrame({"region": ["US", "CN"], "amount": [10, 20]})
    path = project_root / f"orders.{format}"
    schema = {"region": "string", "amount": "int64"}
    if format == "csv":
        rows.to_csv(path, index=False)
        source = md.csv(str(path), schema=schema)
        source_code = f"md.csv({str(path)!r}, schema={schema!r})"
    elif format == "json":
        rows.to_json(path, orient="records")
        source = md.json(str(path), schema=schema)
        source_code = f"md.json({str(path)!r}, schema={schema!r})"
    else:
        rows.to_parquet(path)
        source = md.parquet(str(path))
        source_code = f"md.parquet({str(path)!r})"
    definition = project_root / "models" / "semantic" / "sales" / "datasets.py"
    definition.write_text(
        definition.read_text()
        .replace("ms.ref.datasource('warehouse')", "ms.ref.datasource('default')")
        .replace("md.table('orders')", source_code)
    )
    catalog = ms.load(workspace_dir=project_root)
    catalog.require(ms.ref.entity("sales.orders"))
    inspection = md.inspect(ms.ref.datasource("default"), source)
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("region", "amount"),
        persist_values=True,
    )
    assert snapshot.coverage.observed_row_count == 2
    assert sorted(row["amount"] for row in snapshot.retained_values) == [10, 20]
    session = mv.session.get_or_create(name=f"builtin-{format}")
    try:
        frame = session.observe(ms.ref.metric("sales.revenue"))
        data = frame.to_pandas()
        assert data.shape == (1, 1)
        assert data.iloc[0, 0] == 30
    finally:
        session.close()
    assert not (project_root / "models" / "datasources").exists()


def test_new_process_rereads_file_without_registration(project_root: Path) -> None:
    source = project_root / "orders.csv"
    source.write_text("amount\n10\n20\n")
    code = """
import marivo.datasource as md
import marivo.semantic as ms
snapshot = md.inspect(
    ms.ref.datasource('default'), md.csv('orders.csv', schema={'amount': 'int64'})
).sample(scope=md.unpruned(max_rows=2, timeout_seconds=30), columns=('amount',), persist_values=True)
assert sum(row['amount'] for row in snapshot.retained_values) == 30
assert md.list().ids() == ['default']
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (project_root / "models").exists()


def test_builtin_help_is_reachable(capsys: pytest.CaptureFixture[str]) -> None:
    for target in ("authoring", "register", "remove", "list", "describe"):
        marivo.help(f"datasource.{target}")
        output = capsys.readouterr().out
        assert "default" in output
        assert "Unknown" not in output


def test_builtin_read_only_acquisition_rejects_database_writes(project_root: Path) -> None:
    built = build_backend(store.load_all(project_root)["default"], read_only=True)
    try:
        with pytest.raises(Exception, match="read-only"):
            built.backend.raw_sql("CREATE TABLE forbidden(value INTEGER)")
    finally:
        built.lease.close()
