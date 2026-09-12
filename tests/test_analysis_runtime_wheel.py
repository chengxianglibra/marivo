"""Release gate for the installed Dataset algebra, disclosure and cold reads."""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from zipfile import ZipFile

import pytest

from tests.installed_wheel_probe import surface_snapshot

pytestmark = pytest.mark.release
ROOT = Path(__file__).resolve().parents[1]

# Reuse independent contract expectations, not a copy of the product registry.
CONTRACT_TESTS = (
    "test_public_surface",
    "test_agent_result_protocol",
    "test_lazy_dataset_values",
    "test_lazy_dataset_contract",
    "test_analysis_help",
    "test_analysis_help_resolution",
    "test_unified_help",
    "test_lazy_disclosure",
    "test_lazy_disclosure_examples",
    "test_lazy_public_session",
    "test_cutover_removed_contracts",
    "test_cutover_documentation_examples",
    "test_cli",
)


def _stage_tests(destination: Path) -> None:
    """Copy only selected tests and their statically imported test helpers."""
    pending = {f"tests.{name}" for name in CONTRACT_TESTS}
    pending.update(("tests.conftest", "tests.installed_wheel_probe"))
    copied: set[str] = set()
    while pending:
        name = pending.pop()
        if name in copied:
            continue
        relative = Path(*name.split(".")).with_suffix(".py")
        source = ROOT / relative
        assert source.is_file(), name
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.add(name)
        for node in ast.walk(ast.parse(source.read_text())):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("tests.")
            ):
                pending.add(node.module)
            elif isinstance(node, ast.Import):
                pending.update(
                    alias.name for alias in node.names if alias.name.startswith("tests.")
                )
    (destination / "tests/__init__.py").write_text(
        '"""Isolated installed-package test inputs."""\n'
    )
    for prefix in ("docs", "zh-cn/docs"):
        for page in ("analysis-workflow", "evidence", "semantic-layer"):
            relative = Path(f"site/src/content/docs/{prefix}/latest/concepts/{page}.mdx")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
    # Explicit configuration prevents pytest.ini's source-root pythonpath from leaking in.
    (destination / "pytest.ini").write_text(
        "[pytest]\npython_classes =\nmarkers =\n"
        "    runtime: installed real Runtime checks\n"
        "    release: installed packaging checks\n"
    )


def _check_archives(wheel: Path, sdist: Path) -> dict[str, object]:
    expected = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / "marivo").rglob("*")
        if path.is_file() and (path.suffix == ".py" or path.name == "SKILL.md")
    }
    with ZipFile(wheel) as archive:
        names = archive.namelist()
        contents = {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in names
            if name.startswith("marivo/")
        }
        skill = archive.read("marivo/skills/marivo-analysis/SKILL.md").decode()
        for token in (
            "bounded Run history",
            "exact committed Artifact",
            "focused Session graph",
            "Artifact-owned Finding reads",
        ):
            assert token in skill
        for stale in ("session.jobs(", "session.get_frame(", "session.evidence"):
            assert stale not in skill
    assert contents == expected, "wheel differs from the current package source inventory"
    with tarfile.open(sdist) as archive:
        sources: dict[str, str] = {}
        for member in archive.getmembers():
            relative = member.name.partition("/")[2]
            if relative.startswith("marivo/") and member.isfile():
                stream = archive.extractfile(member)
                assert stream is not None
                sources[relative] = hashlib.sha256(stream.read()).hexdigest()
    assert sources == expected, "sdist differs from the current package source inventory"
    for prefix in ("frames", "intents", "executor", "windows", "scripts", "evidence/extraction"):
        assert not any(name.startswith(f"marivo/analysis/{prefix}/") for name in contents)
    assert not any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names)
    return {
        "files": contents,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "sdist_sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(),
    }


def test_installed_dataset_surface_and_three_process_recovery(
    tmp_path: Path, authoring_evidence_project: Path
) -> None:
    wheels = tuple((ROOT / "dist/pypi").glob("marivo-*.whl"))
    sdists = tuple((ROOT / "dist/pypi").glob("marivo-*.tar.gz"))
    assert len(wheels) == len(sdists) == 1, "Run make pypi-build pypi-check first"
    wheel, sdist = wheels[0], sdists[0]
    archive_report = _check_archives(wheel, sdist)
    work = tmp_path / "installed-check"
    work.mkdir()
    assert not work.resolve().is_relative_to(ROOT)
    reports = work / "reports"
    reports.mkdir()
    (reports / "archives.json").write_text(
        json.dumps(archive_report, indent=2, sort_keys=True) + "\n"
    )
    expected_surface = surface_snapshot()
    (reports / "source-surface.json").write_text(json.dumps(expected_surface, indent=2) + "\n")
    _stage_tests(work)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "PYTEST", "MARIVO_"))
    }
    environment.update(
        MARIVO_TELEMETRY="off",
        PYTHONNOUSERSITE="1",
        MARIVO_WHEEL_SHA256=str(archive_report["wheel_sha256"]),
    )
    receipts: list[dict[str, object]] = []

    def run(
        name: str, command: list[str], *, expected: int = 0, env: dict[str, str] | None = None
    ) -> str:
        start = time.monotonic()
        process = subprocess.run(
            command,
            cwd=work,
            env=env or environment,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        (reports / f"{name}.log").write_text(process.stdout + process.stderr)
        receipts.append(
            {
                "name": name,
                "command": command,
                "cwd": str(work),
                "exit_code": process.returncode,
                "duration_seconds": round(time.monotonic() - start, 3),
            }
        )
        assert (process.returncode == 0) == (expected == 0), process.stdout + process.stderr
        return process.stdout + process.stderr

    try:
        venv = work / ".venv"
        run("create-venv", [sys.executable, "-m", "venv", str(venv)])
        interpreter = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        constraints = work / "constraints.txt"
        constraints.write_text(
            "\n".join(
                sorted(
                    {
                        f"{item.metadata['Name']}=={item.version}"
                        for item in importlib.metadata.distributions()
                        if item.metadata["Name"].lower() != "marivo"
                    }
                )
            )
            + "\n"
        )
        shutil.copy2(constraints, reports / constraints.name)
        run(
            "install",
            [
                str(interpreter),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--constraint",
                str(constraints),
                f"{wheel}[duckdb]",
                "pytest",
                "pytest-xdist",
                "boto3",
            ],
        )
        run("dependencies", [str(interpreter), "-m", "pip", "list", "--format=json"])
        run("dependency-check", [str(interpreter), "-m", "pip", "check"])
        probe = [str(interpreter), "-m", "tests.installed_wheel_probe"]
        run("origin", [*probe, "guard", str(reports / "origin.json")])
        run("surface", [*probe, "surface", str(reports / "installed-surface.json")])
        assert json.loads((reports / "installed-surface.json").read_text()) == expected_surface
        # Deliberately inject the real source tree; the positive gate's guard must reject it.
        poisoned = {**environment, "PYTHONPATH": str(ROOT)}
        rejection = run(
            "foreign-import-rejected",
            [*probe, "guard", str(reports / "invalid.json")],
            expected=1,
            env=poisoned,
        )
        assert "foreign Marivo import" in rejection
        selected = [f"tests/{name}.py" for name in CONTRACT_TESTS]
        for marker in ("not runtime", "runtime"):
            name = "runtime" if marker == "runtime" else "contracts"
            environment["MARIVO_INSTALLED_ORIGIN_REPORT"] = str(reports / f"{name}-origins.json")
            run(
                name,
                [
                    str(interpreter),
                    "-m",
                    "pytest",
                    "-p",
                    "tests.installed_wheel_probe",
                    "-c",
                    str(work / "pytest.ini"),
                    "--import-mode=importlib",
                    "-n",
                    "0",
                    "-q",
                    "--tb=short",
                    "--maxfail=5",
                    "-m",
                    marker,
                    *selected,
                ],
            )
        console = interpreter.parent / ("marivo.exe" if os.name == "nt" else "marivo")
        module_help = run("module-help", [str(interpreter), "-m", "marivo", "help"])
        console_help = run("console-help", [str(console), "help"])
        assert console_help == module_help
        phases = []
        for phase in ("produce", "continue", "recover"):
            report = reports / f"{phase}.json"
            run(phase, [*probe, phase, str(authoring_evidence_project), str(report)])
            phases.append(json.loads(report.read_text()))
        assert len({item["pid"] for item in phases}) == 3
        for key in ("session", "artifact", "run", "evidence", "finding_ids"):
            assert all(item[key] == phases[0][key] for item in phases)
        assert [item["run_count"] for item in phases] == [1, 2, 2]
        assert phases[2]["execution_statements"] == []
    finally:
        (reports / "commands.json").write_text(
            json.dumps(receipts, indent=2, sort_keys=True) + "\n"
        )
        retained = os.environ.get("MARIVO_SLICE8C_EVIDENCE_DIR")
        if retained:
            destination = Path(retained) / "installed-wheel"
            destination.mkdir(parents=True, exist_ok=True)
            for path in reports.iterdir():
                shutil.copy2(path, destination / path.name)
