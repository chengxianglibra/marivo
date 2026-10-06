"""Installed-package boundaries and representative source-free recovery."""

from __future__ import annotations

import json
import os
import shutil

import pytest

from tests.packaging.wheel_probe import surface_snapshot
from tests.packaging.wheel_support import InstalledWheel
from tests.support.json import Json, obj, read
from tests.support.paths import PROJECT_ROOT

pytestmark = pytest.mark.release


def test_installed_origin_surface_and_console(installed_wheel: InstalledWheel) -> None:
    candidate = installed_wheel
    expected = surface_snapshot()
    report = candidate.reports / "installed-surface.json"
    candidate.run(
        "installed-surface",
        [str(candidate.interpreter), "-m", "tests.packaging.wheel_probe", "surface", str(report)],
    )
    assert json.loads(report.read_text()) == expected
    rejected_origins = candidate.reports / "rejected-origins"
    output = candidate.run(
        "foreign-import-rejected",
        [
            str(candidate.interpreter),
            "-m",
            "tests.packaging.wheel_probe",
            "guard",
            str(candidate.reports / "invalid-origin.json"),
        ],
        reject=True,
        extra_env={
            "PYTHONPATH": str(PROJECT_ROOT),
            "MARIVO_INSTALLED_ORIGIN_DIR": str(rejected_origins),
        },
    )
    assert "foreign Marivo import" in output
    rejection_reports = tuple(rejected_origins.glob("*.json"))
    assert rejection_reports
    for path in rejection_reports:
        assert obj(read(path))["error"] == "foreign Marivo import"
    console = candidate.interpreter.parent / ("marivo.exe" if os.name == "nt" else "marivo")
    assert candidate.run("module-help", [str(candidate.interpreter), "-m", "marivo", "help"]) == (
        candidate.run("console-help", [str(console), "help"])
    )


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
