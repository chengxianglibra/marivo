"""Prepare one isolated installed candidate for package-boundary checks."""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
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
    "tests.packaging.wheel_probe",
    "tests.packaging.source_probe",
    "tests.analysis.statistics.recovery_worker",
    "tests.analysis.journey.funnel_public_recovery_worker",
    "tests.analysis.lifecycle.history_public_recovery_worker",
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


@dataclass
class InstalledWheel:
    """An isolated noneditable install shared by installed-package checks."""

    work: Path
    interpreter: Path
    environment: dict[str, str]
    wheel: Path
    archives: dict[str, str | int]
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


def prepare_wheel(work: Path) -> InstalledWheel:
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
    candidate = InstalledWheel(work, interpreter, environment, wheel, archives)
    (candidate.reports / "archives.json").write_text(json.dumps(archives, sort_keys=True))
    candidate.run("create-venv", [sys.executable, "-m", "venv", str(work / ".venv")])
    constraints = work / "constraints.txt"
    constraints.write_text(
        "\n".join(
            sorted(
                f"{item.metadata['Name']}=={item.version}"
                for item in importlib.metadata.distributions()
                if item.metadata["Name"].lower() != "marivo"
            )
        )
        + "\n"
    )
    candidate.run(
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
        ],
    )
    candidate.run("dependency-check", [str(interpreter), "-m", "pip", "check"])
    candidate.run("dependencies", [str(interpreter), "-m", "pip", "list", "--format=json"])
    candidate.probe("installed-origin", "guard")
    candidate.probe("origin-hook", "install-hook")
    candidate.environment["MARIVO_INSTALLED_ORIGIN_DIR"] = str(candidate.reports / "origins")
    return candidate
