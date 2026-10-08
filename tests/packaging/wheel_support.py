"""Prepare one isolated installed candidate for package-boundary checks."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from scripts.package_contents import check_archives
from tests.support.json import Json, read
from tests.support.paths import PROJECT_ROOT

PROBE_MODULES = (
    "tests.packaging.boundary_probe",
    "tests.packaging.interpretation_read_probe",
    "tests.packaging.display_journey",
    "tests.packaging.complete_journeys",
    "tests.packaging.wheel_probe",
    "tests.packaging.source_probe",
    "tests.analysis.statistics.recovery_worker",
    "tests.analysis.graph.interpretation_worker",
    "tests.analysis.journey.funnel_public_recovery_worker",
    "tests.analysis.lifecycle.history_public_recovery_worker",
    "tests.conftest",
    "tests.analysis.graph.test_method_consumers",
    "tests.analysis.graph.test_remote_domain_consumers",
    "tests.analysis.graph.test_reference_consumers",
    "tests.analysis.graph.test_distribution_consumers",
    "tests.analysis.graph.test_multiroot_consumers",
    "tests.analysis.numeric.test_comparison_consumers",
    "tests.analysis.materialization.test_attribution_recovery",
    "tests.analysis.materialization.attribution_recovery_worker",
    "tests.analysis.graph.observation_recovery_worker",
    "tests.analysis.graph.test_premise_verification",
    "tests.analysis.journey.test_native_funnel_recovery",
    "tests.analysis.materialization.test_producer_recovery",
    "tests.analysis.materialization.test_public_refusals",
    "tests.analysis.materialization.test_analysis_state",
    "tests.analysis.materialization.test_analysis_graph_publication",
    "tests.analysis.materialization.test_native_graph_deadline",
    "tests.analysis.materialization.test_graph_transport_phases",
    "tests.analysis.materialization.test_local_graph_interrupts",
    "tests.analysis.statistics.producer_recovery_worker",
    "tests.datasource.test_source_profiles",
    "tests.datasource.test_datasource_profiles_store",
    "tests.datasource.test_mysql_authoring_deadline",
    "tests.datasource.test_source_deadline",
    "tests.analysis.numeric.test_analysis_display",
    "tests.analysis.numeric.test_native_numeric",
    "tests.analysis.numeric.test_cohort_unknown",
    "tests.analysis.numeric.test_fixed_selection_runtime",
    "tests.analysis.numeric.test_analysis_comparison_runtime",
    "tests.analysis.temporal.test_temporal_public_runtime",
    "tests.analysis.statistics.test_analysis_statistics_boundaries",
    "tests.analysis.statistics.test_analysis_statistics",
    "tests.analysis.statistics.test_analysis_statistics_kernel",
    "tests.analysis.statistics.test_composition",
    "tests.analysis.materialization.test_public_schema_drift",
    "tests.analysis.journey.test_anchor_consumers",
    "tests.analysis.journey.sqlite_anchor_components_worker",
)


def stage_inputs(destination: Path) -> None:
    """Copy only package probes and their imported helper dependencies."""
    pending = set(PROBE_MODULES)
    copied: set[str] = set()
    while pending:
        name = pending.pop()
        if name in copied:
            continue
        relative = Path(*name.split(".")).with_suffix(".py")
        source = PROJECT_ROOT / relative
        if not source.is_file():
            relative = Path(*name.split(".")) / "__init__.py"
            source = PROJECT_ROOT / relative
        assert source.is_file(), name
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.add(name)
        for parent in target.parents:
            if parent == destination:
                break
            (parent / "__init__.py").touch()
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported = (node.module, *(node.module + "." + a.name for a in node.names))
            elif isinstance(node, ast.Import):
                imported = tuple(a.name for a in node.names)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith(("tests.", "scripts."))
                and all(part.isidentifier() for part in node.value.split("."))
            ):
                # Python -m helpers name their dependencies as module literals.
                imported = (node.value,)
            else:
                continue
            for module in imported:
                if module.startswith(("tests.", "scripts.")):
                    path = PROJECT_ROOT.joinpath(*module.split("."))
                    if path.with_suffix(".py").is_file() or (path / "__init__.py").is_file():
                        pending.add(module)
    for prefix in ("docs", "zh-cn/docs"):
        relative = Path(f"site/src/content/docs/{prefix}/latest")
        shutil.copytree(PROJECT_ROOT / relative, destination / relative)
    environment_resources = PROJECT_ROOT / "tests/datasource/environment"
    for source in environment_resources.rglob("*"):
        if source.is_file() and source.suffix in (
            ".properties",
            ".xml",
            ".yaml",
            ".json",
            ".config",
            ".sql",
        ):
            target = destination / source.relative_to(PROJECT_ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


@dataclass
class InstalledWheel:
    """An isolated noneditable install shared by installed-package checks."""

    work: Path
    interpreter: Path
    environment: dict[str, str]
    wheel: Path
    archives: dict[str, str | int]
    extras: tuple[str, ...] = ("duckdb",)
    commands: list[dict[str, Json]] = field(default_factory=list)

    @property
    def reports(self) -> Path:
        return self.work / "reports"

    def run(
        self,
        name: str,
        command: list[str],
        *,
        reject: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> str:
        completed = subprocess.run(
            command,
            cwd=self.work,
            env={**self.environment, **(extra_env or {})},
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        output = completed.stdout + completed.stderr
        log = self.reports / (name + ".log")
        log.write_text(output)
        self.commands.append(
            {
                "name": name,
                "command": list(command),
                "exit_code": completed.returncode,
                "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            }
        )
        (self.reports / "commands.json").write_text(json.dumps(self.commands, indent=2))
        assert (completed.returncode != 0) if reject else (completed.returncode == 0), output
        return output

    def probe(self, name: str, mode: str, *arguments: str) -> dict[str, Json]:
        report = self.reports / (name + ".json")
        self.run(
            name,
            [
                str(self.interpreter),
                "-m",
                "tests.packaging.wheel_probe",
                mode,
                *arguments,
                str(report),
            ],
        )
        return read(report)


def prepare_wheel(work: Path, *, extras: tuple[str, ...] = ("duckdb",)) -> InstalledWheel:
    """Validate archives, stage probes and install one exact candidate wheel."""
    directory = Path(os.environ.get("MARIVO_TEST_WHEEL_DIR", str(PROJECT_ROOT / "dist/pypi")))
    wheels = tuple(directory.glob("marivo-*.whl"))
    sdists = tuple(directory.glob("marivo-*.tar.gz"))
    assert len(wheels) == len(sdists) == 1, "Run make pypi-build pypi-check first"
    wheel, sdist = wheels[0], sdists[0]
    archives = check_archives(PROJECT_ROOT, wheel, sdist)
    assert not work.resolve().is_relative_to(PROJECT_ROOT)
    (work / "reports").mkdir()
    stage_inputs(work)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "PYTEST", "MARIVO_"))
    }
    environment.update(
        MARIVO_TELEMETRY="off",
        PYTHONNOUSERSITE="1",
        MARIVO_WHEEL_SHA256=str(archives["wheel_sha256"]),
    )
    interpreter = work / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    candidate = InstalledWheel(work, interpreter, environment, wheel, archives, extras)
    (candidate.reports / "archives.json").write_text(json.dumps(archives, sort_keys=True))
    python = os.environ.get("MARIVO_TEST_PYTHON", sys.executable)
    candidate.run("create-venv", [python, "-m", "venv", str(work / ".venv")])
    candidate.run("bootstrap-pip", [str(interpreter), "-m", "pip", "install", "--upgrade", "pip"])
    constraints = work / "constraints.txt"
    supplied_constraints = os.environ.get("MARIVO_TEST_CONSTRAINTS")
    constraint_args = ["--constraint", supplied_constraints] if supplied_constraints else []
    package = str(wheel) + ("[" + ",".join(extras) + "]" if extras else "")
    candidate.run(
        "install",
        [
            str(interpreter),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            *constraint_args,
            package,
            "pytest",
        ],
    )
    candidate.run("dependency-check", [str(interpreter), "-m", "pip", "check"])
    candidate.run("setup-dependencies", [str(interpreter), "-m", "pip", "list", "--format=json"])
    constraints.write_text(
        candidate.run("freeze", [str(interpreter), "-m", "pip", "freeze", "--exclude", "marivo"])
    )
    candidate.probe("installed-origin", "guard")
    candidate.probe("origin-hook", "install-hook")
    candidate.environment["MARIVO_INSTALLED_ORIGIN_DIR"] = str(candidate.reports / "origins")
    return candidate
