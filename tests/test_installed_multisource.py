"""Opt-in wheel journeys against existing read-only multisource services."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = [
    pytest.mark.release,
    pytest.mark.skipif(
        os.environ.get("MARIVO_INSTALLED_MULTISOURCE_TEST") != "1",
        reason="explicit installed multisource opt-in required",
    ),
]
ROOT = Path(__file__).resolve().parents[1]
BACKENDS = ("sqlite", "postgres", "mysql", "trino", "clickhouse")


@pytest.fixture(scope="module")
def installed_runner(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str, Path], None]:
    wheels = tuple((ROOT / "dist/pypi").glob("marivo-*.whl"))
    assert len(wheels) == 1, "Run make pypi-build pypi-check first"
    wheel = wheels[0]
    work = tmp_path_factory.mktemp("multisource-wheel")
    assert not work.resolve().is_relative_to(ROOT)
    tests = work / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    for name in ("installed_wheel_probe.py", "installed_multisource_probe.py"):
        shutil.copy2(ROOT / "tests" / name, tests / name)
    helpers = tests / "multisource_environment"
    helpers.mkdir()
    (helpers / "__init__.py").write_text("")
    for name in (
        "credentials",
        "postgres_analysis",
        "mysql_analysis",
        "trino_analysis",
        "clickhouse_analysis",
    ):
        shutil.copy2(ROOT / "tests/multisource_environment" / f"{name}.py", helpers / f"{name}.py")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "PYTEST", "MARIVO_"))
    }
    env.update(
        PYTHONNOUSERSITE="1",
        MARIVO_TELEMETRY="off",
        MARIVO_WHEEL_SHA256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
    )
    reports = Path(os.environ.get("MARIVO_MULTISOURCE_EVIDENCE_DIR", str(work / "reports")))
    reports = reports / uuid4().hex
    # A unique run directory prevents serial service groups overwriting receipts.
    reports.mkdir(parents=True, exist_ok=False)
    commands: list[dict[str, object]] = []

    def run(name: str, command: list[str]) -> None:
        completed = subprocess.run(
            command, cwd=work, env=env, capture_output=True, text=True, timeout=900, check=False
        )
        (reports / f"{name}.log").write_text(completed.stdout + completed.stderr)
        commands.append(
            {"name": name, "command": command, "cwd": str(work), "exit_code": completed.returncode}
        )
        (reports / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        assert completed.returncode == 0, completed.stdout + completed.stderr

    venv = work / ".venv"
    run("venv", [sys.executable, "-m", "venv", str(venv)])
    interpreter = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
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
    shutil.copy2(constraints, reports / "constraints.txt")
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
            f"{wheel}[all]",
            "psycopg[binary]",
            "pytest",
        ],
    )

    def journey(engine: str, project: Path) -> None:
        probe = [str(interpreter), "-m", "tests.installed_multisource_probe"]

        def phase(name: str) -> None:
            run(
                engine + "-" + name,
                [*probe, name, engine, str(project), str(reports / f"{engine}-{name}.json")],
            )

        try:
            phase("prepare")
            phase("privileges")
            phase("produce")
            phase("invalidate")
            phase("invalid")
        finally:
            if (project / "prefix").exists():
                phase("remove")
        phase("cold")
        phase("offline")
        produced = json.loads((reports / f"{engine}-produce.json").read_text())
        cold = json.loads((reports / f"{engine}-cold.json").read_text())
        for receipt in cold["result"]["receipts"]:
            hit = receipt["binding_hit_statistics"]
            assert hit["statements"] == []
            assert hit["transferred_rows"] == hit["transferred_bytes"] == 0
            rollup = receipt["rollup_statistics"]
            assert rollup["primary_queries"] == rollup["transferred_rows"] == 1
            assert rollup["transferred_bytes"] > 0
            assert any(role == "primary" for role, _ in rollup["statements"])
        assert len(cold["result"]["receipts"]) == 3
        assert produced["pid"] != cold["pid"]
        assert produced["result"]["session"] == cold["result"]["session"]

    return journey


@pytest.mark.parametrize("engine", BACKENDS)
def test_installed_public_multisource_journey(
    engine: str, tmp_path: Path, installed_runner: Callable[[str, Path], None]
) -> None:
    selected = os.environ.get("MARIVO_INSTALLED_BACKENDS", ",".join(BACKENDS)).split(",")
    assert set(selected) <= set(BACKENDS), selected
    if engine not in selected:
        pytest.skip("backend not selected for this serial service group")
    installed_runner(engine, tmp_path / engine)
