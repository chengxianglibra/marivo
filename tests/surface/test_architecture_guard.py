"""Negative witnesses for the architecture guard and PR ratchet."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import grimp
import pytest

from scripts import check_architecture as guard
from scripts import check_governance_diff as governance
from tests.support.paths import PROJECT_ROOT


def _write(root: Path, file: str, source: str) -> Path:
    path = root / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _empty_baseline() -> guard.Baseline:
    return {rule: {} for rule in guard.RULES}


def _private_import(root: Path) -> Path:
    return _write(
        root,
        "marivo/analysis/probe.py",
        "def disclosure():\n"
        "    from ..semantic.catalog import _private_name as value\n"
        "    return value\n",
    )


def test_arch001_and_plc0415_reject_local_private_import_in_new_module(tmp_path: Path) -> None:
    module = _private_import(tmp_path)
    findings = guard.scan_sources(tmp_path)
    (edge,) = findings["ARCH001"]
    assert edge == "marivo.analysis.probe -> marivo.semantic.catalog : _private_name"
    errors, _ = guard.compare(findings, _empty_baseline())
    assert any(error.startswith(f"ARCH001 {edge} ") for error in errors)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(PROJECT_ROOT / "pyproject.toml"),
            "--output-format",
            "json",
            str(module),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 1, result.stderr
    assert [item["code"] for item in json.loads(result.stdout)] == ["PLC0415"]


def test_arch001_includes_type_imports_and_uses_origin_names(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "marivo/analysis/probe.py",
        "from typing import TYPE_CHECKING\n"
        "from marivo._definition.types import _shared\n"
        "from marivo.semantic.spi import _stable\n"
        "if TYPE_CHECKING:\n"
        "    import marivo.semantic._details as public\n"
        "    from ._analysis_detail import _local\n",
    )
    assert guard.scan_sources(tmp_path)["ARCH001"] == {
        "marivo.analysis.probe -> marivo.semantic._details": 1
    }


@pytest.mark.parametrize("scope", ("analysis", "semantic"))
def test_arch002_counts_compares_match_cases_and_imports(tmp_path: Path, scope: str) -> None:
    file = f"marivo/{scope}/probe.py"
    _write(
        tmp_path,
        file,
        "from ..datasource.engines import clickhouse as engine\n"
        "ok = backend.name in ('postgresql', 'duckdb')\n"
        "match backend.name:\n"
        "    case 'mysql' | 'trino':\n"
        "        pass\n"
        "engine_name = 'postgres'\n",
    )
    findings = guard.scan_sources(tmp_path)
    assert findings["ARCH002"] == {file: 4}
    baseline = _empty_baseline()
    baseline["ARCH002"][file] = 3
    errors, _ = guard.compare(findings, baseline)
    assert f"ARCH002 {file} (4, baseline 3)" in errors


def test_arch003_requires_a_reason_for_each_exact_call_site(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "marivo/probe.py",
        "import importlib as loader\n"
        "from builtins import eval as evaluate\n"
        "def read():\n"
        "    loader.import_module('marivo.allowed')\n"
        "    loader.import_module('marivo.other')\n"
        "    evaluate('1 + 1')\n"
        "    __import__('marivo.unlisted')\n"
        "    exec('value = 42')\n"
        "    table.eval('values + 1')\n",
    )
    baseline = _empty_baseline()
    baseline["ARCH003"]["marivo.probe:read: loader.import_module('marivo.allowed')"] = "lazy probe"
    findings = guard.scan_sources(tmp_path)
    errors, _ = guard.compare(findings, baseline)
    assert errors == [
        "ARCH003 marivo.probe:read: __import__('marivo.unlisted') (1, baseline 0)",
        "ARCH003 marivo.probe:read: evaluate('1 + 1') (1, baseline 0)",
        "ARCH003 marivo.probe:read: exec('value = 42') (1, baseline 0)",
        "ARCH003 marivo.probe:read: loader.import_module('marivo.other') (1, baseline 0)",
    ]


def test_arch004_rejects_a_new_edge_inside_an_existing_cycle() -> None:
    graph = grimp.ImportGraph()
    graph.add_import(importer="a", imported="b")
    graph.add_import(importer="b", imported="c")
    graph.add_import(importer="c", imported="a")
    graph.add_import(importer="c", imported="d")
    baseline = _empty_baseline()
    baseline["ARCH004"] = {"runtime: a -> b": 1, "runtime: b -> c": 1, "runtime: c -> a": 1}
    graph.add_import(importer="a", imported="c")
    findings = {rule: Counter[str]() for rule in guard.RULES}
    for source, destination in guard.cyclic_edges(graph):
        findings["ARCH004"][f"runtime: {source} -> {destination}"] = 1
    errors, _ = guard.compare(findings, baseline)
    assert errors == ["ARCH004 runtime: a -> c (1, baseline 0)"]


def test_arch005_counts_escape_hatches_and_enforces_local_sizes(tmp_path: Path) -> None:
    file = "marivo/probe.py"
    _write(
        tmp_path,
        file,
        "from builtins import getattr as read_attr\n"
        "from typing import Any as Loose, cast as narrow\n"
        "item: Loose = factory()  # type: ignore[name-defined]  # synthetic external input\n"
        "narrow(str, item)\n"
        "read_attr(item, 'name')\n"
        "object.__setattr__(item, 'name', 'ready')\n"
        "async def run():\n"
        + "    step()\n" * 120
        + "class Wide:\n"
        + "".join(f"    def method_{index}(self): pass\n" for index in range(31))
        + "\n" * 801,
    )
    findings = guard.scan_sources(tmp_path)
    budgets = findings["ARCH005"]
    assert {
        key: budgets[key]
        for key in ("cast", "Any", "getattr", "object.__setattr__", "type: ignore")
    } == {
        "cast": 1,
        "Any": 1,
        "getattr": 1,
        "object.__setattr__": 1,
        "type: ignore": 1,
    }
    assert budgets["undocumented type: ignore"] == 0
    assert budgets[f"{file}: function run lines"] == 121
    assert budgets[f"{file}: class Wide methods"] == 31
    assert budgets[f"{file}: file lines"] > 800
    errors, _ = guard.compare(findings, _empty_baseline())
    assert f"ARCH005 {file}: function run lines (121, baseline 0)" in errors


@pytest.fixture
def miniature_package(tmp_path: Path) -> Path:
    for package in ("", "analysis/", "datasource/", "ontology/", "semantic/"):
        _write(tmp_path, f"marivo/{package}__init__.py", "")
    return tmp_path


def test_arch006_detects_a_forbidden_domain_in_a_fresh_process(miniature_package: Path) -> None:
    _write(miniature_package, "marivo/ontology/__init__.py", "from marivo import server\n")
    _write(miniature_package, "marivo/server.py", "")
    assert guard.check_cold_imports(miniature_package) == [
        "ARCH006 marivo.ontology cold import loaded marivo.server"
    ]


def test_ratchet_down_refuses_growth_and_writes_only_a_reduction(miniature_package: Path) -> None:
    root = miniature_package
    script = _write(root, "scripts/check_architecture.py", "")
    shutil.copyfile(PROJECT_ROOT / "scripts/check_architecture.py", script)
    module = _private_import(root)
    baseline = _empty_baseline()
    edge = "marivo.analysis.probe -> marivo.semantic.catalog : _private_name"
    baseline["ARCH001"][edge] = 2
    baseline["ARCH002"]["marivo/analysis/old.py"] = 1
    baseline["ARCH005"]["marivo/analysis/probe.py: local Marivo imports"] = 1
    baseline_file = _write(root, "scripts/architecture_baseline.json", json.dumps(baseline))
    original = baseline_file.read_bytes()

    module.write_text(module.read_text() + "\nengine_check = backend == 'duckdb'\n")
    command = [sys.executable, str(script), "--ratchet-down"]
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 1
    assert "ARCH002 marivo/analysis/probe.py (1, baseline 0)" in result.stderr
    assert baseline_file.read_bytes() == original

    _private_import(root)
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 0, result.stderr
    baseline["ARCH001"][edge] = 1
    baseline["ARCH002"].clear()
    assert json.loads(baseline_file.read_text()) == baseline


def test_diff_rejects_moving_an_import_ignore_and_widening_budgets() -> None:
    config = (
        "[importlinter]\nroot_package = marivo\nexclude_type_checking_imports = false\n"
        "[importlinter:contract:first]\nignore_imports =\n    marivo.a -> marivo.b\n"
    )
    errors = governance.import_config_relaxations(
        config, config.replace("contract:first", "contract:second")
    )
    assert len(errors) == 1
    assert "[importlinter:contract:second] for marivo.a -> marivo.b" in errors[0]
    previous = {"ARCH001": {"old": 2}, "ARCH003": {"existing": "lazy dependency"}}
    candidate = {"ARCH001": {"old": 3}, "ARCH003": {"new": "unreviewed dependency"}}
    assert governance.baseline_increases(json.dumps(previous), json.dumps(candidate)) == [
        "scripts/architecture_baseline.json adds or increases ARCH001 old",
        "scripts/architecture_baseline.json adds or increases ARCH003 new",
    ]
    assert (
        governance.baseline_increases(json.dumps(previous), '{"ARCH001":{"old":1},"ARCH003":{}}')
        == []
    )
