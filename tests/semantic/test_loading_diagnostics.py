"""Original execution diagnostics and standard Python module lifetime."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
import marivo.datasource as md
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
    if isinstance(error, SemanticError):
        assert error.location is not None and error.location.line == 5
    else:
        assert error.location == str(path)
        assert error.source_location is not None and error.source_location.line == 5


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
        assert error.source_location is not None and error.source_location.line == 2


def test_datasource_diagnostics_survive_semantic_and_cli_conversion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(
        tmp_path,
        "datasources/a.py",
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', unexpected=True)\n",
    )
    with pytest.raises(SemanticLoadFailed) as caught:
        ms.load(workspace_dir=tmp_path)
    error = caught.value.errors[0]
    assert isinstance(error.__cause__, DatasourceLoadError)
    assert isinstance(error.__cause__.__cause__, TypeError)
    assert error.repair is error.__cause__.repair
    assert error.traceback == error.__cause__.traceback
    payload = _error_to_dict(error)
    assert payload["exception_type"] == "TypeError"
    assert payload["location"] == {"file": str(path), "line": 2}
    assert "traceback" not in payload
    assert "telemetry" not in str(caught.value)
    assert main(["--workspace-dir", str(tmp_path), "--format", "json"]) == 1
    cli = json.loads(capsys.readouterr().out)
    assert cli["errors"] == [payload]
    assert main(["--workspace-dir", str(tmp_path), "--format", "json", "--readiness"]) == 1
    cli = json.loads(capsys.readouterr().out)
    assert cli["status"] == "blocked"
    assert cli["errors"] == [payload]
    assert "readiness" not in cli
    assert main(["--workspace-dir", str(tmp_path), "--format", "json", "--traceback"]) == 1
    cli = json.loads(capsys.readouterr().out)
    assert isinstance(error.traceback, str) and "telemetry" in error.traceback
    assert cli["errors"][0]["traceback"] == error.traceback
    assert main(["--workspace-dir", str(tmp_path)]) == 1
    display = capsys.readouterr().out
    assert f"{path}:2" in display and "Original traceback" not in display


def test_multiple_execution_failures_have_separate_tracebacks(tmp_path: Path) -> None:
    _write(tmp_path, "datasources/a.py", "raise ValueError('first failure')\n")
    _write(tmp_path, "datasources/b.py", "raise RuntimeError('second failure')\n")
    with pytest.raises(SemanticLoadFailed) as caught:
        ms.load(workspace_dir=tmp_path)
    assert len(caught.value.errors) == 2
    rendered = str(caught.value)
    assert all(error.traceback is not None for error in caught.value.errors)
    assert rendered.count("first failure") == 1
    assert "Traceback" not in rendered


def test_partial_catalog_excludes_failed_datasource_and_transitive_dependents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        "datasources/broken.py",
        "import marivo.datasource as md\n"
        "broken = md.duckdb(name='broken')\n"
        "raise ValueError('broken declaration')\n",
    )
    _write(
        tmp_path,
        "datasources/importer.py",
        "import marivo.datasource as md\n"
        "from .broken import broken\n"
        "md.duckdb(name='from_broken')\n",
    )
    _write(
        tmp_path,
        "datasources/healthy.py",
        "import marivo.datasource as md\nmd.duckdb(name='healthy')\n",
    )
    _write(
        tmp_path,
        "semantic/demo/_domain.py",
        "import marivo.semantic as ms\nms.domain(name='demo', owner='Tester')\n",
    )
    _write(
        tmp_path,
        "semantic/demo/models.py",
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "good = ms.entity(name='good', datasource=ms.ref.datasource('healthy'), source=md.table('good'))\n"
        "bad = ms.entity(name='bad', datasource=ms.ref.datasource('broken'), source=md.table('bad'))\n"
        "amount = ms.measure_column(name='amount', entity=bad, column='amount', additivity=ms.additive_all())\n"
        "@ms.measure()\ndef adjusted():\n    return amount * 2\n"
        "@ms.metric()\ndef total():\n    return adjusted.sum()\n"
        "total_ref = ms.ref.metric('demo.total')\n"
        "@ms.metric()\ndef dependent():\n    return total_ref + total_ref\n"
        "@ms.metric()\ndef good_count():\n    return good.count()\n",
    )
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))

    catalog = ms.load(allow_partial=True)
    page = catalog.search(kind="metric")
    assert [entry.id for entry in page.items] == ["metric:demo.good_count"]
    assert page.scope.complete is False
    assert page.scope.load_error_count == len(catalog.load_errors) == 1
    assert "partial_catalog" in page.render()
    assert catalog.excluded_refs == (
        "entity:demo.bad",
        "measure:demo.bad.adjusted",
        "measure:demo.bad.amount",
        "metric:demo.dependent",
        "metric:demo.total",
    )
    assert {entry.id for entry in catalog.search(kind="datasource").items} == {
        "datasource:default",
        "datasource:healthy",
    }
    entity = catalog.get("entity:demo.good")
    assert entity.related("dependencies").scope.complete is False
    with pytest.raises(SemanticLoadFailed):
        catalog.preview(entity, scope=md.unpruned(max_rows=1, timeout_seconds=1))
    with pytest.raises(SemanticLoadFailed):
        ms.load()
    with pytest.raises(SemanticLoadFailed):
        _ = mv.session.get_or_create("strict", timezone="UTC").catalog


def test_partial_catalog_does_not_choose_a_winner_for_duplicate_datasources(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "datasources/a.py",
        "import marivo.datasource as md\nmd.duckdb(name='warehouse')\n",
    )
    _write(
        tmp_path,
        "datasources/b.py",
        "import marivo.datasource as md\nmd.duckdb(name='warehouse')\n"
        "raise ValueError('broken declaration')\n",
    )
    with pytest.raises(SemanticLoadFailed) as caught:
        ms.load(workspace_dir=tmp_path, allow_partial=True)
    assert any(error.kind == "duplicate_name" for error in caught.value.errors)


def test_partial_catalog_discards_declarations_from_caught_relative_import(
    tmp_path: Path,
) -> None:
    helper = _write(
        tmp_path,
        "datasources/helpers/broken.py",
        "import marivo.datasource as md\nmd.duckdb(name='unfinished')\n"
        "raise ValueError('broken helper')\n",
    )
    _write(
        tmp_path,
        "datasources/importer.py",
        "import marivo.datasource as md\n"
        "try:\n    from .helpers import broken\nexcept ValueError:\n    pass\n"
        "md.duckdb(name='from_importer')\n",
    )

    catalog = ms.load(workspace_dir=tmp_path, allow_partial=True)
    page = catalog.search(kind="datasource")
    assert not page.scope.complete
    assert [entry.id for entry in page.items] == ["datasource:default"]
    (error,) = catalog.load_errors
    assert error.location is not None and error.location.file == str(helper)


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


@pytest.mark.parametrize("defect", ["missing_field", "cycle", "invalid_time"])
def test_partial_loading_keeps_independent_errors_on_excluded_objects(
    tmp_path: Path, defect: str
) -> None:
    _write(tmp_path, "datasources/bad.py", "raise ValueError('broken source')\n")
    _write(
        tmp_path,
        "semantic/demo/_domain.py",
        "import marivo.semantic as ms\nms.domain(name='demo', owner='Tester')\n",
    )
    source = (
        "import marivo.semantic as ms\nimport marivo.datasource as md\n"
        "bad = ms.entity(name='bad', datasource=ms.ref.datasource('bad'), source=md.table('bad'))\n"
        "amount = ms.measure_column(name='amount', entity=bad, column='amount', additivity=ms.additive_all())\n"
    )
    if defect == "missing_field":
        source += (
            "missing = ms.ref.measure('demo.bad.missing')\n"
            "@ms.metric()\ndef total():\n    return (amount + missing).sum()\n"
        )
    elif defect == "cycle":
        source += (
            "loop = ms.ref.metric('demo.total')\n"
            "@ms.metric()\ndef total():\n    return amount.sum() + loop\n"
        )
    else:
        source = source.replace(
            "source=md.table('bad'))",
            "source=md.table('bad'), default_time=ms.ref.time_dimension('demo.bad.missing'))",
        )
    _write(tmp_path, "semantic/demo/objects.py", source)
    with pytest.raises(SemanticLoadFailed) as caught:
        ms.load(workspace_dir=tmp_path, allow_partial=True)
    assert any(
        error.kind
        == {
            "missing_field": "not_found",
            "cycle": "definition_cycle",
            "invalid_time": "invalid_time_path",
        }[defect]
        for error in caught.value.errors
    )


def test_partial_loading_keeps_duplicates_between_failed_files(tmp_path: Path) -> None:
    for name in ("a", "b"):
        _write(
            tmp_path,
            f"datasources/{name}.py",
            "import marivo.datasource as md\nmd.duckdb(name='duplicate')\nraise ValueError('bad')\n",
        )
    with pytest.raises(SemanticLoadFailed) as caught:
        ms.load(workspace_dir=tmp_path, allow_partial=True)
    assert any(error.kind == "duplicate_name" for error in caught.value.errors)


def test_load_deduplication_preserves_distinct_exception_causes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "datasources/helper.py",
        "def fail(reason):\n"
        "    try:\n        raise ValueError(reason)\n"
        "    except ValueError as cause:\n        raise RuntimeError('wrapper') from cause\n",
    )
    for name in ("a", "b"):
        _write(tmp_path, f"datasources/{name}.py", f"from .helper import fail\nfail('{name}')\n")
    catalog = ms.load(workspace_dir=tmp_path, allow_partial=True)
    assert len(catalog.load_errors) == 2
    assert {"ValueError: a", "ValueError: b"} <= {
        line for error in catalog.load_errors for line in (error.traceback or "").splitlines()
    }


def test_partial_loading_rolls_back_import_cycle_and_reloads_independent_helper(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "datasources/a.py",
        "import marivo.datasource as md\n"
        "source = md.duckdb(name='unfinished')\n"
        "from . import b, healthy\n"
        "raise ValueError('cycle failure')\n",
    )
    _write(
        tmp_path,
        "datasources/b.py",
        "import marivo.datasource as md\nfrom .a import source\nmd.duckdb(name='dependent')\n",
    )
    _write(
        tmp_path,
        "datasources/healthy.py",
        "import marivo.datasource as md\nmd.duckdb(name='healthy')\n",
    )
    catalog = ms.load(workspace_dir=tmp_path, allow_partial=True)
    assert {item.id for item in catalog.search(kind="datasource").items} == {
        "datasource:default",
        "datasource:healthy",
    }
    assert len(catalog.load_errors) == 1


def test_partial_loading_discloses_unattributed_missing_datasource(tmp_path: Path) -> None:
    _write(tmp_path, "datasources/a.py", "raise ValueError('source failure')\n")
    _write(
        tmp_path,
        "semantic/demo/_domain.py",
        "import marivo.semantic as ms\nms.domain(name='demo', owner='Tester')\n",
    )
    _write(
        tmp_path,
        "semantic/demo/objects.py",
        "import marivo.semantic as ms\nimport marivo.datasource as md\n"
        "ms.entity(name='bad', datasource=ms.ref.datasource('typo'), source=md.table('bad'))\n",
    )
    catalog = ms.load(workspace_dir=tmp_path, allow_partial=True)
    assert any(
        e.kind == "missing_datasource_ref" and e.received == "typo" for e in catalog.load_errors
    )
    assert catalog.excluded_refs == ("entity:demo.bad",)


def test_partial_loading_preserves_reserved_identity_error_in_failed_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "datasources/a.py",
        "import marivo.datasource as md\nmd.duckdb(name='default')\nraise ValueError('bad')\n",
    )
    with pytest.raises(SemanticLoadFailed) as caught:
        ms.load(workspace_dir=tmp_path, allow_partial=True)
    assert any(error.kind == "duplicate_name" for error in caught.value.errors)
