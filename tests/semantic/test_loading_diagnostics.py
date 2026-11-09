"""Original execution diagnostics and standard Python module lifetime."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import marivo
import marivo.semantic as ms
from marivo._help import help as marivo_help
from marivo.datasource.errors import DatasourceDuplicateError, DatasourceLoadError
from marivo.datasource.loader import load_datasources
from marivo.semantic.check import _error_to_dict, main
from marivo.semantic.errors import SemanticError, SemanticLoadFailed
from marivo.semantic.loader import load_project


@pytest.fixture(autouse=True)
def isolate_authored_imports() -> Iterator[None]:
    def owned(name: str) -> bool:
        return (
            name == "datasources"
            or name.startswith("datasources.")
            or name.startswith("_marivo_datasource_")
            or name.startswith("_marivo_semantic_")
        )

    saved = {name: module for name, module in sys.modules.copy().items() if owned(name)}
    for name in saved:
        del sys.modules[name]
    try:
        yield
    finally:
        for name in list(sys.modules):
            if owned(name):
                del sys.modules[name]
        sys.modules.update(saved)


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / "models" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


def _load_failure(
    root: Path, surface: str, source: str
) -> tuple[DatasourceLoadError | SemanticError, Path]:
    if surface == "datasource":
        path = _write(root, "datasources/a.py", source)
        error = load_datasources(path.parent).errors[0]
        assert isinstance(error, DatasourceLoadError)
    else:
        _write(
            root,
            "semantic/demo/_domain.py",
            "import marivo.semantic as ms\nms.domain(name='demo', owner='tester')\n",
        )
        path = _write(root, "semantic/demo/a.py", source)
        error = load_project(root / "models" / "semantic").errors[0]
    return error, path


@pytest.mark.parametrize("surface", ["datasource", "semantic"])
def test_execution_preserves_complete_exception_chain(tmp_path: Path, surface: str) -> None:
    error, path = _load_failure(
        tmp_path,
        surface,
        "secret_local = 'diagnostic-local-value'\n"
        "try:\n    raise ValueError('root failure')\n"
        "except ValueError as cause:\n    raise RuntimeError('outer failure') from cause\n",
    )
    assert error.exception_type == "RuntimeError"
    assert isinstance(error.__cause__, RuntimeError)
    assert isinstance(error.__cause__.__cause__, ValueError)
    assert error.traceback is not None
    assert str(path.resolve()) in error.traceback or str(path) in error.traceback
    assert str(path) in error.message or str(path.resolve()) in error.message
    if isinstance(error, SemanticError):
        assert error.location is not None and error.location.line == 5
    else:
        assert error.location == str(path)


@pytest.mark.parametrize("surface", ["datasource", "semantic"])
def test_syntax_failure_reports_original_location(tmp_path: Path, surface: str) -> None:
    error, path = _load_failure(tmp_path, surface, "# header\ndef broken(:\n    pass\n")
    assert error.exception_type == "SyntaxError"
    assert isinstance(error.__cause__, SyntaxError)
    assert error.__cause__.lineno == 2
    assert error.traceback is not None and "SyntaxError" in error.traceback
    assert error.repair is not None
    if isinstance(error, SemanticError):
        assert error.location is not None and error.location.line == 2
        assert str(path.resolve()) == error.location.file
    else:
        assert error.location == str(path)


def test_datasource_diagnostics_survive_semantic_and_cli_conversion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "datasources/a.py", "raise ValueError('source failure')\n")
    with pytest.raises(SemanticLoadFailed) as caught:
        ms.load(workspace_dir=tmp_path)
    error = caught.value.errors[0]
    assert isinstance(error.__cause__, DatasourceLoadError)
    assert isinstance(error.__cause__.__cause__, ValueError)
    assert error.repair is error.__cause__.repair
    assert error.traceback == error.__cause__.traceback
    payload = _error_to_dict(error)
    assert payload["exception_type"] == "ValueError"
    assert payload["traceback"] == error.traceback
    assert main(["--workspace-dir", str(tmp_path), "--format", "json"]) == 1
    cli = json.loads(capsys.readouterr().out)
    assert cli["errors"][0]["traceback"] == error.traceback
    assert main(["--workspace-dir", str(tmp_path)]) == 1
    plain = _error_to_dict(SemanticError(kind="test", message="static failure"))


def test_multiple_execution_failures_have_separate_tracebacks(tmp_path: Path) -> None:
    _write(tmp_path, "datasources/a.py", "raise ValueError('first failure')\n")
    _write(tmp_path, "datasources/b.py", "raise RuntimeError('second failure')\n")
    with pytest.raises(SemanticLoadFailed) as caught:
        ms.load(workspace_dir=tmp_path)
    assert len(caught.value.errors) == 2
    rendered = str(caught.value)
    assert all(error.traceback is not None for error in caught.value.errors)


def test_relative_import_reload_does_not_reuse_deleted_source(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "datasources/a.py",
        "import marivo.datasource as md\nmd.duckdb(name='local')\nfrom .local import declare\ndeclare()\n",
    )
    local = _write(
        tmp_path,
        "datasources/local.py",
        "import marivo.datasource as md\ndef declare():\n    return md.duckdb(name='local')\n",
    )
    first = load_project(tmp_path / "models" / "semantic")
    duplicate = next(error for error in first.errors if error.kind == "duplicate_name")
    assert isinstance(duplicate.__cause__, DatasourceDuplicateError)
    assert duplicate.__cause__.declaration_paths == (str(a.resolve()), str(local.resolve()))
    cached = {
        name: module
        for name, module in sys.modules.copy().items()
        if getattr(module, "__file__", None) == str(local)
    }
    assert cached
    caches = tuple((local.parent / "__pycache__").glob("local.*.pyc"))
    assert caches
    before = {path: path.read_bytes() for path in caches}
    local.unlink()
    second = load_project(tmp_path / "models" / "semantic")
    assert second.status == "errored"
    assert second.errors[0].exception_type == "ModuleNotFoundError"
    assert isinstance(second.errors[0].__cause__, DatasourceLoadError)
    assert isinstance(second.errors[0].__cause__.__cause__, ModuleNotFoundError)
    assert all(error.kind != "duplicate_name" for error in second.errors)
    assert all(sys.modules[name] is module for name, module in cached.items())
    assert {path: path.read_bytes() for path in caches} == before


def test_orphan_bytecode_is_ignored_and_real_duplicates_still_fail(tmp_path: Path) -> None:
    a = _write(
        tmp_path, "datasources/a.py", "import marivo.datasource as md\nmd.duckdb(name='local')\n"
    )
    local = _write(tmp_path, "datasources/local.py", a.read_text())
    first = load_datasources(a.parent)
    duplicate = first.errors[0]
    assert isinstance(duplicate, DatasourceDuplicateError)
    assert duplicate.declaration_paths == (str(a), str(local))
    caches = tuple((a.parent / "__pycache__").glob("local.*.pyc"))
    assert caches
    before = {path: path.read_bytes() for path in caches}
    local.unlink()
    second = load_datasources(a.parent)
    assert not second.errors
    assert [datasource.name for datasource in second.datasources] == ["local"]
    assert {path: path.read_bytes() for path in caches} == before


def test_real_import_recursion_retains_importlib_traceback(tmp_path: Path) -> None:
    datasource_root = tmp_path / "datasource"
    semantic_root = tmp_path / "semantic"
    directory = "datasources"
    _write(
        semantic_root,
        "semantic/demo/_domain.py",
        "import marivo.semantic as ms\nms.domain(name='demo', owner='tester')\n",
    )
    for index in range(100):
        _write(datasource_root, f"{directory}/m{index:03}.py", f"from . import m{index + 1:03}\n")
        _write(
            semantic_root,
            f"semantic/demo/m{index:03}.py",
            f"from . import m{index + 1:03}\n",
        )
    code = """import json, sys
from pathlib import Path
from marivo.datasource.loader import load_datasources
from marivo.semantic.loader import load_project
root = Path(sys.argv[1])
sys.setrecursionlimit(150)
results = {
    'datasource': load_datasources(root / 'datasource' / 'models' / 'datasources'),
    'semantic': load_project(root / 'semantic' / 'models' / 'semantic'),
}
diagnostics = {}
for surface, result in results.items():
    error = next(e for e in result.errors if e.exception_type == 'RecursionError')
    diagnostics[surface] = {
        'traceback': error.traceback,
        'repair': getattr(error, 'repair', None).action if getattr(error, 'repair', None) else None,
        'hint': getattr(error, 'hint', None),
        'rendered': str(error),
    }
print(json.dumps(diagnostics))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    diagnostics = json.loads(result.stdout)
    for surface in ("datasource", "semantic"):
        diagnostic = diagnostics[surface]
        assert "RecursionError" in diagnostic["traceback"]
        assert "importlib" in diagnostic["traceback"]


@pytest.mark.parametrize("surface", ["datasource", "semantic"])
def test_live_load_help_discloses_restart_and_execution_diagnostics(
    capsys: pytest.CaptureFixture[str], surface: str
) -> None:
    assert marivo.help is marivo_help
    marivo_help(f"{surface}.load")
    help_text = capsys.readouterr().out
