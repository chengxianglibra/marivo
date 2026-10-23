"""Exercise daily and release Make routing without running the dispatched suites."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from tests.support.paths import PROJECT_ROOT

_ROOT = PROJECT_ROOT
_TOOLS = ("python", "pip", "pytest", "mypy", "ruff", "lint-imports", "twine", "sphinx-build")


def _run_make(
    directory: Path,
    target: str,
    *,
    format_failure: bool = False,
    tests: str = "",
    pytest_workers: int | None = None,
    runtime_workers: int | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    directory.mkdir(parents=True, exist_ok=True)
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
        MARIVO_CHECK_TEST_LOG=str(log),
        MARIVO_CHECK_FORMAT_FAILURE=str(int(format_failure)),
    )
    arguments = ["make", "--no-print-directory", target, f"TESTS={tests}"]
    if pytest_workers is not None:
        arguments.append(f"PYTEST_WORKERS={pytest_workers}")
    if runtime_workers is not None:
        arguments.append(f"RUNTIME_WORKERS={runtime_workers}")
    result = subprocess.run(
        arguments,
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    commands = [shlex.split(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, commands


def test_daily_checks_exclude_runtime_and_release(tmp_path: Path) -> None:
    for target in ("check", "check-agent"):
        result, commands = _run_make(tmp_path / target, target)
        assert result.returncode == 0, result.stderr
        pytest_commands = [command for command in commands if command[0] == "pytest"]
        assert len(pytest_commands) == 1
        assert pytest_commands[0][1:] == ["-q", "--tb=short", "--maxfail=5", "-n", "12"]
        assert "-m" not in pytest_commands[0]
        mypy_command = next(command for command in commands if command[0] == "mypy")
        assert mypy_command[1:4] == [
            "--no-pretty",
            "--no-color-output",
            "--no-warn-unused-configs",
        ]
        assert {"ruff", "lint-imports", "mypy", "sphinx-build"} <= {
            command[0] for command in commands
        }
        assert commands[0] == ["ruff", "format", "--check", "."]
        assert not {"pip", "twine"} & {command[0] for command in commands}


def test_format_failure_stops_before_expensive_checks(tmp_path: Path) -> None:
    for target in ("check", "check-agent"):
        result, commands = _run_make(tmp_path / target, target, format_failure=True)
        assert result.returncode != 0
        assert commands == [["ruff", "format", "--check", "."]]


def test_release_runs_all_suites_even_after_a_focused_daily_run(tmp_path: Path) -> None:
    result, commands = _run_make(
        tmp_path,
        "release-check",
        tests="tests/focused.py::test_one",
    )
    assert result.returncode == 0, result.stderr
    pytest_commands = [command for command in commands if command[0] == "pytest"]
    assert len(pytest_commands) == 3
    assert pytest_commands[0] == ["pytest", "-q", "--tb=short", "--maxfail=5", "-n", "12"]
    assert pytest_commands[1] == ["pytest", "-m", "runtime", "-n", "2"]
    assert pytest_commands[2] == [
        "pytest",
        "-n",
        "0",
        "-m",
        "release",
        "tests/packaging/test_installer.py",
        "tests/packaging/test_installer_uv.py",
        "tests/packaging/test_wheel.py",
        "tests/packaging/test_installed_journeys.py",
        "tests/surface/test_disclosure_documentation.py",
        "tests/semantic/test_semantic_live_help.py",
        "tests/surface/test_analysis_disclosure_content.py",
        "tests/analysis/materialization/test_frame_storage.py",
        "tests/analysis/materialization/test_frame_attribute_contribution.py",
        "tests/analysis/materialization/test_terminal_publication.py",
        "tests/surface/test_analysis_typing.py",
        "tests/analysis/materialization/test_relation_recovery.py",
        "tests/analysis/statistics/test_analysis_deviation_domains.py",
        "tests/analysis/statistics/test_deviation_contributions.py",
        "tests/analysis/materialization/test_versioned_recovery.py",
        "tests/analysis/statistics/test_analysis_deviation_display.py",
        "tests/analysis/statistics/test_analysis_runs.py",
        "tests/analysis/statistics/test_analysis_deviation.py",
        "tests/analysis/numeric/test_analysis_attribution_runtime.py",
        "tests/analysis/graph/test_distribution_carriers.py",
        "tests/analysis/temporal/test_analysis_temporal.py",
        "tests/analysis/materialization/test_reference_recovery.py",
        "tests/analysis/graph/test_capability_consumers.py",
        "tests/analysis/graph/test_computation_rollup_runtime.py",
        "tests/analysis/graph/test_count_rollup_backends.py",
        "tests/analysis/graph/test_observation_grain.py",
        "tests/analysis/graph/test_premise_verification.py",
        "tests/analysis/graph/test_public_composition.py",
        "tests/analysis/graph/test_relationship_binding.py",
        "tests/analysis/graph/test_runtime_concurrency.py",
        "tests/analysis/materialization/test_attribute_contribution_oracles.py",
        "tests/analysis/materialization/test_attribute_inputs.py",
        "tests/analysis/materialization/test_attribute_temporal_inputs.py",
        "tests/analysis/materialization/test_comparison_recovery.py",
        "tests/analysis/materialization/test_distribution_recovery.py",
        "tests/analysis/materialization/test_frame_attribute.py",
        "tests/analysis/materialization/test_frame_attribute_journeys.py",
        "tests/analysis/materialization/test_frame_temporal_expressions.py",
        "tests/analysis/materialization/test_mean_recovery.py",
        "tests/analysis/materialization/test_multiroot_recovery.py",
        "tests/analysis/materialization/test_named_calendar_windows.py",
        "tests/analysis/materialization/test_numeric_recovery.py",
        "tests/analysis/materialization/test_round1_api_fixes.py",
        "tests/analysis/numeric/test_analysis_cohort.py",
        "tests/analysis/numeric/test_analysis_comparison_runtime.py",
        "tests/analysis/numeric/test_analysis_display.py",
        "tests/analysis/numeric/test_analysis_members.py",
        "tests/analysis/numeric/test_analysis_numeric.py",
        "tests/analysis/numeric/test_analysis_predicates.py",
        "tests/analysis/numeric/test_analysis_references.py",
        "tests/analysis/numeric/test_members_consumers.py",
        "tests/analysis/statistics/test_analysis_statistics_faults.py",
        "tests/analysis/temporal/test_temporal_recovery.py",
        "tests/analysis/temporal/test_timezone_observation.py",
    ]
    assert ["python", "-m", "build", "--outdir", "dist/pypi"] in commands
    assert any(command[:2] == ["twine", "check"] for command in commands)


def test_default_test_runs_slow_cases_without_marker_narrowing(tmp_path: Path) -> None:
    selected = (
        "tests/analysis/graph/test_capability_consumers.py::"
        "test_source_coordinates_statistics_and_original_state"
    )
    result, commands = _run_make(tmp_path, "test", tests=selected)
    assert result.returncode == 0, result.stderr
    assert commands == [["pytest", "-q", "--tb=short", "--maxfail=5", "-n", "0", selected]]


def test_default_test_workers_are_overridable(tmp_path: Path) -> None:
    for workers in (None, 8):
        result, commands = _run_make(
            tmp_path / str(workers),
            "test",
            tests="tests/focused.py",
            pytest_workers=workers,
        )
        assert result.returncode == 0, result.stderr
        assert commands == [
            [
                "pytest",
                "-q",
                "--tb=short",
                "--maxfail=5",
                "-n",
                str(12 if workers is None else workers),
                "tests/focused.py",
            ]
        ]


def test_default_pytest_config_includes_slow_and_excludes_external_suites() -> None:
    configuration = (_ROOT / "pytest.ini").read_text()
    assert 'addopts = -n auto --dist=worksteal -m "not release and not runtime"' in configuration
    assert "not slow" not in configuration


def test_runtime_debugging_keeps_the_requested_test(tmp_path: Path) -> None:
    selected = "tests/focused.py::test_one"
    for target in ("runtime-test", "runtime-test-agent"):
        result, commands = _run_make(tmp_path / target, target, tests=selected)
        assert result.returncode == 0, result.stderr
        assert len(commands) == 1
        command = commands[0]
        assert command[0] == "pytest"
        marker_index = command.index("-m")
        assert command[marker_index + 1] == "runtime"
        assert command[-3:] == ["-n", "0", selected]


def test_runtime_workers_are_bounded_and_overridable(
    tmp_path: Path,
) -> None:
    for target in ("runtime-test", "runtime-test-agent"):
        for workers in (None, 4):
            result, commands = _run_make(
                tmp_path / target / str(workers),
                target,
                tests="tests/focused.py",
                runtime_workers=workers,
            )
            assert result.returncode == 0, result.stderr
            assert len(commands) == 1
            assert commands[0][-3:] == [
                "-n",
                str(2 if workers is None else workers),
                "tests/focused.py",
            ]
