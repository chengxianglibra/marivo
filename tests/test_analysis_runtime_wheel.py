"""Release gate for same-wheel graph journeys, disclosure and cold recovery."""

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
    "test_analysis_help_resolution",
    "test_unified_help",
    "test_lazy_disclosure",
    "test_lazy_public_session",
    "test_cutover_documentation_examples",
    "test_analysis_dsl_public",
    "test_analysis_dsl_r45_migration",
    "test_analysis_graph_preflight_r45",
    "test_analysis_graph_publication_r44",
    "test_analysis_graph_runtime_r42",
    "test_analysis_graph_r33",
    "test_analysis_lowering_r34",
    "test_analysis_dsl_exchange",
    "test_analysis_dsl_contracts",
    "test_analysis_dsl_p2_disclosure",
    "test_cli",
)


R5_TESTS = (
    "test_analysis_members_r52",
    "test_analysis_observation_r53",
    "test_analysis_coordinates_r54",
    "test_analysis_temporal_r55",
    "test_analysis_numeric_r56",
    "test_analysis_numeric_review_r56",
    "test_analysis_recovery_r57",
    "test_lazy_temporal_public_runtime",
    "test_analysis_decimal_e2e",
    "test_analysis_cumulative_decimal",
    "test_lazy_runtime_concurrency",
    "test_lazy_source_algebra",
    "test_lazy_local_placement",
    "test_lazy_status_fold_admission",
    "test_lazy_retained_compiler",
    "test_sqlite_semantic_integration",
    "test_public_quantile_input",
)

R6_TESTS = (
    "test_analysis_comparison_r62",
    "test_analysis_comparison_runtime_r62",
    "test_analysis_predicates_r63",
    "test_analysis_cohort_r63",
    "test_analysis_references_r64",
    "test_analysis_display_r65",
    "test_analysis_attribution_r66",
    "test_analysis_attribution_runtime_r66",
    "test_analysis_recovery_r67",
)

R7_TESTS = (
    "test_analysis_domain_preparation_r72",
    "test_analysis_journey_matching_r73",
    "test_analysis_funnel_r74",
    "test_analysis_lifecycle_r75",
    "test_analysis_history_r76",
    "test_analysis_anchors_r77",
    "test_analysis_retention_r78",
    "test_analysis_retention_r78_evidence",
    "test_analysis_retirement_r79",
    "test_semantic_r23_business_order",
    "test_lazy_public_relationships",
)

R7_WORKERS = (
    "funnel_r74_worker",
    "lifecycle_r75_worker",
    "history_r76_worker",
    "history_r76_a10_worker",
    "history_r76_consumers_worker",
    "anchors_r77_worker",
    "retention_r78_worker",
)

R8_TESTS = (
    "test_public_surface",
    "test_analysis_runs_kernel_r83",
    "test_analysis_statistics_kernel_r84",
    "test_analysis_views_r85",
    "test_analysis_disclosure_r85",
    "test_analysis_acceptance_r86",
    "test_analysis_faults_r86",
)


def _stage_tests(destination: Path, modules: tuple[str, ...] | None = None) -> None:
    """Copy only selected tests and their statically imported test helpers."""
    pending = {
        f"tests.{name}"
        for name in (
            modules
            if modules is not None
            else (*CONTRACT_TESTS, *R5_TESTS, *R6_TESTS, *R7_TESTS, *R7_WORKERS)
        )
    }
    pending.update(
        (
            "tests.conftest",
            "tests.installed_wheel_probe",
            "tests.graph_publication_runtime_worker",
            "tests.installed_r6_journeys",
        )
    )
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
                and node.module.startswith(("tests.", "scripts."))
            ):
                pending.add(node.module)
            elif isinstance(node, ast.Import):
                pending.update(
                    alias.name
                    for alias in node.names
                    if alias.name.startswith(("tests.", "scripts."))
                )
    (destination / "tests/__init__.py").write_text(
        '"""Isolated installed-package test inputs."""\n'
    )
    (destination / "scripts").mkdir(exist_ok=True)
    (destination / "scripts/__init__.py").write_text('"""Isolated evidence helpers."""\n')
    for prefix in ("docs", "zh-cn/docs"):
        relative = Path(f"site/src/content/docs/{prefix}/latest")
        shutil.copytree(ROOT / relative, destination / relative)
    (destination / "docs/api").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "docs/api/analysis.rst", destination / "docs/api/analysis.rst")
    shutil.copy2(ROOT / "pyproject.toml", destination / "pyproject.toml")
    # Qualification assertions read immutable historical inventories; these are
    # test inputs, never an importable checkout or an installed-package authority.
    for source in (ROOT / "docs/superpowers/specs").glob("*r7*"):
        if source.is_file():
            target = destination / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    # Explicit configuration prevents pytest.ini's source-root pythonpath from leaking in.
    (destination / "pytest.ini").write_text(
        "[pytest]\npython_classes =\nmarkers =\n"
        "    runtime: installed real Runtime checks\n"
        "    release: installed packaging checks\n"
        "filterwarnings =\n    ignore::DeprecationWarning:ibis.*\n"
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


def test_installed_graph_journeys_and_three_process_recovery(tmp_path: Path) -> None:
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
    (reports / "inputs.json").write_text(
        json.dumps(
            {
                str(path.relative_to(work)): hashlib.sha256(path.read_bytes()).hexdigest()
                for base in (work / "tests", work / "site", work / "docs")
                for path in sorted(base.rglob("*"))
                if path.is_file()
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "PYTEST", "MARIVO_"))
    }
    environment.update(
        MARIVO_TELEMETRY="off",
        PYTHONNOUSERSITE="1",
        MARIVO_WHEEL_SHA256=str(archive_report["wheel_sha256"]),
        MARIVO_R76_EVIDENCE_DIR=str(reports / "r76-profiles"),
        MARIVO_R77_EVIDENCE_DIR=str(reports / "r77-profiles"),
        MARIVO_R78_EVIDENCE_DIR=str(reports / "r78-profiles"),
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
            timeout=7200 if name in R7_TESTS else 900,
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
            ],
        )
        run("dependencies", [str(interpreter), "-m", "pip", "list", "--format=json"])
        run("dependency-check", [str(interpreter), "-m", "pip", "check"])
        probe = [str(interpreter), "-m", "tests.installed_wheel_probe"]
        run("origin", [*probe, "guard", str(reports / "origin.json")])
        installed = json.loads((reports / "origin.json").read_text())["package"]
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
        # Add the installed-file view after the poison check so that the work
        # directory cannot mask a deliberately injected checkout import.
        (work / "marivo").symlink_to(installed, target_is_directory=True)
        run("origin-hook", [*probe, "install-hook", str(reports / "origin-hook.json")])
        environment["MARIVO_INSTALLED_ORIGIN_DIR"] = str(reports / "process-origins")
        run("origin-hook-check", [*probe, "guard", str(reports / "hook-guard.json")])
        assert tuple((reports / "process-origins").glob("*-start.json")), "origin hook did not run"
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
                    f"--junitxml={reports / (name + '.xml')}",
                    "-m",
                    marker,
                    *selected,
                ],
            )
        for module in (*R5_TESTS, *R6_TESTS, *R7_TESTS):
            environment["MARIVO_INSTALLED_ORIGIN_REPORT"] = str(reports / f"{module}-origins.json")
            run(
                module,
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
                    "2",
                    "-q",
                    "--tb=short",
                    "--maxfail=5",
                    f"--junitxml={reports / (module + '.xml')}",
                    f"tests/{module}.py",
                ],
            )
        console = interpreter.parent / ("marivo.exe" if os.name == "nt" else "marivo")
        module_help = run("module-help", [str(interpreter), "-m", "marivo", "help"])
        console_help = run("console-help", [str(console), "help"])
        assert console_help == module_help
        for source in ("table", "parquet"):
            for scenario in ("j1", "j2", "j3", "j4", "a02", "a06", "a07", "a08"):
                project = work / f"{source}-{scenario}"
                phases = []
                for phase in ("produce", "continue", "recover"):
                    report = reports / f"{source}-{scenario}-{phase}.json"
                    run(
                        f"{source}-{scenario}-{phase}",
                        [*probe, phase, str(project), scenario, source, str(report)],
                    )
                    phases.append(json.loads(report.read_text()))
                assert len({item["pid"] for item in phases}) == 3
                fields = (
                    (
                        "session",
                        "snapshots",
                        "oracle",
                        "facts_sha256",
                        "edge_checks",
                        "shared_nodes",
                    )
                    if scenario.startswith("a")
                    else (
                        "session",
                        "artifact",
                        "run",
                        "rows",
                        "contract",
                        "descriptor",
                        "facts_sha256",
                    )
                )
                for key in fields:
                    assert all(item[key] == phases[0][key] for item in phases)
                assert phases[1]["continuations"] == phases[2]["continuations"]
                assert phases[1]["run_count"] > phases[0]["run_count"]
                assert phases[1]["run_count"] == phases[2]["run_count"]
        origins = tuple((reports / "process-origins").glob("*.json"))
        assert origins
        for path in origins:
            assert "origin" in json.loads(path.read_text()), path.read_text()
    finally:
        (reports / "commands.json").write_text(
            json.dumps(receipts, indent=2, sort_keys=True) + "\n"
        )
        retained = (
            os.environ.get("MARIVO_R79_EVIDENCE_DIR")
            or os.environ.get("MARIVO_R67_EVIDENCE_DIR")
            or os.environ.get("MARIVO_R57_EVIDENCE_DIR")
            or os.environ.get("MARIVO_R46_EVIDENCE_DIR")
        )
        if retained:
            destination = Path(retained) / "installed-wheel"
            destination.mkdir(parents=True, exist_ok=True)
            for path in reports.iterdir():
                if path.is_dir():
                    shutil.copytree(path, destination / path.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(path, destination / path.name)


def test_installed_r8_candidate_and_public_processes(tmp_path: Path) -> None:
    """Run the R8 gate independently of unrelated full-release Runtime suites."""
    wheels = tuple((ROOT / "dist/pypi").glob("marivo-*.whl"))
    sdists = tuple((ROOT / "dist/pypi").glob("marivo-*.tar.gz"))
    assert len(wheels) == len(sdists) == 1, "Run make pypi-build pypi-check first"
    wheel, sdist = wheels[0], sdists[0]
    archives = _check_archives(wheel, sdist)
    work = tmp_path / "installed-r8"
    work.mkdir()
    assert not work.resolve().is_relative_to(ROOT)
    reports = work / "reports"
    reports.mkdir()
    (reports / "archives.json").write_text(json.dumps(archives, sort_keys=True) + "\n")
    expected_surface = surface_snapshot()
    _stage_tests(work, (*R8_TESTS, "r86_worker", "installed_graph_journeys"))
    (reports / "inputs.json").write_text(
        json.dumps(
            {
                str(p.relative_to(work)): hashlib.sha256(p.read_bytes()).hexdigest()
                for folder in ("tests", "scripts", "site", "docs")
                for p in sorted((work / folder).rglob("*"))
                if p.is_file()
            },
            sort_keys=True,
        )
        + "\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "PYTEST", "MARIVO_"))
    }
    environment.update(
        MARIVO_TELEMETRY="off",
        PYTHONNOUSERSITE="1",
        MARIVO_WHEEL_SHA256=str(archives["wheel_sha256"]),
        MARIVO_R86_EVIDENCE_DIR=str(reports),
    )
    receipts: list[dict[str, object]] = []

    def run(
        name: str, command: list[str], *, reject: bool = False, env: dict[str, str] | None = None
    ) -> str:
        started = time.monotonic()
        process = subprocess.run(
            command,
            cwd=work,
            env=env or environment,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        log = reports / f"{name}.log"
        log.write_text(process.stdout + process.stderr)
        receipts.append(
            {
                "name": name,
                "command": command,
                "cwd": str(work),
                "exit_code": process.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
                "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            }
        )
        assert (process.returncode != 0) if reject else (process.returncode == 0), log.read_text()
        return log.read_text()

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
                "mypy",
                "pandas-stubs",
                "scipy-stubs",
                "types-python-dateutil",
            ],
        )
        run("dependencies", [str(interpreter), "-m", "pip", "list", "--format=json"])
        run("dependency-check", [str(interpreter), "-m", "pip", "check"])
        probe = [str(interpreter), "-m", "tests.installed_wheel_probe"]
        run("origin", [*probe, "guard", str(reports / "origin.json")])
        package = json.loads((reports / "origin.json").read_text())["package"]
        rejection = run(
            "poisoned-pythonpath",
            [*probe, "guard", str(reports / "invalid.json")],
            reject=True,
            env={**environment, "PYTHONPATH": str(ROOT)},
        )
        assert "foreign Marivo import" in rejection
        (work / "marivo").symlink_to(package, target_is_directory=True)
        run("surface", [*probe, "surface", str(reports / "surface.json")])
        assert json.loads((reports / "surface.json").read_text()) == expected_surface
        run("origin-hook", [*probe, "install-hook", str(reports / "origin-hook.json")])
        environment["MARIVO_INSTALLED_ORIGIN_DIR"] = str(reports / "process-origins")
        environment["MARIVO_INSTALLED_ORIGIN_REPORT"] = str(reports / "pytest-origins.json")
        run(
            "r8-public",
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
                f"--junitxml={reports / 'r8-public.xml'}",
                *[f"tests/{module}.py" for module in R8_TESTS],
            ],
        )
        # A04 is the established same-Entity, explicit Spearman no-lag journey.
        for form in ("table", "parquet"):
            phases: list[dict[str, object]] = []
            for phase in ("produce", "continue", "recover"):
                report = reports / f"a04-{form}-{phase}.json"
                run(
                    f"a04-{form}-{phase}",
                    [*probe, phase, str(work / f"a04-{form}"), "j4", form, str(report)],
                )
                phases.append(json.loads(report.read_text()))
            assert len({row["pid"] for row in phases}) == 3
            for field in ("artifact", "rows", "contract", "descriptor", "oracle", "facts_sha256"):
                assert all(row[field] == phases[0][field] for row in phases)
            assert phases[1]["continuations"] == phases[2]["continuations"]
        origins = tuple((reports / "process-origins").glob("*.json"))
        assert origins and all("origin" in json.loads(p.read_text()) for p in origins)
        console = interpreter.parent / ("marivo.exe" if os.name == "nt" else "marivo")
        assert run("console-help", [str(console), "help"]) == run(
            "module-help", [str(interpreter), "-m", "marivo", "help"]
        )
    finally:
        (reports / "commands.json").write_text(
            json.dumps(receipts, indent=2, sort_keys=True) + "\n"
        )
        retained = os.environ.get("MARIVO_R86_EVIDENCE_DIR")
        if retained:
            destination = Path(retained) / "installed-wheel"
            shutil.copytree(reports, destination, dirs_exist_ok=True)
