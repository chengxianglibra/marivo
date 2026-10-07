"""Installed-package boundaries and representative source-free recovery."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tests.packaging.boundary_probe import public_snapshot
from tests.packaging.wheel_probe import surface_snapshot
from tests.packaging.wheel_support import InstalledWheel
from tests.support.json import Json, checked, obj, read
from tests.support.paths import PROJECT_ROOT

pytestmark = pytest.mark.release


def test_installed_dependency_isolation(installed_dependency_wheel: InstalledWheel) -> None:
    candidate = installed_dependency_wheel
    extra = candidate.extras[0] if candidate.extras else "base"
    for mode in ("dependencies", "missing") if extra != "base" else ("dependencies",):
        report = candidate.reports / (mode + ".json")
        candidate.run(
            mode,
            [
                str(candidate.interpreter),
                "-m",
                "tests.packaging.boundary_probe",
                mode,
                extra,
                str(report),
            ],
        )
        assert "origin" in read(report)


def test_installed_base_cold_read(
    installed_wheel: InstalledWheel, installed_base_wheel: InstalledWheel
) -> None:
    project = installed_wheel.work / "base-read"
    produced = installed_wheel.probe(
        "base-reader-produce", "produce", str(project), "j1", "parquet"
    )
    report = installed_base_wheel.reports / "base-read.json"
    installed_base_wheel.run(
        "base-read",
        [
            str(installed_base_wheel.interpreter),
            "-m",
            "tests.packaging.boundary_probe",
            "saved",
            str(project),
            str(report),
        ],
    )
    recovered = read(report)
    assert recovered["session"] == produced["session"] and recovered["rows"] == produced["rows"]


def test_installed_origin_surface_and_console(installed_wheel: InstalledWheel) -> None:
    candidate = installed_wheel
    expected = surface_snapshot()
    report = candidate.reports / "installed-surface.json"
    candidate.run(
        "installed-surface",
        [str(candidate.interpreter), "-m", "tests.packaging.wheel_probe", "surface", str(report)],
    )
    assert json.loads(report.read_text()) == expected
    authoring = candidate.reports / "installed-authoring.json"
    candidate.run(
        "installed-authoring",
        [
            str(candidate.interpreter),
            "-m",
            "tests.packaging.boundary_probe",
            "surface",
            "unused",
            str(authoring),
        ],
    )
    actual = read(authoring)
    actual.pop("origin")
    assert actual == json.loads(json.dumps(public_snapshot()))
    rejected_origins = candidate.reports / "rejected-origins"
    foreign_project = candidate.work / "foreign-business"
    output = candidate.run(
        "foreign-import-rejected",
        [
            str(candidate.interpreter),
            "-m",
            "tests.packaging.wheel_probe",
            "produce",
            str(foreign_project),
            "j1",
            "parquet",
            str(candidate.reports / "invalid-origin.json"),
        ],
        reject=True,
        extra_env={
            "PYTHONPATH": str(PROJECT_ROOT),
            "MARIVO_INSTALLED_ORIGIN_DIR": str(rejected_origins),
        },
    )
    assert "foreign Marivo import" in output
    assert not foreign_project.exists(), "foreign product code reached business execution"
    rejection_reports = tuple(rejected_origins.glob("*.json"))
    assert rejection_reports
    for path in rejection_reports:
        assert obj(read(path))["error"] == "foreign Marivo import"
    console = candidate.interpreter.parent / ("marivo.exe" if os.name == "nt" else "marivo")
    assert candidate.run("module-help", [str(candidate.interpreter), "-m", "marivo", "help"]) == (
        candidate.run("console-help", [str(console), "help"])
    )
    project = candidate.work / "bootstrap"
    candidate.run("console-init", [str(console), "init", "--project-root", str(project)])
    assert (project / "marivo.toml").is_file() and (project / "models").is_dir()
    assert (project / ".marivo").is_dir()
    for agent in (".agents", ".claude", ".codex"):
        for skill in ("marivo-semantic", "marivo-analysis"):
            link = project / agent / "skills" / skill
            assert link.is_symlink() and link.resolve().is_relative_to(candidate.work / ".venv")
            assert (link / "SKILL.md").read_bytes() == (
                PROJECT_ROOT / "marivo/skills" / skill / "SKILL.md"
            ).read_bytes()


def test_installed_fixed_grid_display(installed_wheel: InstalledWheel) -> None:
    candidate = installed_wheel
    reports = []
    for phase in ("produce", "fixed", "cold"):
        report = candidate.reports / ("display-" + phase + ".json")
        candidate.run(
            "display-" + phase,
            [
                str(candidate.interpreter),
                "-m",
                "tests.packaging.display_journey",
                phase,
                str(candidate.work / "display"),
                str(report),
            ],
        )
        reports.append(read(report))
    assert len({item["pid"] for item in reports}) == 3
    for field in ("session", "rows", "display"):
        assert all(item[field] == reports[0][field] for item in reports)
    assert reports[1]["continuation"] == reports[2]["continuation"]
    assert reports[1]["run_count"] == reports[2]["run_count"]


def test_installed_origin_rejects_wrong_archive_hash(installed_wheel: InstalledWheel) -> None:
    candidate = installed_wheel
    origin = candidate.probe("hash-origin", "guard")
    package = origin["package"]
    assert isinstance(package, str)
    paths = tuple(Path(package).parent.glob("marivo-*.dist-info/direct_url.json"))
    assert len(paths) == 1
    path = paths[0]
    original = path.read_bytes()
    try:
        for field in ("hash", "hashes"):
            payload = obj(checked(json.loads(original)))
            obj(payload["archive_info"])[field] = (
                "sha256=" + "0" * 64 if field == "hash" else {"sha256": "0" * 64}
            )
            path.write_text(json.dumps(payload))
            output = candidate.run(
                "wrong-" + field,
                [
                    str(candidate.interpreter),
                    "-m",
                    "tests.packaging.wheel_probe",
                    "guard",
                    str(candidate.reports / "invalid-hash.json"),
                ],
                reject=True,
                extra_env={
                    "MARIVO_INSTALLED_ORIGIN_DIR": str(
                        candidate.reports / ("wrong-" + field + "-origins")
                    )
                },
            )
            assert "candidate archive hash mismatch" in output
    finally:
        path.write_bytes(original)
    candidate.probe("restored-hash-origin", "guard")


@pytest.mark.parametrize("scenario", ("j1", "j2", "j3", "j4", "a02", "a07", "a08"))
def test_installed_relations_offline_and_cold(
    installed_wheel: InstalledWheel, scenario: str
) -> None:
    candidate = installed_wheel
    project = candidate.work / ("relations-" + scenario)
    phases = [
        candidate.probe(scenario + "-" + phase, phase, str(project), scenario, "parquet")
        for phase in ("produce", "continue", "recover")
    ]
    assert len({item["pid"] for item in phases}) == 3
    fields = (
        ("session", "snapshots", "oracle", "facts_sha256", "edge_checks", "shared_nodes")
        if scenario.startswith("a")
        else ("session", "artifact", "run", "rows", "contract", "descriptor", "facts_sha256")
    )
    for key in fields:
        assert all(item[key] == phases[0][key] for item in phases)
    assert phases[1]["continuations"] == phases[2]["continuations"]
    source_count = phases[0]["run_count"]
    fixed_count = phases[1]["run_count"]
    cold_count = phases[2]["run_count"]
    assert isinstance(source_count, int)
    assert isinstance(fixed_count, int)
    assert isinstance(cold_count, int)
    assert fixed_count > source_count
    assert fixed_count == cold_count


@pytest.mark.parametrize("family", ("statistics", "funnel", "history"))
def test_installed_result_families_offline_and_cold(
    installed_wheel: InstalledWheel, family: str
) -> None:
    candidate = installed_wheel
    root = candidate.work / ("result-" + family)
    if family != "statistics":
        root.mkdir()
    reports: list[dict[str, Json]] = []
    modules = {
        "statistics": "tests.analysis.statistics.recovery_worker",
        "funnel": "tests.analysis.journey.funnel_public_recovery_worker",
        "history": "tests.analysis.lifecycle.history_public_recovery_worker",
    }
    for phase in ("produce", "fixed", "cold"):
        report = candidate.reports / (family + "-" + phase + ".json")
        arguments = (
            [str(root), "views", phase, str(report)]
            if family == "history"
            else [str(root), phase, "parquet", str(report)]
            if family == "statistics"
            else [str(root), phase, str(report), "parquet"]
        )
        candidate.run(
            family + "-" + phase,
            [str(candidate.interpreter), "-m", modules[family], *arguments],
        )
        reports.append(read(report))
        if phase == "produce" and family != "statistics":
            shutil.rmtree(root / "models")
            for path in (*root.glob("source.duckdb*"), *root.glob("*.parquet")):
                path.unlink()
    assert len({item["pid"] for item in reports}) == 3
    if family != "statistics":
        assert reports[1]["outputs"] == reports[2]["outputs"]
        assert reports[2]["kernels"] == reports[2]["new_runs"] == 0
    for path in (candidate.reports / "origins").glob("*.json"):
        assert "origin" in obj(read(path)), path.read_text()
