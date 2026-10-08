"""One project boundary for declaration loading, inspection, and connections."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from typing import Protocol

import duckdb
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource import loader, store
from marivo.datasource.errors import (
    DatasourceDuplicateError,
    DatasourceLoadError,
    DatasourceMissingError,
)
from marivo.semantic.errors import SemanticLoadFailed
from marivo.semantic.reader import SemanticProject


class _ProjectFactory(Protocol):
    def __call__(
        self,
        files: dict[str, str],
        load: bool = True,
        models: list[str] | None = None,
        workspace_dir: Path | None = None,
    ) -> SemanticProject: ...


def _project(root: Path, factory: _ProjectFactory, name: str = "warehouse") -> Path:
    root.mkdir(parents=True)
    database = root / "warehouse.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount INTEGER)")
        connection.execute("INSERT INTO orders VALUES (1, 10), (2, 20)")
    factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            f"md.sqlite(name={name!r}, path={str(database)!r}, read_only=True)\n"
        },
        load=False,
        workspace_dir=root,
    )
    return root


def test_project_selection_and_bound_catalog_methods(
    tmp_path: Path,
    semantic_project_factory: _ProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = _project(tmp_path / "cwd", semantic_project_factory, "cwd_source")
    env = _project(tmp_path / "env", semantic_project_factory, "env_source")
    explicit = _project(tmp_path / "explicit", semantic_project_factory, "explicit_source")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(env))

    assert ms.load().datasources.refs == (
        ms.ref.datasource("default"),
        ms.ref.datasource("env_source"),
    )
    assert md.load().get("env_source").name == "env_source"
    assert md.inspect(ms.ref.datasource("env_source"), md.table("orders"))._project_root == env
    catalog = md.load(workspace_dir=explicit)
    inspection = md.inspect(
        ms.ref.datasource("explicit_source"), md.table("orders"), workspace_dir=explicit
    )
    assert inspection._project_root == explicit
    assert catalog.describe("explicit_source").literal_fields["path"] == str(
        explicit / "warehouse.sqlite"
    )
    assert catalog.test("explicit_source").ok
    with pytest.raises(DatasourceMissingError) as exc_info:
        catalog.get("missing")
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.candidates == ("default", "explicit_source")
    with pytest.raises(DatasourceMissingError) as description_error:
        catalog.describe("missing")
    assert description_error.value.repair is not None
    assert description_error.value.repair.candidates == ("default", "explicit_source")
    failure = catalog.test("missing")
    assert not failure.ok
    assert failure.repair is not None and failure.repair.candidates == (
        "default",
        "explicit_source",
    )

    monkeypatch.delenv("MARIVO_PROJECT_ROOT")
    nested = cwd / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert md.inspect(ms.ref.datasource("cwd_source"), md.table("orders"))._project_root == cwd
    (cwd / "marivo.toml").unlink()
    monkeypatch.chdir(cwd)
    assert md.inspect(ms.ref.datasource("cwd_source"), md.table("orders"))._project_root == cwd


def test_external_datasource_is_usable_without_a_local_copy(
    tmp_path: Path,
    semantic_project_factory: _ProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = _project(tmp_path / "external", semantic_project_factory)
    local = tmp_path / "local"
    local.mkdir()
    (local / "marivo.toml").write_text('[semantic]\nlayer_paths=["../external/models"]\n')
    monkeypatch.chdir(local)
    monkeypatch.delenv("MARIVO_PROJECT_ROOT", raising=False)
    semantic = ms.load()
    datasources = md.load()
    assert semantic.datasources.refs == (
        ms.ref.datasource("default"),
        ms.ref.datasource("warehouse"),
    )
    assert [item.name for item in datasources.list()] == ["default", "warehouse"]
    assert datasources.describe("warehouse").literal_fields["path"] == str(
        external / "warehouse.sqlite"
    )
    assert datasources.test("warehouse").ok
    assert md.test(ms.ref.datasource("warehouse")).ok
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
    assert inspection.schema[0].name == "id"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path / "other"))
    inspection.partitions()
    sample = inspection.sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30), columns=("amount",), persist_values=True
    )
    assert sorted(row["amount"] for row in sample.retained_values) == [10, 20]
    assert not (local / "models" / "datasources").exists()


@pytest.mark.parametrize("scope", ["local", "external"])
def test_duplicate_declarations_have_the_same_diagnostic_owner(
    tmp_path: Path,
    semantic_project_factory: _ProjectFactory,
    scope: str,
) -> None:
    root = _project(tmp_path / "local", semantic_project_factory)
    first = root / "models" / "datasources" / "warehouse.py"
    if scope == "external":
        external = _project(tmp_path / "external", semantic_project_factory)
        second = external / "models" / "datasources" / "warehouse.py"
        (root / "marivo.toml").write_text('[semantic]\nlayer_paths=["../external/models"]\n')
    else:
        second = first.with_name("other.py")
        second.write_text(first.read_text())
    with pytest.raises(DatasourceDuplicateError) as datasource_error:
        md.load(workspace_dir=root).list()
    with pytest.raises(SemanticLoadFailed) as semantic_error:
        ms.load(workspace_dir=root)
    assert len(semantic_error.value.errors) == 1
    assert semantic_error.value.errors[0].message == datasource_error.value.message
    assert str(first) in datasource_error.value.message
    assert str(second) in datasource_error.value.message


@pytest.mark.parametrize(
    "invalid", ["local", "repeated", "missing", "file", "datasources", "semantic"]
)
def test_model_root_validation_is_shared(
    tmp_path: Path,
    semantic_project_factory: _ProjectFactory,
    invalid: str,
) -> None:
    root = _project(tmp_path / "local", semantic_project_factory)
    external = tmp_path / "external" / "models"
    external.mkdir(parents=True)
    (external / "datasources").mkdir()
    (external / "semantic").mkdir()
    roots = [str(external)]
    if invalid == "local":
        roots = [str(root / "models")]
    elif invalid == "repeated":
        roots *= 2
    elif invalid == "missing":
        roots = [str(tmp_path / "missing")]
    elif invalid == "file":
        file = tmp_path / "file"
        file.write_text("not a directory")
        roots = [str(file)]
    else:
        (external / invalid).rmdir()
    (root / "marivo.toml").write_text(f"[semantic]\nlayer_paths={roots!r}\n")
    with pytest.raises(DatasourceLoadError) as datasource_error:
        md.load(workspace_dir=root).list()
    with pytest.raises(SemanticLoadFailed) as semantic_error:
        ms.load(workspace_dir=root)
    assert semantic_error.value.errors[0].kind == "invalid_project"
    assert semantic_error.value.errors[0].message == datasource_error.value.message


def test_semantic_loading_retains_errors_from_every_datasource_root(
    tmp_path: Path,
    semantic_project_factory: _ProjectFactory,
) -> None:
    root = _project(tmp_path / "local", semantic_project_factory, "local")
    external = _project(tmp_path / "external", semantic_project_factory, "external")
    (root / "marivo.toml").write_text('[semantic]\nlayer_paths=["../external/models"]\n')
    for project in (root, external):
        (project / "models" / "datasources" / "bad.py").write_text(
            "raise RuntimeError('broken declaration')\n"
        )
    with pytest.raises(SemanticLoadFailed) as exc_info:
        ms.load(workspace_dir=root)
    assert len(exc_info.value.errors) == 2
    assert {error.location_label for error in exc_info.value.errors} == {
        str(project / "models" / "datasources" / "bad.py") for project in (root, external)
    }


def test_declaration_errors_are_preserved_without_collected_objects(tmp_path: Path) -> None:
    directory = tmp_path / "models" / "datasources"
    directory.mkdir(parents=True)
    (directory / "bad.py").write_text(
        "from marivo.datasource.errors import DatasourceDuplicateError\n"
        "raise DatasourceDuplicateError(message='authored conflict', expected='unique name', received='warehouse')\n"
    )
    with pytest.raises(DatasourceDuplicateError, match="authored conflict"):
        md.load(workspace_dir=tmp_path).list()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        ms.load(workspace_dir=tmp_path)
    assert len(exc_info.value.errors) == 1
    assert exc_info.value.errors[0].kind == "duplicate_name"
    assert exc_info.value.errors[0].message == "authored conflict"


@pytest.mark.parametrize("relative", [".", "models", "models/semantic"])
def test_low_level_loader_rejects_wrong_path_level(
    tmp_path: Path,
    semantic_project_factory: _ProjectFactory,
    relative: str,
) -> None:
    root = _project(tmp_path / "project", semantic_project_factory)
    result = loader.load_datasources(root / relative)
    assert not result.datasources
    assert len(result.errors) == 1
    error = result.errors[0]
    assert isinstance(error, DatasourceLoadError)
    assert error.expected == str(root / "models" / "datasources")
    assert error.repair is not None and "md.load(workspace_dir=...)" in error.repair.action


def test_exact_arbitrary_directory_and_empty_projects_remain_valid(tmp_path: Path) -> None:
    directory = tmp_path / "declarations"
    directory.mkdir()
    (directory / "source.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse')\n"
    )
    assert [item.name for item in loader.load_datasources(directory).datasources] == ["warehouse"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not loader.load_datasources(empty).errors
    assert not loader.load_datasources(empty / "missing").errors
    assert md.load(workspace_dir=empty).list().ids() == ["default"]
    assert ms.load(workspace_dir=empty).datasources.refs == (ms.ref.datasource("default"),)


def test_registration_saves_without_executing_other_declarations(tmp_path: Path) -> None:
    directory = tmp_path / "models" / "datasources"
    directory.mkdir(parents=True)
    marker = tmp_path / "executed"
    (directory / "bad.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError('broken')\n"
    )
    spec = md.duckdb(name="warehouse", path="saved.duckdb")
    assert md.register(spec, project_root=tmp_path).name == "warehouse"
    stored = store.save_one(spec, tmp_path)
    assert not marker.exists()
    path = directory / "warehouse.py"
    call = ast.parse(path.read_text()).body[2]
    assert stored.location.file == str(path)
    assert stored.location.line == call.lineno
    assert stored.fields == spec.fields
    with pytest.raises(DatasourceLoadError, match="broken"):
        md.load(workspace_dir=tmp_path).list()
    assert marker.exists()


@pytest.mark.parametrize("kind", ["table", "csv", "json", "parquet"])
def test_inspect_executes_declarations_once_and_reload_keeps_old_catalog(
    tmp_path: Path,
    semantic_project_factory: _ProjectFactory,
    kind: str,
) -> None:
    marker = tmp_path / "loads.txt"
    first = tmp_path / "first.duckdb"
    second = tmp_path / "second.duckdb"
    for path in (first, second):
        with duckdb.connect(str(path)) as connection:
            column = "id" if path == first else "new_id"
            connection.execute(f"CREATE TABLE orders ({column} INTEGER)")
            connection.execute("INSERT INTO orders VALUES (1)")
    for extension, body in (("csv", "id\n1\n"), ("json", '[{"id":1}]')):
        (tmp_path / f"orders.{extension}").write_text(body)
    parquet = tmp_path / "orders.parquet"
    with duckdb.connect(str(first)) as connection:
        connection.execute("COPY orders TO ? (FORMAT PARQUET)", [str(parquet)])
    declaration = "import marivo.datasource as md\nfrom pathlib import Path\n" + (
        f"with Path({str(marker)!r}).open('a') as log:\n    log.write('loaded\\n')\n"
    )
    semantic_project_factory(
        {
            "datasources/warehouse.py": declaration
            + f"md.duckdb(name='warehouse', path={str(first)!r})\n"
        },
        load=False,
    )
    catalog = ms.load(workspace_dir=tmp_path)
    old = catalog.datasources.get("warehouse").details()
    marker.write_text("")
    source = {
        "table": md.table("orders"),
        "csv": md.csv(str(tmp_path / "orders.csv")),
        "json": md.json(str(tmp_path / "orders.json")),
        "parquet": md.parquet(str(parquet)),
    }[kind]
    inspected = md.inspect(ms.ref.datasource("warehouse"), source, workspace_dir=tmp_path)
    assert inspected.schema[0].name == "id"
    assert marker.read_text() == "loaded\n"
    (tmp_path / "models" / "datasources" / "warehouse.py").write_text(
        declaration + f"md.duckdb(name='warehouse', path={str(second)!r})\n"
    )
    assert old.fields["path"] == str(first)
    assert catalog.datasources.get("warehouse").details().fields["path"] == str(first)
    assert ms.load(workspace_dir=tmp_path).datasources.get("warehouse").details().fields[
        "path"
    ] == str(second)
    marker.write_text("")
    inspected = md.inspect(ms.ref.datasource("warehouse"), source, workspace_dir=tmp_path)
    assert inspected.schema[0].name == ("new_id" if kind == "table" else "id")
    assert marker.read_text() == "loaded\n"
