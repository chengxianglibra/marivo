"""Exercise daily and release Make routing without running the dispatched suites."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_TOOLS = ("python", "pip", "pytest", "mypy", "ruff", "lint-imports", "twine", "sphinx-build")


def _run_make(
    directory: Path,
    target: str,
    *,
    endpoint: str = "",
    format_failure: bool = False,
    tests: str = "",
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    (directory / "Makefile").write_bytes((_ROOT / "Makefile").read_bytes())
    scripts = directory / "scripts"
    scripts.mkdir()
    guard = scripts / "require-venv.sh"
    guard.write_text("#!/bin/sh\nexit 0\n")
    guard.chmod(0o755)
    bin_dir = directory / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    for name in _TOOLS:
        tool = bin_dir / name
        tool.write_text(
            f"#!{sys.executable}\n"
            "import os, shlex, sys\n"
            "from pathlib import Path\n"
            "name = Path(sys.argv[0]).name\n"
            "with Path(os.environ['MARIVO_CHECK_TEST_LOG']).open('a') as log:\n"
            "    log.write(shlex.join([name, *sys.argv[1:]]) + '\\n')\n"
            "if name == 'ruff' and sys.argv[1:3] == ['format', '--check']:\n"
            "    sys.exit(int(os.environ['MARIVO_CHECK_FORMAT_FAILURE']))\n"
        )
        tool.chmod(0o755)
    log = directory / "commands.log"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL"}
    }
    environment.update(
        MARIVO_TEST_S3_ENDPOINT=endpoint,
        MARIVO_CHECK_TEST_LOG=str(log),
        MARIVO_CHECK_FORMAT_FAILURE=str(int(format_failure)),
    )
    result = subprocess.run(
        ["make", "--no-print-directory", target, f"TESTS={tests}"],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    commands = [shlex.split(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, commands


@pytest.mark.parametrize("target", ("check", "check-agent"))
def test_daily_checks_exclude_runtime_and_release(tmp_path: Path, target: str) -> None:
    result, commands = _run_make(tmp_path, target)
    assert result.returncode == 0, result.stderr
    pytest_commands = [command for command in commands if command[0] == "pytest"]
    assert len(pytest_commands) == 1
    assert pytest_commands[0][1:] == ["-q", "--tb=short", "--maxfail=5"]
    assert "-m" not in pytest_commands[0]
    mypy_command = next(command for command in commands if command[0] == "mypy")
    assert mypy_command[1:4] == [
        "--no-pretty",
        "--no-color-output",
        "--no-warn-unused-configs",
    ]
    assert {"ruff", "lint-imports", "mypy", "sphinx-build"} <= {command[0] for command in commands}
    assert commands[0] == ["ruff", "format", "--check", "."]
    assert not {"pip", "twine"} & {command[0] for command in commands}


@pytest.mark.parametrize("target", ("check", "check-agent"))
def test_format_failure_stops_before_expensive_checks(tmp_path: Path, target: str) -> None:
    result, commands = _run_make(tmp_path, target, format_failure=True)
    assert result.returncode != 0
    assert commands == [["ruff", "format", "--check", "."]]


def test_release_requires_explicit_storage_before_any_checks(tmp_path: Path) -> None:
    result, commands = _run_make(tmp_path, "release-check")
    assert result.returncode != 0
    assert "MARIVO_TEST_S3_ENDPOINT" in result.stderr
    assert commands == []


def test_release_runs_all_suites_even_after_a_focused_daily_run(tmp_path: Path) -> None:
    result, commands = _run_make(
        tmp_path,
        "release-check",
        endpoint="http://127.0.0.1:9000",
        tests="tests/focused.py::test_one",
    )
    assert result.returncode == 0, result.stderr
    pytest_commands = [command for command in commands if command[0] == "pytest"]
    assert len(pytest_commands) == 3
    assert pytest_commands[0] == ["pytest", "-q", "--tb=short", "--maxfail=5"]
    assert pytest_commands[1] == ["pytest", "-m", "runtime"]
    assert pytest_commands[2][:5] == ["pytest", "-n", "0", "-m", "release"]
    assert "tests/focused.py::test_one" not in pytest_commands[2]
    assert ["python", "-m", "build", "--outdir", "dist/pypi"] in commands
    assert any(command[:2] == ["twine", "check"] for command in commands)


@pytest.mark.parametrize("target", ("runtime-test", "runtime-test-agent"))
def test_runtime_debugging_keeps_the_requested_test(tmp_path: Path, target: str) -> None:
    selected = "tests/focused.py::test_one"
    result, commands = _run_make(tmp_path, target, tests=selected)
    assert result.returncode == 0, result.stderr
    assert len(commands) == 1
    command = commands[0]
    assert command[0] == "pytest"
    marker_index = command.index("-m")
    assert command[marker_index + 1] == "runtime"
    assert command[-3:] == ["-n", "0", selected]
