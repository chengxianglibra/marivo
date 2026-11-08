"""Registration is a setup operation, never an authored-model loading effect."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo._authoring.loading import _current_source_loading_file
from marivo.datasource import loader, store
from marivo.datasource.errors import DatasourceError, DatasourceLoadError
from marivo.semantic.errors import SemanticError, SemanticLoadFailed

_DOMAIN = 'import marivo.semantic as ms\nms.domain(name="demo", owner="tester", default=True)\n'


def _write_file(root: Path, relative: str, body: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _assert_registration_repair(error: DatasourceError | SemanticError, name: str) -> None:
    assert error.expected is not None and "outside model loading" in error.expected
    assert error.received is not None and name in error.received
    assert error.repair is not None
    assert error.repair.kind == "reauthor"
    assert error.repair.help_target.surface == "datasource"
    assert error.repair.help_target.canonical_id == "register"


@pytest.mark.parametrize(
    ("filename", "name"),
    [("local.py", "local"), ("a.py", "local"), ("a.py", "a"), ("local.py", "warehouse")],
)
def test_loading_registration_never_rewrites_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str, name: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MARIVO_PROJECT_ROOT", raising=False)
    source = _write_file(
        tmp_path,
        f"models/datasources/{filename}",
        "# This authored source must survive loading.\n"
        "import marivo.datasource as md\n"
        f"md.register(md.duckdb(name={name!r}, path=':memory:'))\n",
    )

    with pytest.raises(DatasourceLoadError) as exc_info:
        md.load(workspace_dir=tmp_path).list()

    _assert_registration_repair(exc_info.value, name)
    assert exc_info.value.location == str(source)


def test_registration_rejects_another_project_before_any_io(tmp_path: Path) -> None:
    target = tmp_path / "other_project"
    source = _write_file(
        tmp_path,
        "models/datasources/a.py",
        "from pathlib import Path\n"
        "import marivo.datasource as md\n"
        f"md.register(md.duckdb(name='local'), project_root=Path({str(target)!r}))\n",
    )

    result = loader.load_datasources(source.parent)

    assert len(result.errors) == 1
    error = result.errors[0]
    assert isinstance(error, DatasourceLoadError)
    _assert_registration_repair(error, "local")


@pytest.mark.parametrize("filename", ["a.py", "local.py"])
@pytest.mark.parametrize("assignment", ["", "local = "])
def test_constructors_load_without_registration_or_source_changes(
    tmp_path: Path, filename: str, assignment: str
) -> None:
    _write_file(
        tmp_path,
        f"models/datasources/{filename}",
        f"import marivo.datasource as md\n{assignment}md.duckdb(name='local', path=':memory:')\n",
    )

    summaries = md.load(workspace_dir=tmp_path).list()

    assert [item.name for item in summaries] == ["default", "local"]


@pytest.mark.parametrize("entry", ["datasource", "semantic", "import"])
def test_semantic_loading_preserves_registration_repair_without_writes(
    tmp_path: Path, entry: str
) -> None:
    _write_file(tmp_path, "models/semantic/demo/_domain.py", _DOMAIN)
    declaration = "import marivo.datasource as md\nmd.register(md.duckdb(name='local'))\n"
    if entry == "semantic":
        _write_file(tmp_path, "models/datasources/a.py", "")
        _write_file(tmp_path, "models/semantic/demo/objects.py", declaration)
    else:
        _write_file(tmp_path, "models/datasources/a.py", declaration)
        if entry == "import":
            _write_file(tmp_path, "models/semantic/demo/objects.py", "from datasources import a\n")
    saved_modules = {
        name: module
        for name, module in sys.modules.copy().items()
        if name == "datasources" or name.startswith("datasources.")
    }
    for name in saved_modules:
        del sys.modules[name]
    try:
        with pytest.raises(SemanticLoadFailed) as exc_info:
            ms.load(workspace_dir=tmp_path)
    finally:
        for name in list(sys.modules):
            if name == "datasources" or name.startswith("datasources."):
                del sys.modules[name]
        sys.modules.update(saved_modules)

    errors = exc_info.value.errors
    assert len(errors) == (2 if entry == "import" else 1)
    for error in errors:
        assert error.kind == "invalid_project"
        _assert_registration_repair(error, "local")
        assert error.repair is not None
        assert error.hint == error.repair.action
        assert error.location_label in {
            str(tmp_path / "models/datasources/a.py"),
            str(tmp_path / "models/semantic/demo/objects.py"),
        }
    assert _current_source_loading_file() is None


@pytest.mark.parametrize("surface", ["datasource", "semantic"])
@pytest.mark.parametrize("fails", [False, True])
def test_loader_scope_restores_normal_registration_and_replacement(
    tmp_path: Path, surface: str, fails: bool
) -> None:
    root = tmp_path / "loaded"
    constructor = "md.duckdb(name='local')"
    _write_file(
        root,
        "models/datasources/a.py",
        "import marivo.datasource as md\n"
        + (
            f"md.register({constructor})\n"
            if fails and surface == "datasource"
            else f"{constructor}\n"
        ),
    )
    _write_file(root, "models/semantic/demo/_domain.py", _DOMAIN)
    if fails and surface == "semantic":
        _write_file(
            root,
            "models/semantic/demo/objects.py",
            f"import marivo.datasource as md\nmd.register({constructor})\n",
        )
    if surface == "datasource":
        if fails:
            with pytest.raises(DatasourceLoadError):
                md.load(workspace_dir=root).list()
        else:
            md.load(workspace_dir=root).list()
    elif fails:
        with pytest.raises(SemanticLoadFailed):
            ms.load(workspace_dir=root)
    else:
        ms.load(workspace_dir=root)
    assert _current_source_loading_file() is None

    target = tmp_path / "setup"
    context = ms.ai_context(
        business_definition="Development warehouse.", guardrails=["Local only."]
    )
    spec = md.duckdb(
        name="warehouse",
        path="first.duckdb",
        secrets=[
            md.http_bearer_secret(
                scope="https://warehouse.example/", token=md.env("WAREHOUSE_TOKEN")
            )
        ],
        ai_context=context,
    )
    assert md.register(spec, project_root=target).name == "warehouse"
    replacement = replace(spec, path="second.duckdb", ai_context=context)
    assert md.register(replacement, project_root=target).name == "warehouse"
    stored = store.load_one("warehouse", target)
    assert stored is not None
    assert stored.fields["path"] == "second.duckdb"
    assert stored.env_refs == {"http@https://warehouse.example/.token": "WAREHOUSE_TOKEN"}
    assert stored.ai_context.business_definition == "Development warehouse."
    assert stored.ai_context.guardrails == ("Local only.",)
